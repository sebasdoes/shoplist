"""Shared fixtures for the server test suite."""

import base64
import time

import jwt
import pytest

AUD = "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8"
ISS = "https://sso.example.com"
KID = "test-kid-1"


def b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture(scope="session")
def rsa():
    from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

    key = generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": KID,
                "alg": "RS256",
                "use": "sig",
                "n": b64url_uint(nums.n),
                "e": b64url_uint(nums.e),
            }
        ]
    }
    return key, jwks


@pytest.fixture
def client(tmp_path, rsa):
    from fastapi.testclient import TestClient

    from app.auth import create_jwks_provider
    from app.main import create_app

    key, jwks = rsa
    provider = create_jwks_provider(fetcher=lambda: jwks)
    app = create_app(oidc_provider=provider, db_path=str(tmp_path / "test.db"))
    return TestClient(app)


@pytest.fixture
def auth(rsa):
    """Return a helper building valid Authorization headers (minted from the test RSA pair)."""

    key, _ = rsa

    def _auth(sub: str = "user-a", name: str = "User A", **overrides) -> dict:
        now = int(time.time())
        claims = {"sub": sub, "name": name, "iss": ISS, "aud": AUD, "iat": now, "exp": now + 600}
        claims.update(overrides)
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})
        return {"Authorization": f"Bearer {token}"}

    return _auth


@pytest.fixture(autouse=True)
def _sandbox_db(tmp_path, monkeypatch):
    """Every test runs against a throwaway DB.

    The app's default DB path is /data/shoplist.db; without this override a
    root shell happily mkdirs /data (masking the issue locally) while CI —
    where the runner cannot write / — fails the whole suite.
    """
    monkeypatch.setenv("SHOPLIST_DB", str(tmp_path / "test.db"))
