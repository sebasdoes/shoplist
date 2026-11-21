"""Hardening of the unauthenticated /api/v1/log diagnostic beacon (TDD).

Contract kept: unauthenticated, JSON body in, {"ok": true} out.
Hardening: msg clamped to 500 chars, control chars/newlines neutralized before
printing (log-forging), and a coarse per-IP token-bucket rate limit (~30/min)
that answers 429 with no body; a non-JSON body is rejected (422).
"""

import json


def test_client_log_still_ok(client):
    r = client.post("/api/v1/log", json={"msg": "probe"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_client_log_sanitizes_newlines_and_control_chars(client, capsys):
    payload = {
        "msg": "2026-11-21 INFO ok\n[server] FAKE LINE\r\nx\x00y\x1bz\u0085tail"
    }
    r = client.post("/api/v1/log", json=payload)
    assert r.status_code == 200, r.text
    out = capsys.readouterr().out
    client_lines = [line for line in out.splitlines() if "client-log" in line]
    assert client_lines, f"beacon line missing in {out!r}"
    line = client_lines[-1]
    # exactly ONE log line was produced by this request — no forged extra lines
    assert out.count("FAKE LINE") == 1 or "FAKE LINE" in line
    for ch in ("\n", "\r", "\x00", "\x1b", "\x85"):
        assert ch not in line


def test_client_log_msg_clamped_to_500(client, capsys):
    r = client.post("/api/v1/log", json={"msg": "A" * 5000})
    assert r.status_code == 200, r.text
    out = capsys.readouterr().out
    line = [line for line in out.splitlines() if "client-log" in line][-1]
    assert len(line) == len("[client-log] ") + 500


def test_client_log_rate_limited_after_30(client):
    """Token bucket: ~30 requests/min per client IP; the 31st within the window
    gets a bare 429 (no body) and the beacon stays silent."""
    for i in range(30):
        r = client.post("/api/v1/log", json={"msg": f"m{i}"})
        assert r.status_code == 200, (i, r.status_code)
    r = client.post("/api/v1/log", json={"msg": "overflow"})
    assert r.status_code == 429
    assert r.content == b""  # silently


def test_client_log_rejects_non_json_body(client):
    r = client.post(
        "/api/v1/log", content=b"not json", headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 422, r.text
    assert set(json.loads(r.text)) == {"detail"}  # error contract shape
