"""
Route Registry — Traefik-inspired service discovery and routing introspection.

This module maintains a global map of all registered providers and their
routes. It serves two purposes:

1. **Service Discovery**: When a request comes in, we can look up which
   provider handles it (useful for the middleware and dashboard).

2. **Route Introspection**: The dashboard and CLI can query this to show
   a Traefik-style routing table — all registered endpoints, which service
   owns them, their HTTP methods, etc.

The design is inspired by Traefik's router concept:
    - Each "router" (our "provider") defines entrypoints (routes)
    - Each route has a rule (path pattern + method)
    - The dashboard visualizes all routers and their rules

This is the module that powers `mise run routes` and the dashboard's
routing table view.
"""

from dataclasses import dataclass
from typing import Optional

from src.providers.base import BaseProvider


@dataclass
class RouteEntry:
    """
    A single registered API route with metadata.

    Represents one row in the Traefik-style routing table.
    """
    method: str           # HTTP method: GET, POST, PATCH, DELETE
    path: str             # URL pattern: /v1/vpcs, /v1/vpcs/{vpc_id}
    service: str          # Owning service: "vpc", "cos", "iks"
    handler_name: str     # Python function name: "list_vpcs", "create_vpc"
    description: str = "" # Optional human-readable description


@dataclass
class ServiceInfo:
    """
    Metadata about a registered service provider.

    One per provider (VPC, COS, IKS, etc.).
    """
    name: str
    description: str
    api_version: str
    api_base_url: str
    route_count: int = 0
    # Status is "active" if the provider is registered, could add health checks later
    status: str = "active"


class RouteRegistry:
    """
    Global registry of all providers and their routes.

    Providers register themselves here during app startup. The dashboard
    and CLI query this to display routing information.

    Usage:
        registry = RouteRegistry()
        registry.register_provider(vpc_provider)
        routes = registry.get_all_routes()
        services = registry.get_service_summary()
    """

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        self._routes: list[RouteEntry] = []
        self._services: dict[str, ServiceInfo] = {}

    def register_provider(self, provider: BaseProvider):
        """
        Register a service provider and index all its routes.

        Called during server startup for each enabled provider.
        After registration, the provider's routes appear in the
        dashboard and CLI route table.
        """
        service_name = provider.service_name
        self._providers[service_name] = provider

        # Extract route info from the provider's FastAPI router
        route_entries = provider.get_route_info()
        for route_info in route_entries:
            entry = RouteEntry(
                method=route_info["method"],
                path=route_info["path"],
                service=service_name,
                handler_name=route_info.get("name", ""),
            )
            self._routes.append(entry)

        # Store service-level metadata
        self._services[service_name] = ServiceInfo(
            name=service_name,
            description=provider.description,
            api_version=provider.api_version,
            api_base_url=provider.api_base_url,
            route_count=len(route_entries),
        )

    def get_all_routes(self) -> list[dict]:
        """
        Return all registered routes as dicts (for JSON serialization).

        This is what the dashboard's routing table displays.
        Returns a list sorted by path, then method.
        """
        sorted_routes = sorted(self._routes, key=lambda r: (r.path, r.method))
        return [
            {
                "method": r.method,
                "path": r.path,
                "service": r.service,
                "handler": r.handler_name,
            }
            for r in sorted_routes
        ]

    def get_service_summary(self) -> list[dict]:
        """
        Return a summary of all registered services (for dashboard overview).

        Each entry includes the service name, status, route count, etc.
        """
        return [
            {
                "name": info.name,
                "description": info.description,
                "api_version": info.api_version,
                "api_base_url": info.api_base_url,
                "route_count": info.route_count,
                "status": info.status,
            }
            for info in self._services.values()
        ]

    def get_routes_for_service(self, service_name: str) -> list[dict]:
        """Return routes belonging to a specific service."""
        return [
            {
                "method": r.method,
                "path": r.path,
                "handler": r.handler_name,
            }
            for r in self._routes
            if r.service == service_name
        ]

    def match_route(self, method: str, path: str) -> Optional[RouteEntry]:
        """
        Find which route matches a given method + path.
        Used by middleware for request classification.

        Note: This does simple prefix matching. FastAPI's own router
        handles the actual dispatch — this is just for introspection.
        """
        for route in self._routes:
            if route.method == method:
                # Simple pattern matching: /v1/vpcs/{vpc_id} matches /v1/vpcs/abc123
                route_parts = route.path.strip("/").split("/")
                path_parts = path.strip("/").split("/")

                if len(route_parts) != len(path_parts):
                    continue

                match = True
                for rp, pp in zip(route_parts, path_parts):
                    if rp.startswith("{") and rp.endswith("}"):
                        continue  # Wildcard segment, always matches
                    if rp != pp:
                        match = False
                        break

                if match:
                    return route
        return None


# ── Module-level singleton (like the state store) ────────────────────
registry = RouteRegistry()
