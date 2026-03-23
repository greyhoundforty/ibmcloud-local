"""
Transit Gateway Provider.

Emulates the IBM Cloud Transit Gateway API:
    https://cloud.ibm.com/apidocs/transit-gateway

Implemented endpoints:
    GET    /v1/transit_gateways
    POST   /v1/transit_gateways
    GET    /v1/transit_gateways/{id}
    PATCH  /v1/transit_gateways/{id}
    DELETE /v1/transit_gateways/{id}
    GET    /v1/transit_gateways/{id}/connections
    POST   /v1/transit_gateways/{id}/connections
    GET    /v1/transit_gateways/{id}/connections/{conn_id}
    DELETE /v1/transit_gateways/{id}/connections/{conn_id}
    GET    /v1/connections   (global — all connections across all gateways)

All endpoints require the 'version' query parameter (YYYY-MM-DD); the
emulator accepts any non-empty value without date validation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Query
from fastapi.responses import JSONResponse

from src.models.transit_gateway import (
    TransitGatewayCreate,
    TransitGatewayUpdate,
    TransitGatewayConnectionCreate,
)
from src.providers.base import BaseProvider
from src.state.store import store

REGION_PREFIX = "r006"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_crn(resource_type: str, resource_id: str) -> str:
    return f"crn:v1:bluemix:public:transit:{resource_type}:a/local-emulator::{resource_type}:{resource_id}"


class TransitGatewayProvider(BaseProvider):
    service_name = "transit_gateway"
    api_version = "v1"
    description = "Transit Gateway service"

    def register_routes(self):
        self.router.get("/v1/transit_gateways")(self.list_gateways)
        self.router.post("/v1/transit_gateways")(self.create_gateway)
        self.router.get("/v1/transit_gateways/{tgw_id}")(self.get_gateway)
        self.router.patch("/v1/transit_gateways/{tgw_id}")(self.update_gateway)
        self.router.delete("/v1/transit_gateways/{tgw_id}")(self.delete_gateway)
        self.router.get("/v1/transit_gateways/{tgw_id}/connections")(self.list_connections)
        self.router.post("/v1/transit_gateways/{tgw_id}/connections")(self.create_connection)
        self.router.get("/v1/transit_gateways/{tgw_id}/connections/{conn_id}")(self.get_connection)
        self.router.delete("/v1/transit_gateways/{tgw_id}/connections/{conn_id}")(self.delete_connection)
        self.router.get("/v1/connections")(self.list_all_connections)

    # ── helpers ───────────────────────────────────────────────────────

    def _get_tgw_or_404(self, tgw_id: str):
        tgw = store.get("transit_gateways", tgw_id)
        if not tgw:
            return None, self.not_found("TransitGateway", tgw_id)
        return tgw, None

    def _get_conn_or_404(self, tgw_id: str, conn_id: str):
        conn = store.get(f"tgw_connections_{tgw_id}", conn_id)
        if not conn:
            return None, self.not_found("TransitGatewayConnection", conn_id)
        return conn, None

    def _tgw_response(self, tgw: dict) -> dict:
        """Return a copy with 'global_routing' key renamed to 'global'."""
        d = dict(tgw)
        d["global"] = d.pop("global_routing", False)
        return d

    def _connection_count(self, tgw_id: str) -> int:
        return len(store.list("tgw_connections_%s" % tgw_id))

    # ── Transit Gateway CRUD ──────────────────────────────────────────

    async def list_gateways(self, version: str = Query(...)):
        gateways = [self._tgw_response(g) for g in store.list("transit_gateways")]
        return {
            "transit_gateways": gateways,
            "limit": 50,
            "first": {"href": "/v1/transit_gateways?limit=50"},
        }

    async def create_gateway(self, body: TransitGatewayCreate, version: str = Query(...)):
        if not body.name:
            return self.error_response(400, "missing_field", "name is required")
        if not body.location:
            return self.error_response(400, "missing_field", "location is required")

        # Duplicate name check
        existing = store.list("transit_gateways")
        if any(g["name"] == body.name for g in existing):
            return self.error_response(409, "already_exists", f"A transit gateway named '{body.name}' already exists.")

        tgw_id = store.generate_id(REGION_PREFIX)
        crn = _make_crn("transit-gateway", tgw_id)
        now = _now()
        tgw = {
            "id": tgw_id,
            "crn": crn,
            "name": body.name,
            "location": body.location,
            "global_routing": body.global_routing,
            "status": "available",
            "created_at": now,
            "updated_at": now,
        }
        store.put("transit_gateways", tgw_id, tgw)
        return JSONResponse(status_code=201, content=self._tgw_response(tgw))

    async def get_gateway(self, tgw_id: str, version: str = Query(...)):
        tgw, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err
        tgw["connection_count"] = self._connection_count(tgw_id)
        return self._tgw_response(tgw)

    async def update_gateway(self, tgw_id: str, body: TransitGatewayUpdate, version: str = Query(...)):
        tgw, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err
        if body.name is not None:
            tgw["name"] = body.name
        if body.global_routing is not None:
            tgw["global_routing"] = body.global_routing
        tgw["updated_at"] = _now()
        store.put("transit_gateways", tgw_id, tgw)
        return self._tgw_response(tgw)

    async def delete_gateway(self, tgw_id: str, version: str = Query(...)):
        tgw, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err

        if self._connection_count(tgw_id) > 0:
            return self.error_response(
                409, "connection_exists",
                "Transit gateway has connections. Delete all connections before deleting the gateway."
            )

        store.delete("transit_gateways", tgw_id)
        return JSONResponse(status_code=204, content=None)

    # ── Connection CRUD ───────────────────────────────────────────────

    async def list_connections(self, tgw_id: str, version: str = Query(...)):
        _, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err
        connections = store.list(f"tgw_connections_{tgw_id}")
        return {
            "connections": connections,
            "limit": 50,
            "total_count": len(connections),
            "first": {"href": f"/v1/transit_gateways/{tgw_id}/connections?limit=50"},
        }

    async def create_connection(
        self, tgw_id: str, body: TransitGatewayConnectionCreate, version: str = Query(...)
    ):
        _, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err

        if not body.network_type:
            return self.error_response(400, "missing_field", "network_type is required")

        # Duplicate network_id check within this gateway
        if body.network_id:
            existing = store.list(f"tgw_connections_{tgw_id}")
            if any(c.get("network_id") == body.network_id for c in existing):
                return self.error_response(
                    409, "already_attached",
                    f"Network '{body.network_id}' is already attached to this transit gateway."
                )

        conn_id = store.generate_id(REGION_PREFIX)
        now = _now()
        conn = {
            "id": conn_id,
            "name": body.name or f"conn-{conn_id[:8]}",
            "network_type": body.network_type,
            "network_id": body.network_id,
            "status": "attached",
            "request_status": "approved",
            "created_at": now,
            "updated_at": now,
        }
        if body.zone:
            conn["zone"] = body.zone.model_dump()
        if body.prefix_filters_default:
            conn["prefix_filters_default"] = body.prefix_filters_default

        store.put(f"tgw_connections_{tgw_id}", conn_id, conn)
        return JSONResponse(status_code=201, content=conn)

    async def get_connection(self, tgw_id: str, conn_id: str, version: str = Query(...)):
        _, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err
        conn, err = self._get_conn_or_404(tgw_id, conn_id)
        if err:
            return err
        return conn

    async def delete_connection(self, tgw_id: str, conn_id: str, version: str = Query(...)):
        _, err = self._get_tgw_or_404(tgw_id)
        if err:
            return err
        _, err = self._get_conn_or_404(tgw_id, conn_id)
        if err:
            return err
        store.delete(f"tgw_connections_{tgw_id}", conn_id)
        return JSONResponse(status_code=204, content=None)

    # ── Global connections endpoint ───────────────────────────────────

    async def list_all_connections(self, version: str = Query(...)):
        all_gateways = store.list("transit_gateways")
        result = []
        for tgw in all_gateways:
            tgw_ref = {"id": tgw["id"], "crn": tgw["crn"], "name": tgw["name"]}
            for conn in store.list(f"tgw_connections_{tgw['id']}"):
                entry = dict(conn)
                entry["transit_gateway"] = tgw_ref
                result.append(entry)
        return {
            "connections": result,
            "limit": 50,
            "total_count": len(result),
            "first": {"href": "/v1/connections?limit=50"},
        }
