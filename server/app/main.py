"""T013/T019 — FastAPI app: routers under /api/v1, unauthenticated /health, optional SPA serving."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from . import db, sync
from .auth import AuthUser, create_jwks_provider, make_auth_dependency, verify_token
from .config import Settings, get_settings
from .history import history_delete, history_list
from .models import PullRequest, PullResponse, PushRequest, PushResponse
from .sync import SyncError


def create_app(
    oidc_provider=None,
    db_path: str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    provider = oidc_provider or create_jwks_provider(jwks_url=settings.jwks_url)
    auth_dep = make_auth_dependency(provider, settings.oidc_issuer, settings.oidc_audience)

    app = FastAPI(title="Shoplist sync server", version="0.1.0")
    app.state.settings = settings
    app.state.db_path = db_path or settings.shoplist_db
    app.state.auth_dep = auth_dep

    api = APIRouter(prefix="/api/v1")

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = db.connect(app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @api.post("/sync/push", response_model=PushResponse)
    def post_push(
        request: PushRequest,
        conn: sqlite3.Connection = Depends(get_conn),
        user: AuthUser = Depends(auth_dep),
    ) -> PushResponse:
        try:
            return sync.push(conn, request, user)
        except SyncError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    @api.post("/sync/pull", response_model=PullResponse)
    def post_pull(
        request: PullRequest,
        conn: sqlite3.Connection = Depends(get_conn),
        user: AuthUser = Depends(auth_dep),
    ) -> PullResponse:
        return sync.pull(conn, request)

    @api.get("/history", response_model=None)
    def get_history(
        conn: sqlite3.Connection = Depends(get_conn),
        user: AuthUser = Depends(auth_dep),
    ):
        return history_list(conn)

    @api.delete("/history/{history_id}", status_code=204)
    def delete_history(
        history_id: str,
        conn: sqlite3.Connection = Depends(get_conn),
        user: AuthUser = Depends(auth_dep),
    ):
        history_delete(conn, history_id)  # raises 404 when unknown (FR-011 hard delete)

    app.include_router(api)

    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc: RequestValidationError):
        # contracts/api.md: errors are {"detail": "<string>"}
        parts = []
        for error in exc.errors():
            loc = ".".join(str(piece) for piece in error.get("loc", []) if piece != "body")
            msg = error.get("msg", "invalid value")
            parts.append(f"{loc}: {msg}" if loc else msg)
        return JSONResponse(status_code=422, content={"detail": "; ".join(parts)})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/v1/config")
    def public_config() -> dict:
        """Unauthenticated on purpose: the PWA must learn the deploy'd OIDC
        issuer/audience at runtime (public repo never bakes real URLs)."""
        return {"issuer": settings.oidc_issuer, "audience": settings.oidc_audience}

    @app.post("/api/v1/log")
    def client_log(body: dict) -> dict:
        """Diagnostic beacon: browser-side errors land in the server log.
        Unauthenticated like /config; stores nothing, prints a truncated line."""
        msg = str(body.get("msg") or "empty")[:2000]
        print(f"[client-log] {msg}", flush=True)
        return {"ok": True}

    @app.get("/api/v1/events")
    async def events(request: Request, token: str = Query(...)):
        """Server push (SSE): fires 'changed' whenever any revision advances, so
        the other device's open PWA pulls within ~0.5 s instead of polling.
        EventSource cannot set headers, so the JWT arrives via ?token= (same
        validation as the bearer path). No DATA flows here — a changed event
        only says 'pull now'; /sync/pull still enforces auth."""
        verify_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            provider,
            settings.oidc_issuer,
            settings.oidc_audience,
        )

        async def stream():
            conn = db.connect(app.state.db_path)
            try:
                last = db.current_rev(conn)
                yield "data: connected\n\n"
                idle_ticks = 0
                while True:
                    await asyncio.sleep(0.5)
                    rev = db.current_rev(conn)
                    if rev != last:
                        last = rev
                        idle_ticks = 0
                        yield "data: changed\n\n"
                    else:
                        idle_ticks += 1
                        if idle_ticks >= 40:  # ~20 s heartbeat through proxies
                            idle_ticks = 0
                            yield ": keep-alive\n\n"
            finally:
                conn.close()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    serve_dir = settings.serve_frontend_dir
    if serve_dir:
        root = Path(serve_dir)

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # SingleFileResponse fallback to index.html; /api routes and /health take precedence
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_file() and root.resolve() in candidate.parents:
                return FileResponse(candidate)
            index = root / "index.html"
            if index.is_file():
                return FileResponse(index)
            raise HTTPException(status_code=404, detail="frontend bundle not found")

    return app


app = create_app()
