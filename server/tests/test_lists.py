"""T003 (written FIRST, TDD) — list ops per specs/002 contracts/api.md.

create_list / delete_list push ops, list_id on items + trips, per-list dedupe
merge, delete cascade tombstones, last-list 409, cross-list trip decisions 400,
whole-batch rollback on list errors.
"""

import uuid

DEFAULT_LIST = "00000000-0000-4000-8000-000000000001"
LIST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIST_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def push(client, headers, mutations):
    return client.post("/api/v1/sync/push", json={"mutations": mutations}, headers=headers)


def pull(client, headers, watermark=0, include_history=False):
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": watermark, "include_history": include_history},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def item_mut(**overrides):
    fields = {
        "id": str(uuid.uuid4()),
        "name": "koffie",
        "tombstone": False,
        "list_id": DEFAULT_LIST,
    }
    fields.update(overrides)
    return {"op": "upsert_item", "item": fields}


def create_list_mut(list_id, name):
    return {"op": "create_list", "list": {"id": list_id, "name": name}}


def delete_list_mut(list_id):
    return {"op": "delete_list", "list_id": list_id}


def trip_mut(decisions, trip_id=None, list_id=DEFAULT_LIST):
    return {
        "op": "trip",
        "trip": {"id": trip_id or str(uuid.uuid4()), "list_id": list_id, "decisions": decisions},
    }


def make_list(client, headers, list_id=LIST_A, name="Winkelpad A"):
    r = push(client, headers, [create_list_mut(list_id, name)])
    assert r.status_code == 200, r.text
    return r.json()


def items_by_id(data):
    return {x["id"]: x for x in data["items"]}


# --- create_list ------------------------------------------------------------


def test_create_list_applies_and_pulls(client, auth):
    headers = auth()
    body = make_list(client, headers)
    entry = body["applied"]["lists"][0]
    assert entry == {
        "id": LIST_A,
        "name": "Winkelpad A",
        "rev": entry["rev"],
        "tombstone": False,
        "sort_order": 1,  # after the seeded default list (v3: create_list = max+1)
    }
    assert isinstance(entry["rev"], int) and entry["rev"] >= 1

    data = pull(client, headers)
    lists = {x["id"]: x for x in data["lists"]}
    assert set(lists) == {DEFAULT_LIST, LIST_A}
    assert lists[LIST_A]["name"] == "Winkelpad A"
    assert lists[LIST_A]["tombstone"] is False
    # items in the response carry list_id
    assert all("list_id" in x for x in data["items"])


def test_create_list_name_validation_422(client, auth):
    headers = auth()
    for bad in ["", "   ", "x" * 101]:
        r = push(client, headers, [create_list_mut(LIST_A, bad)])
        assert r.status_code == 422, bad
    r = push(client, headers, [create_list_mut(LIST_A, "x" * 100)])  # boundary ok
    assert r.status_code == 200


def test_create_list_name_is_trimmed_and_non_uuid_422(client, auth):
    headers = auth()
    r = push(client, headers, [create_list_mut(LIST_A, "  Winkel  ")])
    assert r.status_code == 200
    assert r.json()["applied"]["lists"][0]["name"] == "Winkel"
    r = push(client, headers, [create_list_mut("not-a-uuid", "X")])
    assert r.status_code == 422


def test_create_list_name_reuse_allowed(client, auth):
    """Names are NOT unique — same name on two lists, and reuse after a tombstone."""
    headers = auth()
    assert push(client, headers, [create_list_mut(LIST_A, "Boodschappen")]).status_code == 200
    assert push(client, headers, [create_list_mut(LIST_B, "Boodschappen")]).status_code == 200
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    again = str(uuid.uuid4())
    r = push(client, headers, [create_list_mut(again, "Boodschappen")])
    assert r.status_code == 200


def test_create_list_duplicate_id_is_idempotent_noop(client, auth):
    headers = auth()
    first = make_list(client, headers)
    second = make_list(client, headers)
    assert len(second["applied"]["lists"]) == 1
    assert second["applied"]["lists"][0]["rev"] == first["applied"]["lists"][0]["rev"]
    data = pull(client, headers)
    assert [x["id"] for x in data["lists"]].count(LIST_A) == 1


# --- delete_list ------------------------------------------------------------


def test_delete_list_cascades_item_tombstones(client, auth):
    headers = auth()
    make_list(client, headers)
    item_a, item_default = str(uuid.uuid4()), str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=item_a, list_id=LIST_A), item_mut(id=item_default)])
    assert r.status_code == 200
    watermark = pull(client, auth(sub="user-b"))["watermark"]

    r = push(client, headers, [delete_list_mut(LIST_A)])
    assert r.status_code == 200
    entry = r.json()["applied"]["lists"][0]
    assert entry["id"] == LIST_A and entry["tombstone"] is True

    data = pull(client, headers)
    assert {x["id"]: x for x in data["lists"]}[LIST_A]["tombstone"] is True
    rows = items_by_id(data)
    assert rows[item_a]["tombstone"] == 1  # cascade tombstone
    assert rows[item_default]["tombstone"] == 0  # other list untouched

    # offline device receives the cascade via the normal watermark pull
    delta = pull(client, auth(sub="user-b"), watermark)
    assert {x["id"]: x for x in delta["lists"]}[LIST_A]["tombstone"] is True
    assert {x["id"]: x for x in delta["items"]}[item_a]["tombstone"] == 1


def test_delete_last_live_list_409(client, auth):
    headers = auth()
    r = push(client, headers, [delete_list_mut(DEFAULT_LIST)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "delete_list"
    assert rejected[0]["detail"] == "cannot delete the last list"
    # the forbidden delete did NOT happen: the default list stays live
    data = pull(client, headers)
    assert {x["id"]: x for x in data["lists"]}[DEFAULT_LIST]["tombstone"] is False


def test_delete_last_live_list_409_after_second_list_deleted(client, auth):
    headers = auth()
    make_list(client, headers)
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    r = push(client, headers, [delete_list_mut(DEFAULT_LIST)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "delete_list"
    assert rejected[0]["detail"] == "cannot delete the last list"
    assert {x["id"]: x for x in pull(client, headers)["lists"]}[DEFAULT_LIST][
        "tombstone"
    ] is False


def test_delete_last_live_list_ok_when_another_is_live(client, auth):
    headers = auth()
    item_default = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_default)])
    make_list(client, headers)  # second live list exists
    assert push(client, headers, [delete_list_mut(DEFAULT_LIST)]).status_code == 200


def test_delete_already_deleted_list_is_noop_success(client, auth):
    headers = auth()
    make_list(client, headers)
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    second = push(client, headers, [delete_list_mut(LIST_A)])
    assert second.status_code == 200  # no-op success, not an error


def test_delete_unknown_list_400(client, auth):
    ghost = str(uuid.uuid4())
    r = push(client, auth(), [delete_list_mut(ghost)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "delete_list"
    assert rejected[0]["detail"] == f"unknown list: {ghost}"


# --- item pushes need a live list ---------------------------------------------


def test_item_push_unknown_list_400_and_full_rollback(client, auth):
    headers = auth()
    ghost = str(uuid.uuid4())
    good = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=good), item_mut(list_id=ghost)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 1
    assert rejected[0]["op"] == "upsert_item"
    assert rejected[0]["detail"] == f"unknown list: {ghost}"
    rows = items_by_id(pull(client, headers))
    assert good in rows  # the VALID sibling (index 0) applies
    assert all(x["list_id"] != ghost for x in rows.values())  # poison never created


def test_item_push_tombstoned_list_400_and_full_rollback(client, auth):
    headers = auth()
    make_list(client, headers)
    item_a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_a, list_id=LIST_A)])
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    good = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=good), item_mut(list_id=LIST_A, name="weer")])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 1
    assert rejected[0]["op"] == "upsert_item"
    assert rejected[0]["detail"] == f"list is deleted: {LIST_A}"
    items = items_by_id(pull(client, headers))
    assert good in items  # the VALID sibling (index 0) applies
    assert item_a in items and items[item_a]["tombstone"] == 1  # cascade intact
    assert all(x["name"] != "weer" for x in items.values())  # poison never saved


def test_item_without_list_id_422(client, auth):
    mut = item_mut()
    del mut["item"]["list_id"]
    assert push(client, auth(), [mut]).status_code == 422


# --- per-list dedupe merge ----------------------------------------------------


def test_dedupe_merge_scoped_to_list(client, auth):
    headers = auth()
    make_list(client, headers)
    in_default, in_a = str(uuid.uuid4()), str(uuid.uuid4())
    r = push(
        client,
        headers,
        [
            item_mut(id=in_default, name="koffie"),
            item_mut(id=in_a, list_id=LIST_A, name="koffie"),
        ],
    )
    assert r.status_code == 200
    assert r.json()["applied"]["lists"] == []

    dupe = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=dupe, list_id=LIST_A, name="KOFFIE")])
    assert r.status_code == 200
    applied = r.json()["applied"]["items"][0]
    assert applied["merged_into"] == in_a  # folds onto the SAME list's survivor

    rows = items_by_id(pull(client, headers))
    assert rows[in_default]["tombstone"] == 0  # untouched cross-list
    assert rows[in_a]["tombstone"] == 0
    # contract update: the pushed dupe is now recorded as a TOMBSTONED row
    # (fold propagates to other devices + the client can confirm by its id);
    # it must NOT be live.
    assert rows[dupe]["tombstone"] == 1


# --- trip scoping ---------------------------------------------------------------


def test_trip_cross_list_decision_400_and_rollback(client, auth):
    headers = auth()
    make_list(client, headers)
    item_default = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_default)])
    watermark = pull(client, headers)["watermark"]

    r = push(
        client,
        auth(sub="user-b", name="User B"),
        [trip_mut([{"item_id": item_default, "outcome": "bought"}], list_id=LIST_A)],
    )
    # per-mutation isolation: 200 + reported at its index, trip never applied
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "trip"
    assert rejected[0]["detail"] == f"item {item_default} belongs to another list"
    data = pull(client, headers, watermark)  # the forbidden effects never happened
    assert data["items"] == []
    assert client.get("/api/v1/history", headers=headers).json() == []


def test_trip_bulk_decisions_scoped_to_own_list(client, auth):
    headers = auth()
    make_list(client, headers)
    in_default, in_a = str(uuid.uuid4()), str(uuid.uuid4())
    push(
        client,
        headers,
        [
            item_mut(id=in_default, name="rijst"),
            item_mut(id=in_a, list_id=LIST_A, name="pasta"),
        ],
    )

    r = push(client, headers, [trip_mut([{"outcome": "not_bought"}], list_id=LIST_A)])
    assert r.status_code == 200
    rows = items_by_id(pull(client, headers))
    assert rows[in_a]["status"] == "dropped"  # bulk applies to list A's remainder
    assert rows[in_default]["status"] == "active"  # cross-list item untouched


def test_trip_duplicate_id_still_noop(client, auth):
    headers = auth()
    item_id = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_id)])
    trip_id = str(uuid.uuid4())
    first = push(client, headers, [trip_mut([{"item_id": item_id, "outcome": "bought"}], trip_id)])
    assert first.status_code == 200
    second = push(client, headers, [trip_mut([], trip_id)])
    assert second.status_code == 200
    assert second.json()["trip"]["duplicate"] is True


def test_trip_without_list_id_422(client, auth):
    mut = trip_mut([])
    del mut["trip"]["list_id"]
    assert push(client, auth(), [mut]).status_code == 422


def test_trip_on_unknown_or_deleted_list_400(client, auth):
    headers = auth()
    ghost = str(uuid.uuid4())
    r = push(client, headers, [trip_mut([], list_id=ghost)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "trip"
    assert rejected[0]["detail"] == f"unknown list: {ghost}"
    make_list(client, headers)
    item_a = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_a, list_id=LIST_A)])
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    r = push(client, headers, [trip_mut([], list_id=LIST_A)])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "trip"
    assert rejected[0]["detail"] == f"list is deleted: {LIST_A}"


def test_staple_readd_stays_on_same_list(client, auth):
    headers = auth()
    make_list(client, headers)
    staple_id = str(uuid.uuid4())
    push(
        client,
        headers,
        [item_mut(id=staple_id, list_id=LIST_A, name="melk", staple=True, quantity="2x")],
    )
    r = push(
        client,
        headers,
        [trip_mut([{"item_id": staple_id, "outcome": "bought"}], list_id=LIST_A)],
    )
    assert r.status_code == 200
    readded = r.json()["trip"]["readded"]
    assert len(readded) == 1
    rows = items_by_id(pull(client, headers))
    assert rows[readded[0]["id"]]["list_id"] == LIST_A
    assert rows[readded[0]["id"]]["status"] == "active"


# --- reorder_lists (feature 003, schema v3) -----------------------------------


def reorder_mut(order):
    return {"op": "reorder_lists", "order": order}


def sort_orders(client, headers):
    data = pull(client, headers)
    return {x["id"]: x["sort_order"] for x in data["lists"]}


def test_pull_list_rows_carry_sort_order(client, auth):
    headers = auth()
    make_list(client, headers)
    data = pull(client, headers)
    lists = {x["id"]: x for x in data["lists"]}
    assert lists[DEFAULT_LIST]["sort_order"] == 0
    assert lists[LIST_A]["sort_order"] == 1


def test_create_list_assigns_max_plus_one(client, auth):
    headers = auth()
    make_list(client, headers)
    push(client, headers, [create_list_mut(LIST_B, "B")])
    assert sort_orders(client, headers) == {DEFAULT_LIST: 0, LIST_A: 1, LIST_B: 2}
    # max counts tombstoned rows too — the freed slot is not reused
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    fresh_id = str(uuid.uuid4())
    r = push(client, headers, [create_list_mut(fresh_id, "C")])
    assert r.status_code == 200
    assert r.json()["applied"]["lists"][0]["sort_order"] == 3
    assert sort_orders(client, headers)[fresh_id] == 3


def test_reorder_lists_sets_sequential_sort_orders(client, auth):
    headers = auth()
    make_list(client, headers)
    push(client, headers, [create_list_mut(LIST_B, "B")])

    r = push(client, headers, [reorder_mut([LIST_B, LIST_A, DEFAULT_LIST])])
    assert r.status_code == 200, r.text
    applied = r.json()["applied"]["lists"]
    assert [(x["id"], x["sort_order"]) for x in applied] == [
        (LIST_B, 0),
        (LIST_A, 1),
        (DEFAULT_LIST, 2),
    ]
    # each reordered row carries a fresh (bumped) rev
    assert all(isinstance(x["rev"], int) and x["rev"] >= 1 for x in applied)
    revs = {x["id"]: x["rev"] for x in applied}

    # pull rows adopt the new order (id, sort_order and bumped rev)
    pulled = {x["id"]: x for x in pull(client, headers)["lists"]}
    assert {i: x["sort_order"] for i, x in pulled.items()} == {
        LIST_B: 0,
        LIST_A: 1,
        DEFAULT_LIST: 2,
    }
    assert {i: x["rev"] for i, x in pulled.items() if i in revs} == revs

    # bumped revs make the reorder propagate as a watermark delta
    watermark = pull(client, auth(sub="user-b"), 0)["watermark"]
    # re-apply a different order to produce a delta after the old watermark
    r = push(client, headers, [reorder_mut([DEFAULT_LIST, LIST_A, LIST_B])])
    assert r.status_code == 200
    delta = pull(client, auth(sub="user-b"), watermark)
    delta_lists = {x["id"]: x for x in delta["lists"]}
    assert set(delta_lists) == {DEFAULT_LIST, LIST_A, LIST_B}
    assert delta_lists[LIST_B]["sort_order"] == 2
    assert delta_lists[DEFAULT_LIST]["sort_order"] == 0
    assert all(isinstance(x["rev"], int) for x in delta_lists.values())


def test_reorder_lists_bumps_revs_for_sse_and_pull(client, auth):
    headers = auth()
    make_list(client, headers)
    before = {x["id"]: x["rev"] for x in pull(client, headers)["lists"]}
    watermark = pull(client, auth(sub="user-b"))["watermark"]

    r = push(client, headers, [reorder_mut([LIST_A, DEFAULT_LIST])])
    assert r.status_code == 200
    assert all(
        x["rev"] > before[x["id"]]
        for x in r.json()["applied"]["lists"]
    )
    # offline device sees the reorder in its watermark delta
    delta = pull(client, auth(sub="user-b"), watermark)
    delta_lists = {x["id"]: x for x in delta["lists"]}
    assert {i: x["sort_order"] for i, x in delta_lists.items()} == {DEFAULT_LIST: 1, LIST_A: 0}


def test_reorder_lists_unknown_id_400_and_rollback(client, auth):
    headers = auth()
    make_list(client, headers)
    push(client, headers, [create_list_mut(LIST_B, "B")])
    before_orders = sort_orders(client, headers)
    before_revs = {x["id"]: x["rev"] for x in pull(client, headers)["lists"]}

    ghost = str(uuid.uuid4())
    r = push(client, headers, [reorder_mut([DEFAULT_LIST, ghost, LIST_A])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_lists"
    assert rejected[0]["detail"] == f"unknown list: {ghost}"
    # rejection: the whole mutation is inert — previous order AND revs intact
    assert sort_orders(client, headers) == before_orders
    assert {x["id"]: x["rev"] for x in pull(client, headers)["lists"]} == before_revs


def test_reorder_lists_tombstoned_id_400(client, auth):
    headers = auth()
    make_list(client, headers)
    assert push(client, headers, [delete_list_mut(LIST_A)]).status_code == 200
    before_orders = sort_orders(client, headers)
    r = push(client, headers, [reorder_mut([DEFAULT_LIST, LIST_A])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_lists"
    assert rejected[0]["detail"] == f"list is deleted: {LIST_A}"
    assert sort_orders(client, headers) == before_orders  # order untouched


def test_reorder_lists_mixed_batch_rolls_back_atomically(client, auth):
    headers = auth()
    make_list(client, headers)
    item_id = str(uuid.uuid4())
    push(client, headers, [item_mut(id=item_id)])
    watermark = pull(client, headers)["watermark"]

    ghost = str(uuid.uuid4())
    r = push(
        client,
        headers,
        [reorder_mut([DEFAULT_LIST, ghost]), item_mut(id=str(uuid.uuid4()), name="nieuw")],
    )
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    # the poison reorder is reported at its index ...
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_lists"
    assert rejected[0]["detail"] == f"unknown list: {ghost}"
    data = pull(client, headers, watermark)
    assert data["lists"] == []  # the reorder's forbidden effect never happened
    # ... and the VALID sibling (index 1) still applies
    assert [x["name"] for x in data["items"] if not x["tombstone"]] == ["nieuw"]
    assert item_id not in [x["id"] for x in data["items"]]  # watermark delta only


def test_reorder_lists_invalid_payload_422(client, auth):
    headers = auth()
    # request-level validation still 422s
    assert push(client, headers, [{"op": "reorder_lists"}]).status_code == 422  # missing payload

    def body(mut):
        r = push(client, headers, [mut])
        assert r.status_code == 200, r.text
        rejected = r.json()["rejected"]
        assert len(rejected) == 1
        assert rejected[0]["index"] == 0
        assert rejected[0]["op"] == "reorder_lists"
        return rejected[0]["detail"]

    assert body(reorder_mut([])) == "order: the full list sequence is required"  # empty order
    assert body(reorder_mut(["not-a-uuid"])) == "order: not a uuid: not-a-uuid"
    assert (
        body(reorder_mut([DEFAULT_LIST, DEFAULT_LIST])) == "order: duplicate list ids"
    )


def test_reorder_lists_is_idempotent_retry(client, auth):
    """Re-sending the same order (outbox retry) is a success, re-bumping revs."""
    headers = auth()
    make_list(client, headers)
    push(client, headers, [create_list_mut(LIST_B, "B")])
    mut = reorder_mut([LIST_B, LIST_A, DEFAULT_LIST])
    assert push(client, headers, [mut]).status_code == 200
    assert push(client, headers, [mut]).status_code == 200
    assert sort_orders(client, headers) == {LIST_B: 0, LIST_A: 1, DEFAULT_LIST: 2}
