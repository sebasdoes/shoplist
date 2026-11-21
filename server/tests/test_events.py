"""SSE server-push endpoint: auth via ?token=, handshake + 'changed' on rev bump."""

import threading
import time


def test_events_requires_token(client):
    r = client.get("/api/v1/events")
    assert r.status_code in (401, 422), r.text  # missing token -> validation error


def test_events_rejects_bad_token(client):
    r = client.get("/api/v1/events?token=not-a-jwt")
    assert r.status_code == 401, r.text


def test_events_streams_handshake_and_changed(client, auth):
    headers = auth()
    events = []

    def reader():
        with client.stream(
            "GET", "/api/v1/events", params={"token": headers["Authorization"].split()[1]}
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            for text in r.iter_text():
                events.append(text)
                if "changed" in "".join(events):
                    break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.6)

    # push a mutation so the global rev advances
    push = client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={
            "mutations": [
                {
                    "op": "upsert_item",
                    "item": {
                        "id": "a5f0d9d1-3f9a-4e39-8f61-2e5a8e3c9b77",
                        "name": "koffie",
                    },
                }
            ]
        },
    )
    assert push.status_code == 200, push.text

    t.join(timeout=8)
    assert not t.is_alive(), "SSE stream did not emit 'changed' in time"
    joined = "".join(events)
    assert "connected" in joined
    assert "changed" in joined
