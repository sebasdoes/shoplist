"""Poison isolation: one bad mutation never 400s the batch again."""

import uuid


def test_poison_upsert_rejected_but_batch_applies(client, auth):
    headers = auth()
    # live list + a POISON upsert for a tombstoned list (like an outbox entry
    # queued before another device deleted that list) in the SAME batch.
    live = "b9c1b5c1-2a77-4b6a-9cf0-6d5cf5bcb101"
    dead = "deadbeef-0000-4000-8000-00000000dead"
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"mutations": [{"op": "create_list", "list": {"id": live, "name": "Live"}}]},
    )
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"mutations": [{"op": "create_list", "list": {"id": dead, "name": "Dead"}}]},
    )
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={
            "mutations": [
                {"op": "delete_list", "list_id": dead},
            ]
        },
    )

    r = client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={
            "mutations": [
                {
                    "op": "upsert_item",
                    "item": {"id": str(uuid.uuid4()), "name": "poison", "list_id": dead},
                },
                {
                    "op": "upsert_item",
                    "item": {"id": str(uuid.uuid4()), "name": "survivor", "list_id": live},
                },
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # the survivor applies (Applied carries id/rev, not the name — verify via pull)
    survivor = client.post(
        "/api/v1/sync/pull",
        headers=headers,
        json={"watermark": 0, "include_history": True},
    ).json()
    names = [x["name"] for x in survivor["items"] if not x["tombstone"]]
    assert "survivor" in names
    assert "poison" not in names
    # the poison one is reported per-index
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["index"] == 0
    assert body["rejected"][0]["op"] == "upsert_item"
    assert "dead" in body["rejected"][0]["detail"]


def test_poison_trip_rejected_batch_still_applies(client, auth):
    headers = auth()
    live = "b9c1b5c1-2a77-4b6a-9cf0-6d5cf5bcb101"
    dead = "deadbeef-0000-4000-8000-00000000dead"
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"mutations": [{"op": "create_list", "list": {"id": live, "name": "Live"}}]},
    )
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"mutations": [{"op": "create_list", "list": {"id": dead, "name": "Dead"}}]},
    )
    client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"mutations": [{"op": "delete_list", "list_id": dead}]},
    )
    item_id = str(uuid.uuid4())
    r = client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={
            "mutations": [
                {"op": "trip", "trip": {"id": str(uuid.uuid4()), "list_id": dead, "decisions": []}},
                {"op": "upsert_item", "item": {"id": item_id, "name": "melk", "list_id": live}},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["op"] == "trip"
    survivor = client.post(
        "/api/v1/sync/pull",
        headers=headers,
        json={"watermark": 0, "include_history": True},
    ).json()
    names = [x["name"] for x in survivor["items"] if not x["tombstone"] and x["name"] == "melk"]
    assert names == ["melk"]
