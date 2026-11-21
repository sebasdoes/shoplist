"""T015 (written FIRST, TDD) — sync engine behaviors via the push/pull API.

Per-field LWW (research D2), auto-dedupe on name_key (research D6), tombstone
precedence, out-of-order push safety, pull watermark + full-snapshot flag (>500
rows, data-model rev), server-stamped timestamps.
"""

import uuid

DEFAULT_LIST = "00000000-0000-4000-8000-000000000001"


def push(client, headers, mutations):
    return client.post("/api/v1/sync/push", json={"mutations": mutations}, headers=headers)


def pull(client, headers, watermark, include_history=True):
    return client.post(
        "/api/v1/sync/pull",
        json={"watermark": watermark, "include_history": include_history},
        headers=headers,
    )


def item_mut(**overrides):
    fields = {
        "id": str(uuid.uuid4()),
        "name": "koffie",
        "tombstone": False,
        "list_id": DEFAULT_LIST,
    }
    fields.update(overrides)
    return {"op": "upsert_item", "item": fields}


def active_items(client, headers, watermark):
    resp = pull(client, headers, watermark, include_history=False)
    assert resp.status_code == 200, resp.text
    return resp.json()


def find_by_id(rows, item_id):
    return next(r for r in rows if r["id"] == item_id)


def test_quantity_edit_and_note_edit_both_survive(client, auth):
    """research D2: per-field LWW — a per-field edit does not clobber the other field."""
    headers = auth()
    item_id = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=item_id, name="koffie", quantity="1x")])
    assert r.status_code == 200
    assert r.json()["applied"]["items"][0]["merged_into"] is None

    # Device B only edits the note; device A only edits the quantity, afterwards.
    note_edit = {"id": item_id, "list_id": DEFAULT_LIST, "note": "the small ones"}
    r = push(client, auth(sub="user-b"), [{"op": "upsert_item", "item": note_edit}])
    assert r.status_code == 200
    r = push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": item_id, "list_id": DEFAULT_LIST, "quantity": "2x"}}],
    )
    assert r.status_code == 200

    data = active_items(client, headers, 0)
    row = find_by_id(data["items"], item_id)
    assert row["quantity"] == "2x"
    assert row["note"] == "the small ones"


def test_same_field_lww_last_write_wins(client, auth):
    headers = auth()
    a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=a, name="melk", quantity="1x")])
    headers_b = auth(sub="user-b")
    push(
        client,
        headers_b,
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "quantity": "9x"}}],
    )
    push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "quantity": "3x"}}],
    )
    row = find_by_id(active_items(client, headers, 0)["items"], a)
    assert row["quantity"] == "3x"


def test_duplicates_allowed_normalized_name_key(client, auth):
    """Constitution v1.3.0: auto-dedupe REMOVED — two same-normalized names on
    one list are independent rows (name_key kept for grouping, no unique index).
    casefold + trim + collapse whitespace + NFKD diacritic strip stays for
    keying, but no longer folds rows."""
    headers = auth()
    first = str(uuid.uuid4())
    r = push(
        client, headers, [item_mut(id=first, name="Café au Lait", quantity="2x", note="morgen")]
    )
    assert r.status_code == 200
    assert r.json()["applied"]["items"][0]["merged_into"] is None

    second = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=second, name="cafe   au lait", note="avond")])
    assert r.status_code == 200
    applied = r.json()["applied"]["items"]
    assert len(applied) == 1  # exactly the pushed row — no survivor echo
    assert applied[0]["id"] == second
    assert applied[0]["merged_into"] is None

    data = active_items(client, headers, 0)
    actives = [x for x in data["items"] if x["tombstone"] == 0]
    by_id = {x["id"]: x for x in actives}
    assert set(by_id) == {first, second}  # both rows live, both in pull
    assert by_id[first]["name"] == "Café au Lait"
    assert by_id[second]["name"] == "cafe   au lait"  # each keeps its display form


def test_tombstone_wins_over_same_epoch_edit(client, auth):
    headers = auth()
    a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=a, name="brood", staple=True, quantity="1")])
    # delete (tombstone) then a concurrent edit — the tombstone must win.
    push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "tombstone": True}}],
    )
    push(
        client,
        auth(sub="user-b"),
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "note": "later edit"}}],
    )

    data = active_items(client, headers, 0)
    row = find_by_id(data["items"], a)
    assert row["tombstone"] == 1
    # staple flag survives deletion (data-model items.staple note)
    assert row["staple"] == 1


def test_edit_then_late_tombstone_loses_edit(client, auth):
    """Reverse order: edit arrives first, tombstone after — the delete still wins (LWW)."""
    headers = auth()
    a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=a, name="thee")])
    late_edit = {"id": a, "list_id": DEFAULT_LIST, "note": "earlier edit"}
    push(client, headers, [{"op": "upsert_item", "item": late_edit}])
    push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "tombstone": True}}],
    )
    row = find_by_id(active_items(client, headers, 0)["items"], a)
    assert row["tombstone"] == 1
    assert row["note"] == "earlier edit"  # tombstone does not erase data, it wins on liveness


def test_out_of_order_push_safety(client, auth):
    """Independent pushes in any order all land; batch applies in order."""
    headers = auth()
    ids = [str(uuid.uuid4()) for _ in range(3)]
    r = push(
        client,
        headers,
        [item_mut(id=i, name=f"item-{n}") for n, i in reversed(list(enumerate(ids)))],
    )
    assert r.status_code == 200
    assert [a["id"] for a in r.json()["applied"]["items"]] == list(reversed(ids))
    rows = active_items(client, headers, 0)["items"]
    assert sorted(x["name"] for x in rows) == ["item-0", "item-1", "item-2"]


def test_pull_watermark_incremental(client, auth):
    headers = auth()
    a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=a, name="rijst")])
    first = pull(client, headers, 0)
    assert first.status_code == 200
    body = first.json()
    assert body["full_snapshot"] is False
    watermark = body["watermark"]
    assert watermark >= 1
    assert [x["id"] for x in body["items"]] == [a]

    b = str(uuid.uuid4())
    push(client, headers, [item_mut(id=b, name="pasta")])
    second = pull(client, headers, watermark).json()
    assert [x["id"] for x in second["items"]] == [b]
    assert second["watermark"] > watermark


def test_pull_full_snapshot_flag_over_500(client, auth):
    """data-model rev: delta > 500 rows → full snapshot flag."""
    headers = auth()
    batch = [item_mut(name=f"bulk-{i}") for i in range(510)]
    r = push(client, headers, batch)
    assert r.status_code == 200
    watermark = r.json()["applied"]["items"][0]["rev"] - 1  # watermark below the bulk push

    resp = pull(client, headers, watermark, include_history=False)
    body = resp.json()
    assert resp.status_code == 200
    assert body["full_snapshot"] is True
    assert len(body["items"]) == 510
    assert body["watermark"] >= max(a["rev"] for a in r.json()["applied"]["items"])


def test_server_stamped_timestamps(client, auth):
    """Client clock ignored — timestamps come back server-stamped ISO-8601 UTC."""
    from datetime import datetime

    headers = auth()
    a = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=a, name="kaas")])
    applied = r.json()["applied"]["items"][0]
    assert applied["id"] == a
    assert applied["rev"] >= 1
    stamped = datetime.fromisoformat(applied["updated_at"])
    assert stamped.tzinfo is not None
    # "2026-…+00:00" — UTC
    assert stamped.utcoffset().total_seconds() == 0

    data = pull(client, headers, 0, include_history=True).json()
    assert isinstance(data["history"], list)


def test_tombstone_propagates_to_offline_devices(client, auth):
    """data-model items.tombstone: 1 = deleted, propagates to offline devices via pull."""
    headers = auth()
    offline_headers = auth(sub="user-b")
    a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=a, name="wasmiddel")])
    watermark = pull(client, offline_headers, 0, include_history=False).json()["watermark"]

    push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": a, "list_id": DEFAULT_LIST, "tombstone": True}}],
    )
    delta = pull(client, offline_headers, watermark, include_history=False).json()
    row = find_by_id(delta["items"], a)
    assert row["tombstone"] == 1


# --- name contract + rejected[] detail sanitization (hardening review fixes) ---


def test_push_new_item_explicit_null_name_rejected(client, auth):
    """'name' is required (trimmed 1–200): pushing {"name": None} on a NEW item
    must be REPORTED in rejected[], not applied as an empty-named row."""
    headers = auth()
    item_id = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=item_id, name=None)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"]["items"] == []
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["index"] == 0
    assert body["rejected"][0]["detail"] == "name required after trim, 1–200 chars"

    # nothing was inserted
    data = active_items(client, headers, 0)
    assert all(row["id"] != item_id for row in data["items"])


def test_push_existing_item_explicit_null_name_rejected(client, auth):
    """An explicit null name on an EXISTING row is a SyncError too (reported),
    and the row keeps its previous name."""
    headers = auth()
    item_id = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_id, name="karnemelk")])

    r = push(
        client,
        headers,
        [{"op": "upsert_item", "item": {"id": item_id, "list_id": DEFAULT_LIST, "name": None}}],
    )
    body = r.json()
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["detail"] == "name required after trim, 1–200 chars"

    data = active_items(client, headers, 0)
    assert find_by_id(data["items"], item_id)["name"] == "karnemelk"


def test_push_new_tombstone_without_name_still_applies(client, auth):
    """Regression guard: tombstone-only inserts (no name on the wire) stay legal."""
    headers = auth()
    item_id = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=item_id, name=None, tombstone=True)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rejected"]) == 0
    assert body["applied"]["items"], "tombstone insert must still echo applied"


def test_rejected_internal_exception_is_sanitized(client, auth, monkeypatch):
    """Internal exception text (e.g. sqlite constraint repr) must NEVER reach
    rejected[].detail — clients get a generic pointer to the server logs."""
    from app import sync as sync_mod

    def boom(conn, item, user):
        raise RuntimeError("NOT NULL constraint failed: items.name")

    monkeypatch.setattr(sync_mod, "upsert_apply", boom)
    r = push(client, auth(), [item_mut(name="ietsies")])
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["detail"] == "internal error — see server logs"
    assert "NOT NULL" not in body["rejected"][0]["detail"]


def test_rejected_syncerror_detail_preserved(client, auth):
    """SyncError details (client-actionable) are passed through unchanged."""
    headers = auth()
    dead = "deadbeef-0000-4000-8000-00000000dead"
    r = push(client, headers, [item_mut(name="poison", list_id=dead)])
    body = r.json()
    assert body["rejected"][0]["detail"] == f"unknown list: {dead}"
