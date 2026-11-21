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
# Hardening: remembering a failed kid lookup for this window prevents an
# attacker cycling unknown `kid` values from forcing one synchronous upstream
# JWKS fetch per request (DoS amplification). ≥60 s per the hardening spec.
JWKS_NEGATIVE_TTL_SECONDS = 60


@dataclass
class AuthUser:
    subject: str
    name: str | None


class JWKSProvider:
    """Fetches and caches the issuer JWKS; refreshes on TTL expiry or unknown kid.

    Hardening: a kid that is still unknown after a refresh is negatively cached
    for `negative_ttl` seconds — repeated requests with that kid are rejected
    WITHOUT refetching upstream. Known kids stay cached for the normal TTL; a
    kid rotated out of the JWKS becomes unknown again after its refresh misses.

    Documented failure choice: a JWKS fetch error (unreachable IdP) yields None
    → 401 'unknown signing key' (never a 500): credentials cannot be validated,
    which is squarely an authentication failure. A known kid is still served
    stale when the TTL refresh fails.
    """

    _NEGATIVE_CACHE_MAX = 1024  # bound for attacker-spread kid floods

    def __init__(
        self,
        fetcher: Callable[[], dict],
        ttl: float = JWKS_TTL_SECONDS,
        negative_ttl: float = JWKS_NEGATIVE_TTL_SECONDS,
    ):
        self._fetcher = fetcher
        self._ttl = ttl
        self._negative_ttl = negative_ttl
        self._keys: dict[str, dict] = {}
        self._fetched_at = 0.0
        self._fetched_once = False
        self._negative: dict[str | None, float] = {}  # kid → monotonic ts of the failed lookup
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        document = self._fetcher()
        self._keys = {k.get("kid"): k for k in document.get("keys", []) if k.get("kid")}
        self._fetched_at = time.monotonic()
        self._fetched_once = True

    def _known_key(self, kid: str):
        return jwt.PyJWK.from_dict(self._keys[kid]).key

    def _prune_negative(self, now: float) -> None:
        """Drop expired entries and cap the map so kid floods cannot grow it."""
        for cached_kid, ts in list(self._negative.items()):
            if (now - ts) >= self._negative_ttl:
                del self._negative[cached_kid]
        if len(self._negative) > self._NEGATIVE_CACHE_MAX:
            freshest = sorted(self._negative.items(), key=lambda kv: kv[1])
            for cached_kid, _ts in freshest[: len(self._negative) - self._NEGATIVE_CACHE_MAX]:
                del self._negative[cached_kid]

    def get_key(self, kid: str | None):
        with self._lock:
            now = time.monotonic()
            if kid in self._keys:
                expired = self._fetched_once and (now - self._fetched_at) >= self._ttl
                if not expired:
                    return self._known_key(kid)
                try:
                    self._refresh()
                except Exception:  # noqa: BLE001 — unreachable IdP: serve the stale key
                    return self._known_key(kid) if kid in self._keys else None
                if kid in self._keys:
                    return self._known_key(kid)
                # The key was rotated out during the TTL refresh → unknown below.

            # Unknown kid: within the negative-cache window, reject WITHOUT refetching.
            cached_at = self._negative.get(kid)
            if cached_at is not None and (now - cached_at) < self._negative_ttl:
                return None

            try:
                self._refresh()
            except Exception:  # noqa: BLE001 — unreachable IdP → 401, never 500
                # Also negatively cache the kid: during an IdP outage the failed
                # fetch must not amplify into one upstream attempt per request.
                self._prune_negative(now)
                self._negative[kid] = now
                return None
            if kid in self._keys:
                self._negative.pop(kid, None)
                return self._known_key(kid)
            self._prune_negative(now)
            self._negative[kid] = now
            return None


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
