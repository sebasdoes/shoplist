"""T005 — environment configuration with .env support."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ISSUER = "https://id.akiha.nl"
DEFAULT_AUDIENCE = "abac6ecf-d312-4ed1-a3f8-46e5958a0fe8"
DEFAULT_DB = "/data/shoplist.db"

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_env_file(path: str | Path) -> None:
    """Load KEY=VALUE lines into os.environ (existing env wins — gitignored .env support)."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


def load_dotenv(path: Path | None = None) -> None:
    _load_env_file(path or _ENV_FILE)


@dataclass(frozen=True)
class Settings:
    oidc_issuer: str
    oidc_audience: str
    shoplist_db: str
    serve_frontend_dir: str | None

    @property
    def jwks_url(self) -> str:
        return f"{self.oidc_issuer.rstrip('/')}/.well-known/jwks.json"


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        oidc_issuer=os.environ.get("OIDC_ISSUER", DEFAULT_ISSUER),
        oidc_audience=os.environ.get("OIDC_AUDIENCE", DEFAULT_AUDIENCE),
        shoplist_db=os.environ.get("SHOPLIST_DB", DEFAULT_DB),
        serve_frontend_dir=os.environ.get("SERVE_FRONTEND_DIR") or None,
    )
