"""T007 (written FIRST, TDD) — JWT auth validation per research.md D7.

Covers: valid token → 200; invalid signature, wrong aud, expired, missing header
→ 401; JWKS cache TTL + refresh on unknown kid.
"""

import base64
import time

import httpx
import jwt
import pytest


def b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def rsa_env():
    """Generated RSA pair served as a mock JWKS."""
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    key = generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "test-kid-1",
                "alg": "RS256",
                "use": "sig",
                "n": b64url_uint(nums.n),
                "e": b64url_uint(nums.e),
            }
        ]
    }
    return key, jwks


def mint(key, kid: str, claims: dict) -> str:
    return jwt.encode(
        claims, key.public_key() if False else key, algorithm="RS256", headers={"kid": kid}
    )


def make_client(rsa_env, fetch_log=None):
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    key, jwks = rsa_env

    def fetcher() -> dict:
        if fetch_log is not None:
            fetch_log.append(time.monotonic())
        return jwks

    provider = create_jwks_provider(fetcher=fetcher)
    app = create_app(oidc_provider=provider)
    return TestClient(app), key


def test_valid_token_gets_200(rsa_env):
    client, key = make_client(rsa_env)
    now = int(time.time())
    token = mint(
        key,
        "test-kid-1",
        {
            "sub": "user-a",
            "name": "User A",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "iat": now,
            "exp": now + 600,
        },
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_missing_header_401(rsa_env):
    client, _ = make_client(rsa_env)
    resp = client.post("/api/v1/sync/pull", json={"watermark": 0})
    assert resp.status_code == 401


def test_garbage_header_401(rsa_env):
    client, _ = make_client(rsa_env)
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert resp.status_code == 401


def test_invalid_signature_401(rsa_env):
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    client, _ = make_client(rsa_env)
    impostor = generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    token = mint(
        impostor,
        "test-kid-1",
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": now + 600,
        },
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_wrong_audience_401(rsa_env):
    client, key = make_client(rsa_env)
    now = int(time.time())
    token = mint(
        key,
        "test-kid-1",
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "some-other-client",
            "exp": now + 600,
        },
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_expired_401(rsa_env):
    client, key = make_client(rsa_env)
    now = int(time.time())
    token = mint(
        key,
        "test-kid-1",
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": now - 60,
        },
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_wrong_issuer_401(rsa_env):
    client, key = make_client(rsa_env)
    now = int(time.time())
    token = mint(
        key,
        "test-kid-1",
        {
            "sub": "user-a",
            "iss": "https://evil.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": now + 600,
        },
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_hs256_rejected_401(rsa_env):
    client, _ = make_client(rsa_env)
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": now + 600,
        },
        "weak-secret",
        algorithm="HS256",
        headers={"kid": "test-kid-1"},
    )
    resp = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


def test_unknown_kid_triggers_jwks_refresh(rsa_env):
    """D7: refresh on unknown kid — a rotated key gets served without waiting for TTL."""
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    key, jwks = rsa_env
    state = {"jwks": jwks, "fetches": 0}

    def fetcher() -> dict:
        state["fetches"] += 1
        return state["jwks"]

    provider = create_jwks_provider(fetcher=fetcher)
    app = create_app(oidc_provider=provider)
    client = TestClient(app)
    now = int(time.time())

    def token_with(kid: str) -> str:
        return jwt.encode(
            {
                "sub": "user-a",
                "iss": "https://sso.example.com",
                "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
                "exp": now + 600,
            },
            key,
            algorithm="RS256",
            headers={"kid": kid},
        )

    # First fetch populates the cache with kid 1.
    r1 = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token_with('test-kid-1')}"},
    )
    assert r1.status_code == 200
    fetches_after_first = state["fetches"]

    # Rotate: the JWKS now serves kid 2 (same key material, new kid).
    state["jwks"] = {
        "keys": [
            {**jwks["keys"][0], "kid": "test-kid-2"},
        ]
    }

    r2 = client.post(
        "/api/v1/sync/pull",
        json={"watermark": 0},
        headers={"Authorization": f"Bearer {token_with('test-kid-2')}"},
    )
    assert r2.status_code == 200
    assert state["fetches"] > fetches_after_first


def test_jwks_cache_ttl_no_refetch(rsa_env):
    """Within TTL, a cached kid does not cause refetches."""
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    key, jwks = rsa_env
    state = {"fetches": 0}

    def fetcher() -> dict:
        state["fetches"] += 1
        return jwks

    provider = create_jwks_provider(fetcher=fetcher)
    app = create_app(oidc_provider=provider)
    client = TestClient(app)
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": now + 600,
        },
        key,
        algorithm="RS256",
        headers={"kid": "test-kid-1"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(4):
        assert (
            client.post("/api/v1/sync/pull", json={"watermark": 0}, headers=headers).status_code
            == 200
        )
    assert state["fetches"] == 1


# --- JWKS hardening: negative cache for unknown kids + upstream failure handling ---


def test_repeated_unknown_kid_served_from_negative_cache():
    """DoS amplification fix: an attacker cycling an unknown kid must not force
    one synchronous upstream JWKS fetch per request. After the first miss, the
    kid is negatively cached (≥60 s) and rejected WITHOUT refetching."""
    from app.auth import create_jwks_provider

    state = {"fetches": 0}

    def fetcher() -> dict:
        state["fetches"] += 1
        return {"keys": []}

    provider = create_jwks_provider(fetcher=fetcher)
    assert provider.get_key("attacker-kid") is None
    fetches_after_first = state["fetches"]
    assert fetches_after_first >= 1

    for _ in range(5):
        assert provider.get_key("attacker-kid") is None
    assert state["fetches"] == fetches_after_first, "negative cache was ignored"


def test_negative_cache_expires_and_refetches():
    """After the negative-TTL window (≥60 s) the lookup is retried upstream."""
    from app.auth import JWKS_NEGATIVE_TTL_SECONDS, create_jwks_provider

    assert JWKS_NEGATIVE_TTL_SECONDS >= 60
    state = {"fetches": 0}

    def fetcher() -> dict:
        state["fetches"] += 1
        return {"keys": [{"kid": "k1", "kty": "RSA"}]}

    provider = create_jwks_provider(fetcher=fetcher)
    assert provider.get_key("unknown") is None
    assert state["fetches"] == 1

    # age the negative entry past the window
    for kid, ts in list(provider._negative.items()):
        provider._negative[kid] = ts - (JWKS_NEGATIVE_TTL_SECONDS + 1)
    assert provider.get_key("unknown") is None
    assert state["fetches"] == 2  # window expired → one refetch


def test_known_kid_still_resolves_after_unknown_kid_flood(rsa_env):
    """Negative-cached attacker kids never shadow a legitimate rotated-in kid."""
    from app.auth import create_jwks_provider

    key, jwks = rsa_env
    state = {"jwks": jwks}

    def fetcher() -> dict:
        return state["jwks"]

    provider = create_jwks_provider(fetcher=fetcher)
    assert provider.get_key("attacker-kid") is None  # miss → negative cache
    assert provider.get_key("attacker-kid") is None  # no refetch
    # Now the key legitimately rotates in under a new kid.
    state["jwks"] = {"keys": [{**jwks["keys"][0], "kid": "real-new-kid"}]}
    assert provider.get_key("attacker-kid") is None  # still no refetch
    fetched_key = provider.get_key("real-new-kid")
    assert fetched_key is not None


def test_unknown_kid_http_401_and_single_fetch(monkeypatch, rsa_env):
    """HTTP-level: repeated unknown-kid requests return 401 and hit upstream once."""
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    state = {"fetches": 0}

    def fetcher() -> dict:
        state["fetches"] += 1
        return {"keys": []}

    provider = create_jwks_provider(fetcher=fetcher)
    client = TestClient(create_app(oidc_provider=provider))
    token = jwt.encode(
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": int(time.time()) + 600,
        },
        rsa_env[0],
        algorithm="RS256",
        headers={"kid": "attacker-kid"},
    )
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(4):
        r = client.post("/api/v1/sync/pull", json={"watermark": 0}, headers=headers)
        assert r.status_code == 401, r.text
    assert state["fetches"] == 1


def test_unreachable_idp_yields_401_not_500(rsa_env):
    """Documented choice: a JWKS fetch failure surfaces as 401 'unknown signing
    key' (credentials cannot be validated), never a 500."""
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    def fetcher() -> dict:
        raise httpx.ConnectError("connection refused")

    provider = create_jwks_provider(fetcher=fetcher)
    client = TestClient(create_app(oidc_provider=provider))
    token = jwt.encode(
        {
            "sub": "user-a",
            "iss": "https://sso.example.com",
            "aud": "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8",
            "exp": int(time.time()) + 600,
        },
        rsa_env[0],
        algorithm="RS256",
        headers={"kid": "test-kid-1"},
    )
    r = client.post(
        "/api/v1/sync/pull", json={"watermark": 0}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401, r.text


def test_default_fetcher_failure_returns_none(monkeypatch):
    """The production default fetcher (httpx) is wrapped too — mocked httpx."""
    from app.auth import create_jwks_provider

    calls = {"n": 0}

    def fake_get(url, timeout):
        calls["n"] += 1
        raise httpx.ConnectTimeout("upstream down")

    monkeypatch.setattr(httpx, "get", fake_get)
    provider = create_jwks_provider(jwks_url="https://idp.example.com/jwks")
    assert provider.get_key("kid-a") is None
    assert calls["n"] == 1
    assert provider.get_key("kid-a") is None
    assert calls["n"] == 1  # negative cache also covers the default fetcher
