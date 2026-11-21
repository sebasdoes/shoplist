"""T013 health test + T016 (written FIRST, TDD) — API contract cases per contracts/api.md."""

import uuid

DEFAULT_LIST = "00000000-0000-4000-8000-000000000001"


def push(client, headers, mutations):
    return client.post("/api/v1/sync/push", json={"mutations": mutations}, headers=headers)


def item_mut(**overrides):
    fields = {
        "id": str(uuid.uuid4()),
        "name": "koffie",
        "tombstone": False,
        "list_id": DEFAULT_LIST,
    }
    fields.update(overrides)
    return {"op": "upsert_item", "item": fields}


# --- T013 ---------------------------------------------------------------


def test_health_unauthenticated(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# --- T016 contract cases -------------------------------------------------


def test_push_happy_path_and_applied_shape(client, auth):
    headers = auth()
    item_id = str(uuid.uuid4())
    resp = push(
        client,
        headers,
        [item_mut(id=item_id, name="koffie", quantity="2x", note="the small ones", staple=True)],
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"applied", "trip"}
    applied = body["applied"]
    assert set(applied.keys()) == {"items", "lists"}
    assert applied["lists"] == []
    assert len(applied["items"]) == 1
    entry = applied["items"][0]
    assert entry["id"] == item_id
    assert isinstance(entry["rev"], int)
    assert isinstance(entry["updated_at"], str)
    assert entry["merged_into"] is None
    assert body["trip"] is None


def test_pull_response_shape(client, auth):
    headers = auth()
    push(client, headers, [item_mut(name="melk", quantity="1lx")])
    resp = client.post(
        "/api/v1/sync/pull", json={"watermark": 0, "include_history": True}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert {"watermark", "items", "history", "full_snapshot"} <= set(body.keys())
    item_fields = {
        "id",
        "list_id",
        "name",
        "quantity",
        "note",
        "status",
        "staple",
        "tombstone",
        "created_by",
        "rev",
    }
    for row in body["items"]:
        assert set(row.keys()) == item_fields
    assert body["items"][0]["name"] == "melk"
    assert body["items"][0]["status"] == "active"
    assert body["items"][0]["tombstone"] is False or body["items"][0]["tombstone"] == 0


def test_push_merged_into_response(client, auth):
    headers = auth()
    survivor = str(uuid.uuid4())
    push(client, headers, [item_mut(id=survivor, name="koffie")])
    dupe = str(uuid.uuid4())
    resp = push(client, headers, [item_mut(id=dupe, name="KOFFIE")])
    assert resp.status_code == 200
    assert resp.json()["applied"]["items"][0]["merged_into"] == survivor


def test_401_without_token(client):
    for method, path, body in [
        ("post", "/api/v1/sync/push", {"mutations": []}),
        ("post", "/api/v1/sync/pull", {"watermark": 0}),
        ("get", "/api/v1/history", None),
        ("delete", "/api/v1/history/whatever", None),
    ]:
        resp = (
            getattr(client, method)(path, json=body)
            if body is not None
            else getattr(client, method)(path)
        )
        assert resp.status_code == 401, (path, resp.status_code)


def test_401_with_bad_token(client, auth):
    resp = push(client, {"Authorization": "Bearer faketoken.faketoken.faketoken"}, [])
    assert resp.status_code == 401


def test_422_empty_name(client, auth):
    headers = auth()
    for bad in ["", "   "]:
        resp = push(client, headers, [item_mut(name=bad)])
        assert resp.status_code == 422, bad
        detail = resp.json()["detail"]
        assert isinstance(detail, str)


def test_422_name_over_200_chars(client, auth):
    resp = push(client, auth(), [item_mut(name="x" * 201)])
    assert resp.status_code == 422


def test_422_quantity_over_50_chars(client, auth):
    resp = push(client, auth(), [item_mut(quantity="q" * 51)])
    assert resp.status_code == 422


def test_422_note_over_200_chars(client, auth):
    resp = push(client, auth(), [item_mut(note="n" * 201)])
    assert resp.status_code == 422


def test_422_unknown_op(client, auth):
    resp = push(client, auth(), [{"op": "teleport", "item": {"id": str(uuid.uuid4())}}])
    assert resp.status_code == 422


def test_error_envelope_shape(client, auth):
    """errors: {"detail": "..."} with 400/401/404/409/422."""
    resp = push(client, auth(), [item_mut(name="")])
    assert resp.status_code == 422
    assert "detail" in resp.json()


def test_push_batch_applies_in_order(client, auth):
    headers = auth()
    a = str(uuid.uuid4())
    resp = push(client, headers, [item_mut(id=a, name="boter"), item_mut(id=a, name="margarine")])
    assert resp.status_code == 200
    applied = resp.json()["applied"]
    assert len(applied["items"]) == 2
    data = client.post("/api/v1/sync/pull", json={"watermark": 0}, headers=headers).json()
    rows = [x for x in data["items"] if x["id"] == a]
    assert len(rows) == 1
    assert rows[0]["name"] == "margarine"


# --- T005 contract tests (specs/002 contracts/api.md) ----------------------


LIST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_pull_watermark0_snapshot_includes_all_lists(client, auth):
    """watermark-0 snapshot includes ALL lists — tombstoned ones too."""
    headers = auth()
    r = push(client, headers, [{"op": "create_list", "list": {"id": LIST_A, "name": "A"}}])
    assert r.status_code == 200
    r = push(client, headers, [{"op": "delete_list", "list_id": LIST_A}])
    assert r.status_code == 200

    body = client.post(
        "/api/v1/sync/pull", json={"watermark": 0, "include_history": False}, headers=headers
    ).json()
    assert "lists" in body
    lists = {x["id"]: x for x in body["lists"]}
    assert set(lists) == {"00000000-0000-4000-8000-000000000001", LIST_A}
    for entry in lists.values():
        assert set(entry.keys()) == {"id", "name", "tombstone", "rev"}
    assert lists["00000000-0000-4000-8000-000000000001"]["name"] == "Boodschappen"
    assert lists["00000000-0000-4000-8000-000000000001"]["tombstone"] is False
    assert lists[LIST_A]["tombstone"] is True  # tombstoned lists ship too


def test_pull_delta_lists_via_watermark(client, auth):
    """A list applied after the watermark arrives in the delta pull."""
    headers = auth()
    first_wm = client.post(
        "/api/v1/sync/pull", json={"watermark": 0, "include_history": False}, headers=headers
    ).json()["watermark"]
    push(client, headers, [{"op": "create_list", "list": {"id": LIST_A, "name": "A"}}])
    body = client.post(
        "/api/v1/sync/pull", json={"watermark": first_wm, "include_history": False}, headers=headers
    ).json()
    assert [x["id"] for x in body["lists"]] == [LIST_A]
    assert body["lists"][0]["name"] == "A"
    assert body["lists"][0]["tombstone"] is False


def test_push_response_applied_lists(client, auth):
    """applied gains a lists section: [{id, name, rev, tombstone}]."""
    headers = auth()
    r = push(
        client, headers, [{"op": "create_list", "list": {"id": LIST_A, "name": "Winkelpad A"}}]
    )
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert set(applied.keys()) == {"items", "lists"}
    assert applied["items"] == []
    entry = applied["lists"][0]
    assert set(entry.keys()) == {"id", "name", "rev", "tombstone"}
    assert entry == {
        "id": LIST_A,
        "name": "Winkelpad A",
        "rev": entry["rev"],
        "tombstone": False,
    }

    r = push(client, headers, [{"op": "delete_list", "list_id": LIST_A}])
    assert r.status_code == 200
    entry = r.json()["applied"]["lists"][0]
    assert entry["id"] == LIST_A
    assert entry["tombstone"] is True
    assert entry["rev"] > 0


def test_push_mixed_batch_applied_sections(client, auth):
    """items and lists sections fill independently in one batch."""
    headers = auth()
    item_id = str(uuid.uuid4())
    r = push(
        client,
        headers,
        [
            item_mut(id=item_id, name="koffie"),
            {"op": "create_list", "list": {"id": LIST_A, "name": "A"}},
        ],
    )
    assert r.status_code == 200
    applied = r.json()["applied"]
    assert [a["id"] for a in applied["items"]] == [item_id]
    assert [a["id"] for a in applied["lists"]] == [LIST_A]
    assert applied["lists"][0]["name"] == "A"
    assert applied["lists"][0]["tombstone"] is False
