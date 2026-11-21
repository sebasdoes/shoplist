"""T013 health test + T016 (written FIRST, TDD) — API contract cases per contracts/api.md."""

import uuid


def push(client, headers, mutations):
    return client.post("/api/v1/sync/push", json={"mutations": mutations}, headers=headers)


def item_mut(**overrides):
    fields = {"id": str(uuid.uuid4()), "name": "koffie", "tombstone": False}
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
    assert len(applied) == 1
    entry = applied[0]
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
    assert resp.json()["applied"][0]["merged_into"] == survivor


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
    assert len(applied) == 2
    data = client.post("/api/v1/sync/pull", json={"watermark": 0}, headers=headers).json()
    rows = [x for x in data["items"] if x["id"] == a]
    assert len(rows) == 1
    assert rows[0]["name"] == "margarine"
