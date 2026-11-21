"""T007 (written FIRST, TDD) — JWT auth validation per research.md D7.

Covers: valid token → 200; invalid signature, wrong aud, expired, missing header
→ 401; JWKS cache TTL + refresh on unknown kid.
"""

import base64
import time

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
            "name": "Sebastiaan",
            "iss": "https://id.akiha.nl",
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
            "iss": "https://id.akiha.nl",
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
            "iss": "https://id.akiha.nl",
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
            "iss": "https://id.akiha.nl",
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
            "iss": "https://id.akiha.nl",
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
                "iss": "https://id.akiha.nl",
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
            "iss": "https://id.akiha.nl",
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
