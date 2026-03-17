"""
Base Provider — abstract base class for all IBM Cloud service emulators.

Every service (VPC, COS, IKS, etc.) subclasses BaseProvider and registers
its routes with the FastAPI app. This is analogous to LocalStack's
"ServiceProvider" pattern.

The provider pattern gives us:
    1. Consistent route registration across services
    2. A place to hook in service-specific initialization
    3. A clean way for the dashboard to discover what services are available
    4. Standardized error responses matching IBM Cloud API error format
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from src.state.store import store


class BaseProvider:
    """
    Abstract base for IBM Cloud service providers.

    Subclasses must define:
        - service_name: str  (e.g., "vpc", "cos", "iks")
        - api_version: str   (e.g., "v1", "v2")
        - description: str   (human-readable, shown in dashboard)
        - register_routes()  (add FastAPI routes to self.router)

    Usage:
        class VpcProvider(BaseProvider):
            service_name = "vpc"
            api_version = "v1"
            ...

        provider = VpcProvider()
        app.include_router(provider.router)
    """

    # Subclasses override these
    service_name: str = "unknown"
    api_version: str = "v1"
    description: str = "Base service provider"
    # Tracks which IBM Cloud API endpoints this provider covers
    api_base_url: str = ""

    def __init__(self):
        # Each provider gets its own FastAPI router
        # The prefix determines the URL path, e.g., /v1/vpcs, /v1/subnets
        self.router = APIRouter(tags=[self.service_name])
        self.store = store  # Reference to the shared state store

        # Let the subclass register its specific routes
        self.register_routes()

    def register_routes(self):
        """
        Override this to add FastAPI route handlers to self.router.

        Example:
            @self.router.get("/v1/vpcs")
            async def list_vpcs():
                ...
        """
        raise NotImplementedError("Providers must implement register_routes()")

    def get_route_info(self) -> list[dict]:
        """
        Return metadata about all routes this provider handles.
        Used by the dashboard and the `ibmcloud-local routes` CLI command.

        Returns a list like:
            [
                {"method": "GET", "path": "/v1/vpcs", "name": "list_vpcs"},
                {"method": "POST", "path": "/v1/vpcs", "name": "create_vpc"},
                ...
            ]
        """
        routes = []
        for route in self.router.routes:
            # FastAPI routes have a .methods set and .path string
            if hasattr(route, "methods"):
                for method in route.methods:
                    routes.append({
                        "method": method,
                        "path": route.path,
                        "name": getattr(route, "name", ""),
                        "service": self.service_name,
                    })
        return routes

    # ── Standard IBM Cloud error responses ───────────────────────────
    # The real IBM Cloud API returns errors in a consistent format:
    # { "errors": [{ "code": "not_found", "message": "...", "more_info": "..." }] }

    @staticmethod
    def error_response(status_code: int, code: str, message: str) -> JSONResponse:
        """
        Return a JSON error matching IBM Cloud's error envelope format.

        Args:
            status_code: HTTP status (404, 400, 409, etc.)
            code: IBM-style error code ("not_found", "invalid_request", etc.)
            message: Human-readable description
        """
        return JSONResponse(
            status_code=status_code,
            content={
                "errors": [
                    {
                        "code": code,
                        "message": message,
                        "more_info": "https://github.com/your-org/ibmcloud-local",
                    }
                ]
            },
        )

    @staticmethod
    def not_found(resource_type: str, resource_id: str) -> JSONResponse:
        """Convenience: 404 Not Found for a specific resource."""
        return BaseProvider.error_response(
            404, "not_found", f"{resource_type} with id '{resource_id}' not found"
        )

    @staticmethod
    def collection_response(
        resources: list[dict],
        collection_name: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """
        Wrap a list of resources in IBM Cloud's standard collection envelope.

        IBM Cloud list endpoints return:
        {
            "<collection_name>": [...],
            "total_count": N,
            "limit": 50,
            "offset": 0,
            "first": {"href": "..."},
            "next": {"href": "..."}  # only if there are more pages
        }
        """
        total = len(resources)
        paged = resources[offset : offset + limit]

        response = {
            collection_name: paged,
            "total_count": total,
            "limit": limit,
            "offset": offset,
            "first": {"href": f"/{collection_name}?limit={limit}"},
        }

        # Include "next" link if there are more pages
        if offset + limit < total:
            response["next"] = {
                "href": f"/{collection_name}?limit={limit}&start={offset + limit}"
            }

        return response
