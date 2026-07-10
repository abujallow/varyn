from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

from varyn_settings import setting


PUBLIC_PATHS = {"/ping", "/health"}
OWNER_PREFIXES = (
    "/audit",
    "/safety",
    "/upload",
    "/files/",
    "/session/",
    "/health/details",
)


def security_required() -> bool:
    configured = os.getenv("VARYN_SECURITY_REQUIRED", "").strip().casefold()
    return configured in {"1", "true", "yes", "on"} or bool(os.getenv("RENDER"))


def proxy_secret() -> str:
    return os.getenv("VARYN_PROXY_SECRET", "").strip()


def is_owner_path(request: Request) -> bool:
    # /confirmations/{id} is intentionally NOT blanket owner-gated here: some
    # confirmation-gated actions (export_risk_memo) are meant to be resolvable
    # by any authenticated demo/public session. main.py's resolve_confirmation()
    # route does its own per-confirmation, action-aware owner check instead --
    # see confirmation_requires_owner() there.
    path = request.url.path
    if path.startswith(OWNER_PREFIXES):
        return True
    if path == "/heartbeat/run" or path.startswith("/heartbeat/notices/"):
        return True
    if path.startswith(("/sec/fundamentals/", "/cfpb/")):
        return request.query_params.get("refresh", "false").casefold() == "true"
    return False


async def enforce_request_security(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)

    if path == "/upload" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            max_bytes = int(setting("security.max_upload_bytes", 10 * 1024 * 1024))
            if int(content_length) > max_bytes + 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Upload exceeds the {max_bytes // (1024 * 1024)} MB limit."},
                )

    secret = proxy_secret()
    if not secret:
        if security_required():
            return JSONResponse(
                status_code=503,
                content={"detail": "Varyn security configuration is unavailable."},
            )
        request.state.access_role = "owner"
        request.state.proxy_authenticated = False
        return await call_next(request)

    supplied = request.headers.get("x-varyn-proxy-key", "")
    if not supplied or not hmac.compare_digest(supplied, secret):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized."})

    role = request.headers.get("x-varyn-role", "demo").strip().casefold()
    request.state.access_role = "owner" if role == "owner" else "demo"
    request.state.proxy_authenticated = True

    if is_owner_path(request) and request.state.access_role != "owner":
        return JSONResponse(
            status_code=403,
            content={"detail": "Owner authentication is required for this action."},
        )
    return await call_next(request)


def request_role(request: Request) -> str:
    return getattr(request.state, "access_role", "demo")
