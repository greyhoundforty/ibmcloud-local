"""
IBM Cloud Local Emulator — Main Server

This is the entrypoint. It creates the FastAPI app, registers all service
providers, attaches the request logging middleware, and serves the
dashboard API endpoints.

Start it with:
    mise run dev          (development mode with auto-reload)
    mise run start        (production mode via CLI)
    uvicorn src.server:app --port 4515

Then point your IBM Cloud SDK/CLI at it:
    export IBMCLOUD_VPC_API_ENDPOINT=http://localhost:4515

Architecture:
    ┌─────────────────────────────────────────────┐
    │  FastAPI App                                 │
    │  ├── RequestLoggerMiddleware (logs all reqs) │
    │  ├── CORS Middleware (for dashboard UI)      │
    │  │                                           │
    │  ├── /v1/vpcs, /v1/subnets, ...  (VPC)     │
    │  ├── /v2/cos/...                 (COS)  ◄── future
    │  ├── /pcloud/v1/...              (PowerVS)◄── future
    │  │                                           │
    │  ├── /api/dashboard/...   (dashboard API)    │
    │  └── /_emulator/...       (control plane)    │
    │                                              │
    │  StateStore (in-memory)                      │
    │  RouteRegistry (introspection)               │
    └─────────────────────────────────────────────┘
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.middleware.auth import BearerTokenMiddleware, set_iam_provider
from src.middleware.request_logger import RequestLoggerMiddleware
from src.providers.iam import IamProvider
from src.providers.load_balancer import LoadBalancerProvider
from src.providers.network_acl import NetworkAclProvider
from src.providers.public_gateway import PublicGatewayProvider
from src.providers.resource_manager import ResourceManagerProvider, ensure_default_resource_group
from src.providers.vpc import VpcProvider
from src.routing import registry
from src.state.store import store


# ── Providers created once at module import time ─────────────────────
# Route registration happens here rather than inside lifespan so that
# repeated TestClient instantiation (as happens in the test suite) does
# not accumulate duplicate routes on the shared app object.

_iam_provider = IamProvider()
set_iam_provider(_iam_provider)

_providers = [
    _iam_provider,
    ResourceManagerProvider(),
    VpcProvider(),
    NetworkAclProvider(),
    PublicGatewayProvider(),
    LoadBalancerProvider(),
    # Future providers go here:
    # CosProvider(),
    # PowerVsProvider(),
    # IksProvider(),
    # CodeEngineProvider(),
]


# ── Lifespan handler: startup/shutdown side effects only ─────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once when the server starts and once when it stops.

    Routes are already registered at module level. This handler handles
    state seeding, optional disk persistence, and startup logging.
    """
    # ── Startup ──────────────────────────────────────────────────
    print("=" * 60)
    print("  IBM Cloud Local Emulator")
    print(f"  Port: {os.environ.get('IBMCLOUD_LOCAL_PORT', '4515')}")
    print("=" * 60)

    ensure_default_resource_group()

    for provider in _providers:
        print(f"  ✓ {provider.service_name:12s} → {len(provider.get_route_info())} routes")

    print("-" * 60)
    print(f"  Dashboard: http://localhost:{os.environ.get('IBMCLOUD_LOCAL_PORT', '4515')}/api/dashboard")
    print(f"  Routes:    http://localhost:{os.environ.get('IBMCLOUD_LOCAL_PORT', '4515')}/api/dashboard/routes")
    print("=" * 60)

    persistence = os.environ.get("IBMCLOUD_LOCAL_PERSISTENCE", "memory")
    if persistence == "disk":
        store.restore_from_disk("/tmp/ibmcloud-local-state.json")
        print("  Restored state from disk snapshot")

    yield  # ← Server is running, handling requests

    # ── Shutdown ─────────────────────────────────────────────────
    if persistence == "disk":
        store.snapshot_to_disk("/tmp/ibmcloud-local-state.json")
        print("  Saved state snapshot to disk")

    print("  IBM Cloud Local Emulator stopped.")


# ── Create the FastAPI app ───────────────────────────────────────────
app = FastAPI(
    title="IBM Cloud Local Emulator",
    description="Local emulator for IBM Cloud services — like LocalStack, but for IBM Cloud",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────
# CORS: Allow the dashboard UI (served separately or embedded) to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In a real deployment you'd lock this down
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth: validate bearer tokens on all non-bypass endpoints
app.add_middleware(BearerTokenMiddleware)
# Request logging: capture every request for the dashboard's live feed
app.add_middleware(RequestLoggerMiddleware)

# ── Register all provider routes ──────────────────────────────────────
for _p in _providers:
    app.include_router(_p.router)
    registry.register_provider(_p)


# ══════════════════════════════════════════════════════════════════════
# DASHBOARD API — Traefik-inspired introspection endpoints
# ══════════════════════════════════════════════════════════════════════
# These endpoints power the dashboard UI. They expose:
#   - Route table (all registered API routes)
#   - Service health/status
#   - Request logs (live activity feed)
#   - State summaries (resource counts per namespace)


@app.get("/api/dashboard")
async def dashboard_overview():
    """
    Dashboard home — shows overall emulator status.
    Returns service list, total route count, resource counts, etc.
    """
    return {
        "emulator": "ibmcloud-local",
        "version": "0.1.0",
        "services": registry.get_service_summary(),
        "total_routes": len(registry.get_all_routes()),
        "state_summary": store.namespaces(),
    }


@app.get("/api/dashboard/routes")
async def dashboard_routes():
    """
    Routing table — Traefik-style list of all registered API routes.

    Returns every endpoint the emulator handles, grouped by service,
    with method, path pattern, and handler name.
    """
    routes = registry.get_all_routes()
    services = registry.get_service_summary()

    return {
        "routes": routes,
        "services": services,
        "total_routes": len(routes),
    }


@app.get("/api/dashboard/requests")
async def dashboard_requests(limit: int = 50):
    """
    Live request feed — most recent API calls.

    Returns the last N requests with timing, status codes, and service tags.
    The dashboard polls this to show a real-time activity stream.
    """
    return {
        "requests": store.get_request_log(limit=limit),
        "total_logged": len(store._request_log),
    }


@app.get("/api/dashboard/services/{service_name}")
async def dashboard_service_detail(service_name: str):
    """
    Deep dive into a specific service — its routes and resource counts.
    """
    routes = registry.get_routes_for_service(service_name)
    if not routes:
        return JSONResponse(
            status_code=404,
            content={"error": f"Service '{service_name}' not found"},
        )

    resource_counts = {}
    for ns, count in store.namespaces().items():
        resource_counts[ns] = count

    return {
        "service": service_name,
        "routes": routes,
        "route_count": len(routes),
        "resources": resource_counts,
    }


# ══════════════════════════════════════════════════════════════════════
# EMULATOR CONTROL PLANE — manage the emulator itself
# ══════════════════════════════════════════════════════════════════════

@app.post("/_emulator/reset")
async def reset_state():
    """
    Reset all emulator state. Useful between test runs.

    curl -X POST http://localhost:4515/_emulator/reset
    """
    store.reset()
    return {"status": "ok", "message": "All state has been reset"}


@app.post("/_emulator/reset/{namespace}")
async def reset_namespace(namespace: str):
    """Reset state for a specific namespace (e.g., 'vpcs', 'instances')."""
    store.reset(namespace)
    return {"status": "ok", "message": f"State for '{namespace}' has been reset"}


@app.get("/_emulator/state")
async def dump_state():
    """
    Dump the entire emulator state (useful for debugging).
    WARNING: Can be large if you have many resources.
    """
    return {
        "namespaces": store.namespaces(),
        "data": store._data,
    }


@app.get("/_emulator/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "healthy", "services": list(registry._services.keys())}
