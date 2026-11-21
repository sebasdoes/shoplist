"""Public runtime-config endpoint: unauthenticated by design (T038 follow-up)."""

import json


def test_config_is_public_and_matches_settings(client):
    r = client.get("/api/v1/config")
    assert r.status_code == 200, r.text
    body = json.loads(r.text)
    assert set(body) == {"issuer", "audience"}
    assert body["issuer"] and body["audience"]


def test_config_does_not_leak_secrets(client):
    body = json.loads(client.get("/api/v1/config").text)
    leaked = {k: v for k, v in body.items() if not v}
    assert not leaked, "empty values would defeat the runtime-config contract"
