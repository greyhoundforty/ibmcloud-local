"""
Bearer Token Auth Middleware.

Validates JWT tokens on every request to VPC/service endpoints.

Modes (IBMCLOUD_LOCAL_AUTH env var):
    permissive (default) — token must be present and structurally valid
                           (three base64 segments), but signature is NOT checked.
    strict               — full RS256 signature verification + expiry check.

Bypass paths (no token required):
    /_emulator/*    — control plane (reset, health, state dump)
    /api/dashboard/* — dashboard API
    /identity/*     — IAM token and JWKS endpoints themselves

The middleware references the IamProvider's key pair via a module-level
accessor set during server startup.
"""

import os
import time

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


# Set by server.py after IamProvider is instantiated
_iam_provider = None


def set_iam_provider(provider) -> None:
    global _iam_provider
    _iam_provider = provider


_BYPASS_PREFIXES = (
    "/_emulator/",
    "/api/dashboard",
    "/api/dashboard/",
    "/identity/",
)


def _is_bypass_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in _BYPASS_PREFIXES)


def _is_structurally_valid_jwt(token: str) -> bool:
    """Check token is three non-empty base64url segments."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return False
    return all(len(p) > 0 for p in parts)


def _error_401(message: str = "Authorization required") -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"errors": [{"code": "not_authorized", "message": message}]},
    )


def _error_403(action: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"errors": [{"code": "not_authorized", "message": f"Subject does not have permission to perform action '{action}'."}]},
    )


def _check_authz(iam_id: str, method: str, path: str) -> JSONResponse | None:
    """
    Return a 403 JSONResponse if the identity lacks permission, or None to allow.
    Called only when IBMCLOUD_LOCAL_AUTHZ=enforce.
    """
    policy_file = os.environ.get("IBMCLOUD_LOCAL_POLICY_FILE", "")
    if not policy_file:
        return None  # no policy file configured → fail open

    from src.iam.vpc_action_map import resolve_action
    from src.iam.policy_store import PolicyStore

    action = resolve_action(method, path)
    if action is None:
        return None  # unmapped path → fail open

    try:
        ps = PolicyStore.load_from_file(policy_file)
    except (FileNotFoundError, ValueError):
        return None  # unreadable policy → fail open

    if not ps.allows(iam_id, action):
        return _error_403(action)
    return None


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Bypass auth for control plane, dashboard, and identity endpoints
        if _is_bypass_path(path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return _error_401("Missing or invalid Authorization header.")

        token = auth_header[len("Bearer "):].strip()
        if not token:
            return _error_401("Bearer token is empty.")

        if not _is_structurally_valid_jwt(token):
            return _error_401("Token is not a valid JWT (expected header.payload.signature).")

        auth_mode = os.environ.get("IBMCLOUD_LOCAL_AUTH", "permissive")

        if auth_mode == "strict" and _iam_provider is not None:
            # Full RS256 verification + expiry
            try:
                public_key = _iam_provider.private_key.public_key()
                payload = jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                    options={"verify_exp": True},
                )
            except jwt.ExpiredSignatureError:
                return _error_401("Token has expired.")
            except jwt.InvalidTokenError as exc:
                return _error_401(f"Invalid token: {exc}")
        else:
            # Permissive: check expiry from payload without signature verification
            try:
                payload = jwt.decode(
                    token,
                    options={"verify_signature": False, "verify_exp": False},
                    algorithms=["RS256"],
                )
                exp = payload.get("exp")
                if exp is not None and int(time.time()) > exp:
                    return _error_401("Token has expired.")
            except Exception:
                return _error_401("Token payload could not be decoded.")

        # Policy enforcement (IBMCLOUD_LOCAL_AUTHZ=enforce)
        if os.environ.get("IBMCLOUD_LOCAL_AUTHZ", "off") == "enforce":
            iam_id = payload.get("iam_id") or payload.get("sub", "")
            denial = _check_authz(iam_id, request.method, path)
            if denial is not None:
                return denial

        return await call_next(request)
