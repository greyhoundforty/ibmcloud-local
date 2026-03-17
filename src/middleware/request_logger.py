"""
Request Logging Middleware — captures every API call flowing through the emulator.

This is the Traefik-dashboard-inspired piece. Every HTTP request gets logged
with timing, service classification, status code, and path. The dashboard
reads these logs to show:
    - Live request feed (like Traefik's access log panel)
    - Per-service request counts and error rates
    - Response time histograms
    - Route hit maps

Architecture note:
    This is a standard Starlette middleware. It wraps every request/response
    cycle, measures timing, and classifies which IBM Cloud service handled it.
"""

import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.state.store import store


# ── Service classification rules ─────────────────────────────────────
# Given a request path, determine which IBM Cloud service it belongs to.
# This is how the dashboard groups requests by service.

SERVICE_ROUTE_MAP = [
    # (path_prefix, service_name)
    ("/v1/vpcs", "vpc"),
    ("/v1/subnets", "vpc"),
    ("/v1/instances", "vpc"),
    ("/v1/security_groups", "vpc"),
    ("/v1/floating_ips", "vpc"),
    ("/v1/network_acls", "vpc"),
    ("/v1/public_gateways", "vpc"),
    ("/v1/keys", "vpc"),
    ("/v1/volumes", "vpc"),
    ("/v1/images", "vpc"),
    # Future providers will add their prefixes here:
    # ("/v2/cos/", "cos"),
    # ("/pcloud/v1/", "power_vs"),
    # ("/v2/kubernetes/", "iks"),
    # ("/v2/code_engine/", "code_engine"),
    ("/api/dashboard", "dashboard"),
    ("/_emulator", "emulator"),
]


def classify_service(path: str) -> str:
    """
    Determine which IBM Cloud service a request path belongs to.

    Iterates through SERVICE_ROUTE_MAP in order and returns the first match.
    Falls back to "unknown" if no prefix matches.
    """
    for prefix, service in SERVICE_ROUTE_MAP:
        if path.startswith(prefix):
            return service
    return "unknown"


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs every request/response for dashboard visibility.

    Captures:
        - Timestamp (ISO 8601)
        - HTTP method and path
        - Query parameters
        - Which service handled it
        - Response status code
        - Duration in milliseconds

    This data feeds the dashboard's live request feed and analytics.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Record the start time
        start = time.perf_counter()

        # Let the actual handler process the request
        response = await call_next(request)

        # Calculate how long it took
        duration_ms = (time.perf_counter() - start) * 1000

        # Classify which service handled this request
        service = classify_service(request.url.path)

        # Build the log entry
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": request.method,
            "path": request.url.path,
            "query": str(request.url.query) if request.url.query else "",
            "service": service,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
            # Tag error responses for dashboard highlighting
            "is_error": response.status_code >= 400,
        }

        # Store the log entry (the dashboard reads from this)
        store.log_request(entry)

        # Also add the timing as a response header (like Traefik does)
        response.headers["X-Emulator-Duration-Ms"] = str(round(duration_ms, 2))
        response.headers["X-Emulator-Service"] = service

        return response
