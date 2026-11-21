"""T003 (written FIRST, TDD) — `reorder_items` push op per feature 004.

FLAT wire shape {"op": "reorder_items", "list_id": uuid, "order": [ids]}.
Only ACTIVE, live, same-list item ids may appear (unknown/tombstoned/
foreign-list/not-active → 400 + whole-batch rollback). Empty order,
duplicates and non-uuid ids → 422. sort_order = index over the given
sequence; partial orders are accepted (client is the final authority and
sends the full active set) — listed ids reindex, others keep position
and rev. Every assigned row bumps rev so SSE/pull propagate. Plus: new
ACTIVE items enter at the TOP of their list (sort_order = min − 1) and
existing upserts keep their position.
"""

import uuid

DEFAULT_LIST = "00000000-0000-4000-8000-000000000001"
LIST_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def push(client, headers, mutations):
    return client.post("/api/v1/sync/push", json={"mutations": mutations}, headers=headers)


def pull(client, headers, watermark=0):
    resp = client.post(
        "/api/v1/sync/pull", json={"watermark": watermark, "include_history": False},
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


def trip_mut(decisions, list_id=DEFAULT_LIST):
    return {
        "op": "trip",
        "trip": {"id": str(uuid.uuid4()), "list_id": list_id, "decisions": decisions},
    }


def reorder_mut(list_id, order):
    return {"op": "reorder_items", "list_id": list_id, "order": order}


def make_list(client, headers, list_id=LIST_A, name="Winkelpad A"):
    r = push(client, headers, [create_list_mut(list_id, name)])
    assert r.status_code == 200, r.text


def items_by_id(client, headers):
    return {x["id"]: x for x in pull(client, headers)["items"]}


def active_orders(client, headers, list_id=DEFAULT_LIST):
    rows = items_by_id(client, headers)
    return {
        i: x["sort_order"]
        for i, x in rows.items()
        if x["list_id"] == list_id and x["status"] == "active" and not x["tombstone"]
    }


def make_items(client, headers, list_id=DEFAULT_LIST, names=("melk", "brood", "rijst")):
    """Insert three active items in one batch; returns their ids oldest-first."""
    ids = [str(uuid.uuid4()) for _ in names]
    muts = [item_mut(id=i, list_id=list_id, name=n) for i, n in zip(ids, names)]
    r = push(client, headers, muts)
    assert r.status_code == 200, r.text
    return ids


# --- happy path ---------------------------------------------------------------


def test_reorder_items_sets_sequential_orders_and_bumps_revs(client, auth):
    headers = auth()
    a, b, c = make_items(client, headers)  # top-insert: c newest, lowest sort_order
    before = items_by_id(client, headers)
    watermark = pull(client, headers)["watermark"]

    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [a, b, c])])
    assert r.status_code == 200, r.text

    # echo echoes them in applied.items, shape {id, rev, updated_at, merged_into}
    applied = r.json()["applied"]["items"]
    assert [x["id"] for x in applied] == [a, b, c]
    for entry in applied:
        assert set(entry.keys()) == {"id", "rev", "updated_at", "merged_into"}
        assert entry["merged_into"] is None
        assert entry["rev"] > before[entry["id"]]["rev"]
        assert isinstance(entry["updated_at"], str)

    rows = items_by_id(client, headers)
    assert {i: rows[i]["sort_order"] for i in (a, b, c)} == {a: 0, b: 1, c: 2}
    assert {i: rows[i]["rev"] for i in (a, b, c)} == {x["id"]: x["rev"] for x in applied}

    # bumped revs make the reorder propagate as a watermark delta
    delta = pull(client, auth(sub="user-b"), watermark)["items"]
    delta_rows = {x["id"]: x for x in delta}
    assert {i: delta_rows[i]["sort_order"] for i in (a, b, c)} == {a: 0, b: 1, c: 2}


def test_reorder_items_full_active_set_across_lists(client, auth):
    """Reordering one list does not touch another list's items."""
    headers = auth()
    make_list(client, headers)
    in_a = make_items(client, headers, list_id=LIST_A)[0]
    before_a = active_orders(client, headers, list_id=LIST_A)

    fresh = str(uuid.uuid4())
    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [fresh, in_a])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    # the never-pushed foreign id fails the unknown-item check first
    assert rejected[0]["detail"] == f"unknown item: {fresh}"
    # the poison mutation is inert: LIST_A untouched, the ghost id never created
    assert active_orders(client, headers, list_id=LIST_A) == before_a
    assert fresh not in items_by_id(client, headers)


def test_reorder_items_other_list_untouched_on_success(client, auth):
    headers = auth()
    make_list(client, headers)
    a, b, c = make_items(client, headers, list_id=LIST_A)
    make_items(client, headers)  # also populates the default list (untouched below)
    before_default = active_orders(client, headers)

    r = push(client, headers, [reorder_mut(LIST_A, [c, b, a])])
    assert r.status_code == 200
    rows = items_by_id(client, headers)
    assert {i: rows[i]["sort_order"] for i in (a, b, c)} == {a: 2, b: 1, c: 0}
    assert active_orders(client, headers) == before_default  # default list untouched


def test_reorder_items_is_idempotent_retry(client, auth):
    """Re-sending the same order (outbox retry) is a success, re-bumping revs."""
    headers = auth()
    a, b, c = make_items(client, headers)
    mut = reorder_mut(DEFAULT_LIST, [c, b, a])
    assert push(client, headers, [mut]).status_code == 200
    assert push(client, headers, [mut]).status_code == 200
    assert active_orders(client, headers) == {c: 0, b: 1, a: 2}


# --- validation / 400 + rollback -----------------------------------------------


def test_reorder_items_unknown_item_400_and_rollback(client, auth):
    headers = auth()
    a, b, _ = make_items(client, headers)
    before_orders = active_orders(client, headers)
    before_revs = {i: x["rev"] for i, x in items_by_id(client, headers).items()}

    ghost = str(uuid.uuid4())
    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [a, ghost, b])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    assert rejected[0]["detail"] == f"unknown item: {ghost}"
    # the rejected mutation is inert: orders AND revs intact
    assert active_orders(client, headers) == before_orders
    assert {i: x["rev"] for i, x in items_by_id(client, headers).items()} == before_revs


def test_reorder_items_tombstoned_item_400(client, auth):
    headers = auth()
    a, b, _ = make_items(client, headers)
    push(client, headers, [item_mut(id=b, tombstone=True)])
    before = active_orders(client, headers)
    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [a, b])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    assert rejected[0]["detail"] == f"item is deleted: {b}"
    assert active_orders(client, headers) == before  # order untouched


def test_reorder_items_foreign_list_item_400(client, auth):
    headers = auth()
    make_list(client, headers)
    in_default = make_items(client, headers)[0]
    in_a = make_items(client, headers, list_id=LIST_A)[0]
    before = active_orders(client, headers)
    before_a = active_orders(client, headers, list_id=LIST_A)

    r = push(
        client,
        headers,
        [reorder_mut(LIST_A, [in_default]), reorder_mut(DEFAULT_LIST, [in_a])],
    )
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    # BOTH mutations are poison (each names a foreign item) — reported per index
    assert [(x["index"], x["op"]) for x in rejected] == [
        (0, "reorder_items"),
        (1, "reorder_items"),
    ]
    assert rejected[0]["detail"] == f"item {in_default} belongs to another list"
    assert rejected[1]["detail"] == f"item {in_a} belongs to another list"
    assert active_orders(client, headers) == before
    assert active_orders(client, headers, list_id=LIST_A) == before_a


def test_reorder_items_not_active_item_400(client, auth):
    """Only the ACTIVE queue is reorderable — completed items may not appear."""
    headers = auth()
    live, ticked = make_items(client, headers, names=("melk", "brood"))[0:2]
    push(client, headers, [trip_mut([{"item_id": ticked, "outcome": "bought"}])])
    before = active_orders(client, headers)
    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [live, ticked])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    assert rejected[0]["detail"] == f"item {ticked} is not active"
    assert active_orders(client, headers) == before  # order untouched


def test_reorder_items_unknown_list_400(client, auth):
    headers = auth()
    x = make_items(client, headers)[0]
    before = active_orders(client, headers)
    ghost_list = str(uuid.uuid4())
    r = push(client, headers, [reorder_mut(ghost_list, [x])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    assert rejected[0]["detail"] == f"unknown list: {ghost_list}"
    assert active_orders(client, headers) == before  # order untouched


def test_reorder_items_on_deleted_list_400(client, auth):
    headers = auth()
    make_list(client, headers)
    x = make_items(client, headers, list_id=LIST_A)[0]
    push(client, headers, [{"op": "delete_list", "list_id": LIST_A}])
    r = push(client, headers, [reorder_mut(LIST_A, [x])])
    assert r.status_code == 200, r.text
    rejected = r.json()["rejected"]
    assert len(rejected) == 1
    assert rejected[0]["index"] == 0
    assert rejected[0]["op"] == "reorder_items"
    assert rejected[0]["detail"] == f"list is deleted: {LIST_A}"
    # the reorder never touched the (deleted) list's items
    assert items_by_id(client, headers)[x]["status"] == "active"


# --- validation / 422 ------------------------------------------------------------


def test_reorder_items_invalid_payload_422(client, auth):
    headers = auth()
    a, b, _ = make_items(client, headers)
    # request-level validation still 422s
    assert push(client, headers, [{"op": "reorder_items"}]).status_code == 422  # no payload
    bad = push(client, headers, [reorder_mut("not-a-uuid", [a])])  # list_id not a uuid
    assert bad.status_code == 422

    def body(mut):
        r = push(client, headers, [mut])
        assert r.status_code == 200, r.text
        rejected = r.json()["rejected"]
        assert len(rejected) == 1
        assert rejected[0]["index"] == 0
        assert rejected[0]["op"] == "reorder_items"
        return rejected[0]["detail"]

    # op-level validation is now reported per-mutation (200 + rejected)
    assert (
        body(reorder_mut(DEFAULT_LIST, [])) == "order: the active item sequence is required"
    )  # empty
    assert body(reorder_mut(DEFAULT_LIST, ["not-a-uuid"])) == "order: not a uuid: not-a-uuid"
    assert body(reorder_mut(DEFAULT_LIST, [a, a, b])) == "order: duplicate item ids"


# --- partial order (listed ids only) ---------------------------------------------


def test_reorder_items_partial_order_reindexes_listed_only(client, auth):
    headers = auth()
    a, b, c = make_items(client, headers)
    push(client, headers, [reorder_mut(DEFAULT_LIST, [a, b, c])])
    before = items_by_id(client, headers)

    r = push(client, headers, [reorder_mut(DEFAULT_LIST, [c])])
    assert r.status_code == 200, r.text
    rows = items_by_id(client, headers)
    assert rows[c]["sort_order"] == 0  # listed id takes its payload index
    assert rows[c]["rev"] > before[c]["rev"]
    for untouched in (a, b):
        assert rows[untouched]["sort_order"] == before[untouched]["sort_order"]
        assert rows[untouched]["rev"] == before[untouched]["rev"]  # no rev bump


# --- top-insert for new ACTIVE items ---------------------------------------------


def test_new_item_enters_at_top(client, auth):
    headers = auth()
    a, b, c = make_items(client, headers)  # c has min sort_order
    rows = items_by_id(client, headers)
    assert rows[a]["sort_order"] > rows[b]["sort_order"] > rows[c]["sort_order"]

    fresh = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=fresh, name="kaas")])
    assert r.status_code == 200
    rows = items_by_id(client, headers)
    assert rows[fresh]["sort_order"] < min(
        rows[i]["sort_order"] for i in (a, b, c)
    )


def test_new_item_enters_at_top_after_explicit_reorder(client, auth):
    headers = auth()
    a, b, c = make_items(client, headers)
    push(client, headers, [reorder_mut(DEFAULT_LIST, [a, b, c])])
    fresh = str(uuid.uuid4())
    push(client, headers, [item_mut(id=fresh, name="kaas")])
    rows = items_by_id(client, headers)
    assert rows[fresh]["sort_order"] < rows[a]["sort_order"]  # top of the queue


def test_staple_readd_enters_at_top(client, auth):
    headers = auth()
    make_list(client, headers)
    other = str(uuid.uuid4())
    staple_id = str(uuid.uuid4())
    push(
        client,
        headers,
        [item_mut(id=other, list_id=LIST_A, name="rijst"), item_mut(id=staple_id, list_id=LIST_A,
            name="melk", staple=True, quantity="2x")],
    )
    r = push(
        client,
        headers,
        [trip_mut([{"item_id": staple_id, "outcome": "bought"}], list_id=LIST_A)],
    )
    assert r.status_code == 200, r.text
    readded = r.json()["trip"]["readded"]
    assert len(readded) == 1
    rows = items_by_id(client, headers)
    assert rows[readded[0]["id"]]["sort_order"] < rows[other]["sort_order"]


def test_existing_upserts_keep_position(client, auth):
    """Field edits and dedupe merges never move an item."""
    headers = auth()
    a, b, c = make_items(client, headers)
    push(client, headers, [reorder_mut(DEFAULT_LIST, [a, b, c])])
    before = items_by_id(client, headers)

    # field edit on the same id (name untouched — same field-set value)
    push(client, headers, [item_mut(id=b, name="brood", quantity="2x")])
    rows = items_by_id(client, headers)
    assert rows[b]["sort_order"] == before[b]["sort_order"]

    # dedupe merge: pushing a new id with the same name folds onto the survivor
    dupe = str(uuid.uuid4())
    r = push(client, headers, [item_mut(id=dupe, name="BROOD")])
    assert r.status_code == 200
    assert r.json()["applied"]["items"][0]["merged_into"] == b
    rows = items_by_id(client, headers)
    assert rows[b]["sort_order"] == before[b]["sort_order"]
    # the merged row got a rev bump (field application), position unchanged
