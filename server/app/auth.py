"""T006 — JWT auth: JWKS cache (1 h TTL, refresh on unknown kid) + FastAPI dependency.

Per research.md D7: verify RS256, iss, aud, exp; 401 on any failure.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWKS_TTL_SECONDS = 3600  # 1 h cache


@dataclass
class AuthUser:
    subject: str
    name: str | None


class JWKSProvider:
    """Fetches and caches the issuer JWKS; refreshes on TTL expiry or unknown kid."""

    def __init__(self, fetcher: Callable[[], dict], ttl: float = JWKS_TTL_SECONDS):
        self._fetcher = fetcher
        self._ttl = ttl
        self._keys: dict[str, dict] = {}
        self._fetched_at = 0.0
        self._fetched_once = False
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        document = self._fetcher()
        self._keys = {k.get("kid"): k for k in document.get("keys", []) if k.get("kid")}
        self._fetched_at = time.monotonic()
        self._fetched_once = True

    def get_key(self, kid: str | None):
        with self._lock:
            known = kid in self._keys
            expired = self._fetched_once and (time.monotonic() - self._fetched_at) >= self._ttl
            if not known or expired:
                self._refresh()
                known = kid in self._keys
            if not known:
                return None
            return jwt.PyJWK.from_dict(self._keys[kid]).key


def _default_fetcher(jwks_url: str) -> Callable[[], dict]:
    def fetch() -> dict:
        resp = httpx.get(jwks_url, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    return fetch


def create_jwks_provider(
    fetcher: Callable[[], dict] | None = None, jwks_url: str | None = None
) -> JWKSProvider:
    if fetcher is None:
        if jwks_url is None:
            raise ValueError("either fetcher or jwks_url is required")
        fetcher = _default_fetcher(jwks_url)
    return JWKSProvider(fetcher)


_bearer = HTTPBearer(auto_error=False)


def verify_token(
    credentials: HTTPAuthorizationCredentials | None,
    provider: JWKSProvider,
    issuer: str,
    audience: str,
) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    if header.get("alg") != "RS256":
        raise HTTPException(status_code=401, detail="invalid algorithm")
    key = provider.get_key(header.get("kid"))
    if key is None:
        raise HTTPException(status_code=401, detail="unknown signing key")
    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token") from None
    return AuthUser(
        subject=payload.get("sub") or "",
        name=payload.get("name") or payload.get("preferred_username"),
    )


def make_auth_dependency(provider: JWKSProvider, issuer: str, audience: str):
    def dependency(
        request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
    ) -> AuthUser:
        return verify_token(credentials, provider, issuer, audience)

    return dependency
