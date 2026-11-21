"""T023 (written FIRST, TDD) — trip application semantics per data-model + contracts/api.md."""

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


def trip_mut(decisions, trip_id=None, list_id=DEFAULT_LIST):
    return {
        "op": "trip",
        "trip": {"id": trip_id or str(uuid.uuid4()), "list_id": list_id, "decisions": decisions},
    }


def seed_items(client, headers, *specs):
    """specs: (name, quantity, note, staple). Returns list of ids."""
    ids = [str(uuid.uuid4()) for _ in specs]
    mutations = [
        item_mut(id=i, name=n, quantity=q, note=note, staple=s)
        for i, (n, q, note, s) in zip(ids, specs)
    ]
    r = push(client, headers, mutations)
    assert r.status_code == 200, r.text
    return ids


def pull_all(client, headers):
    return client.post("/api/v1/sync/pull", json={"watermark": 0}, headers=headers).json()


def history(client, headers):
    return client.get("/api/v1/history", headers=headers).json()


def test_bought_creates_history_snapshot(client, auth):
    headers = auth(sub="user-b", name="User B")
    item_id = seed_items(client, auth(), ("koffie", "2x", "small ones", False))[0]
    r = push(client, headers, [trip_mut([{"item_id": item_id, "outcome": "bought"}])])
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"applied", "trip", "rejected"}
    assert body["rejected"] == []
    assert body["trip"]["id"] == body["trip"]["id"]
    assert body["trip"]["duplicate"] is False
    assert body["trip"]["readded"] == []

    items = {x["id"]: x for x in pull_all(client, headers)["items"]}
    assert items[item_id]["status"] == "completed"

    rows = history(client, headers)
    assert len(rows) == 1
    entry = rows[0]
    assert entry["item_id"] == item_id
    assert entry["name"] == "koffie"
    assert entry["quantity"] == "2x"
    assert entry["note"] == "small ones"
    assert entry["outcome"] == "bought"
    assert entry["staple_snapshot"] is False
    assert entry["actor"] == "user-b"
    assert isinstance(entry["occurred_at"], str)


def test_keep_leaves_active_no_history(client, auth):
    headers = auth()
    item_id = seed_items(client, headers, ("thee", "1x", None, False))[0]
    r = push(client, headers, [trip_mut([{"item_id": item_id, "outcome": "kept"}])])
    assert r.status_code == 200
    row = {x["id"]: x for x in pull_all(client, headers)["items"]}[item_id]
    assert row["status"] == "active"  # data-model: keep → active (unchanged)
    assert history(client, headers) == []


def test_drop_not_bought_history_and_dropped_status(client, auth):
    headers = auth()
    item_id = seed_items(client, headers, ("spruitjes", "500g", "yuck", False))[0]
    r = push(client, headers, [trip_mut([{"item_id": item_id, "outcome": "not_bought"}])])
    assert r.status_code == 200
    row = {x["id"]: x for x in pull_all(client, headers)["items"]}[item_id]
    assert row["status"] == "dropped"  # data-model: drop → dropped
    rows = history(client, headers)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "not_bought"
    assert rows[0]["item_id"] == item_id


def test_staple_readd_new_active_row_prefilled(client, auth):
    headers = auth()
    staple_id = seed_items(client, headers, ("melk", "2x", "halfvolle", True))[0]
    r = push(client, headers, [trip_mut([{"item_id": staple_id, "outcome": "bought"}])])
    assert r.status_code == 200
    readded = r.json()["trip"]["readded"]
    assert len(readded) == 1

    new_row = {x["id"]: x for x in pull_all(client, headers)["items"]}[readded[0]["id"]]
    assert new_row["id"] != staple_id  # NEW active row, fresh id
    assert new_row["name"] == "melk"
    assert new_row["staple"] is True
    assert new_row["quantity"] == "2x"  # prefilled from history snapshot
    assert new_row["note"] == "halfvolle"
    assert new_row["status"] == "active"

    old_row = {x["id"]: x for x in pull_all(client, headers)["items"]}[staple_id]
    assert old_row["status"] == "completed"  # old row untouched for audit
    assert old_row["staple"] is True


def test_staple_already_active_no_duplicate(client, auth):
    headers = auth()
    staple_id = seed_items(client, headers, ("melk", "2x", None, True))[0]
    r = push(client, headers, [trip_mut([{"item_id": staple_id, "outcome": "kept"}])])
    assert r.status_code == 200
    assert r.json()["trip"]["readded"] == []
    actives = [x for x in pull_all(client, headers)["items"] if x["name"] == "melk"]
    assert len(actives) == 1
    assert actives[0]["id"] == staple_id
    assert actives[0]["staple"] is True


def test_mixed_trip_decisions(client, auth):
    """bought + keep + drop in one trip, staple re-add only for bought staples."""
    headers = auth()
    bought, kept, dropped, kept_staple = seed_items(
        client,
        headers,
        ("koffie", None, None, False),
        ("thee", None, None, False),
        ("spruitjes", None, None, False),
        ("brood", "1", None, True),
    )
    r = push(
        client,
        headers,
        [
            trip_mut(
                [
                    {"item_id": bought, "outcome": "bought"},
                    {"item_id": kept, "outcome": "kept"},
                    {"item_id": dropped, "outcome": "not_bought"},
                    {"item_id": kept_staple, "outcome": "kept"},
                ]
            )
        ],
    )
    assert r.status_code == 200
    body = r.json()["trip"]
    assert body["readded"] == []  # kept staple stays active → no duplicate re-add
    rows = {x["id"]: x for x in pull_all(client, headers)["items"]}
    assert rows[bought]["status"] == "completed"
    assert rows[kept]["status"] == "active"
    assert rows[dropped]["status"] == "dropped"
    assert rows[kept_staple]["status"] == "active"
    outcomes = sorted(h["outcome"] for h in history(client, headers))
    assert outcomes == ["bought", "not_bought"]


def test_duplicate_trip_id_is_noop(client, auth):
    """contracts/api.md: duplicate trip id → {"duplicate": true}, no effects, client clears it."""
    headers = auth()
    item_id = seed_items(client, headers, ("koffie", "2x", None, True))[0]
    trip_id = str(uuid.uuid4())
    first = push(client, headers, [trip_mut([{"item_id": item_id, "outcome": "bought"}], trip_id)])
    assert first.status_code == 200
    assert first.json()["trip"]["duplicate"] is False
    history_after_first = history(client, headers)
    items_after_first = {x["id"]: x for x in pull_all(client, headers)["items"]}

    second = push(client, headers, [trip_mut([], trip_id)])
    assert second.status_code == 200
    trip = second.json()["trip"]
    assert trip["id"] == trip_id
    assert trip["duplicate"] is True
    assert trip["readded"] == []
    # no further effects
    assert len(history(client, headers)) == len(history_after_first)
    assert {x["id"]: x for x in pull_all(client, headers)["items"]} == items_after_first


def test_duplicate_trip_two_users_whoever_second_sees_duplicate(client, auth):
    headers_a = auth()
    headers_b = auth(sub="user-b", name="User B")
    item_id = seed_items(client, headers_a, ("koffie", None, None, False))[0]
    trip_id = str(uuid.uuid4())
    decision = [{"item_id": item_id, "outcome": "bought"}]
    first = push(client, headers_a, [trip_mut(decision, trip_id)])
    second = push(client, headers_b, [trip_mut(decision, trip_id)])
    assert first.json()["trip"]["duplicate"] is False
    assert second.json()["trip"]["duplicate"] is True
    assert len(history(client, headers_b)) == 1  # only one application


def test_atomicity_partial_failure_leaves_no_history(client, auth):
    """One transaction: any failure rolls back history rows, statuses and the trip row."""
    headers = auth()
    item_id = seed_items(client, headers, ("koffie", None, None, True))[0]
    new_item = str(uuid.uuid4())
    bad_trip = trip_mut(
        [
            {"item_id": item_id, "outcome": "bought"},
            {"item_id": str(uuid.uuid4()), "outcome": "bought"},  # unknown item
        ]
    )
    r = push(client, headers, [item_mut(id=new_item, name="pasta"), bad_trip])
    # per-mutation isolation: 200 + the poison trip reported at its index;
    # the valid item sibling still applies and commits.
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 1
    assert rejected[0]["op"] == "trip"
    # no forbidden effects from the rejected trip
    rows = {x["id"]: x for x in pull_all(client, headers)["items"]}
    assert (
        rows[item_id]["status"] == "active"
    )  # the trip never applied (kept row: bought → completed)
    assert rows[item_id]["staple"] is True
    assert history(client, headers) == []
    actives = [x for x in rows.values() if x["name"] == "koffie" and not x["tombstone"]]
    assert len(actives) == 1  # no staple re-add happened
    # the VALID sibling (index 0) applies despite the rejected trip
    assert rows[new_item]["name"] == "pasta"


def test_offline_trip_single_unit_applies_with_created_item(client, auth):
    """Offline add + trip in one outbox unit: item created then tripped, in order, or nothing."""
    headers = auth()
    fresh = str(uuid.uuid4())
    r = push(
        client,
        headers,
        [
            item_mut(id=fresh, name="pasta", staple=True, quantity="2"),
            trip_mut([{"item_id": fresh, "outcome": "bought"}]),
        ],
    )
    assert r.status_code == 200
    readded = r.json()["trip"]["readded"]
    assert len(readded) == 1
    rows = {x["id"]: x for x in pull_all(client, headers)["items"]}
    assert rows[fresh]["status"] == "completed"
    assert rows[readded[0]["id"]]["status"] == "active"
    assert rows[readded[0]["id"]]["quantity"] == "2"


def test_unknown_outcome_422(client, auth):
    item_id = seed_items(client, auth(), ("koffie", None, None, False))[0]
    r = push(client, auth(), [trip_mut([{"item_id": item_id, "outcome": "burned"}])])
    assert r.status_code == 422


def test_bought_requires_item_id(client, auth):
    r = push(client, auth(), [trip_mut([{"outcome": "bought"}])])
    assert r.status_code == 422


def test_unknown_item_in_decision_rejected(client, auth):
    ghost = str(uuid.uuid4())
    r = push(
        client,
        auth(),
        [trip_mut([{"item_id": ghost, "outcome": "bought"}])],
    )
    # per-mutation isolation: 200 + one rejected entry, trip never applied
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "trip"
    assert rejected[0]["detail"] == f"unknown item in trip decisions: {ghost}"


def test_trip_id_must_be_uuid(client, auth):
    r = push(client, auth(), [trip_mut([], trip_id="not-a-uuid")])
    assert r.status_code == 422
