"""T032 — GET /api/v1/history + DELETE /api/v1/history/{id} → 204 hard delete (FR-011)."""

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


def _make_history(client, headers, n=3):
    ids = [str(uuid.uuid4()) for _ in range(n)]
    r = push(client, headers, [item_mut(id=i, name=f"item-{k}") for k, i in enumerate(ids)])
    assert r.status_code == 200
    trip_id = str(uuid.uuid4())
    r = push(
        client,
        headers,
        [
            {
                "op": "trip",
                "trip": {
                    "id": trip_id,
                    "list_id": DEFAULT_LIST,
                    "decisions": [{"item_id": i, "outcome": "bought"} for i in ids],
                },
            }
        ],
    )
    assert r.status_code == 200, r.text
    return ids


def test_history_full_list(client, auth):
    headers = auth()
    _make_history(client, headers, n=3)
    resp = client.get("/api/v1/history", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    fields = {
        "id",
        "list_id",
        "item_id",
        "name",
        "quantity",
        "note",
        "outcome",
        "staple_snapshot",
        "actor",
        "occurred_at",
        "rev",
    }
    for row in rows:
        assert set(row.keys()) == fields
        assert row["outcome"] == "bought"
    # newest first
    occurred = [r["occurred_at"] for r in rows]
    assert occurred == sorted(occurred, reverse=True)


def test_delete_history_204_hard_delete(client, auth):
    headers = auth()
    _make_history(client, headers, n=2)
    rows = client.get("/api/v1/history", headers=headers).json()
    target = rows[0]["id"]

    resp = client.delete(f"/api/v1/history/{target}", headers=headers)
    assert resp.status_code == 204
    assert resp.content == b""

    remaining = client.get("/api/v1/history", headers=headers).json()
    assert [r["id"] for r in remaining] == [rows[1]["id"]]
    # hard delete: nothing to pull back, no tombstone
    assert all(r["id"] != target for r in remaining)


def test_delete_history_unknown_404(client, auth):
    resp = client.delete(f"/api/v1/history/{uuid.uuid4()}")
    assert resp.status_code == 401  # unauthenticated cannot delete
    resp = client.delete(f"/api/v1/history/{uuid.uuid4()}", headers=auth(sub="user-a"))
    assert resp.status_code == 404
    assert "detail" in resp.json()
