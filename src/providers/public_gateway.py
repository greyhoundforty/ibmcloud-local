"""
Public Gateway Provider — emulates IBM Cloud VPC Public Gateway API.

Endpoints:
    GET/POST  /v1/public_gateways
    GET/PATCH/DELETE  /v1/public_gateways/{id}
    GET  /v1/subnets/{id}/public_gateway
    PUT  /v1/subnets/{id}/public_gateway
    DELETE  /v1/subnets/{id}/public_gateway

IBM Cloud API reference: https://cloud.ibm.com/apidocs/vpc/latest#list-public-gateways
"""

import random

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.providers.base import BaseProvider
from src.state.store import store
from src.models.public_gateway import PublicGateway, PublicGatewayCreate
from src.models.vpc import ResourceReference


class PublicGatewayProvider(BaseProvider):
    service_name = "public_gateway"
    api_version = "v1"
    description = "VPC Public Gateways"
    api_base_url = "https://us-south.iaas.cloud.ibm.com"

    REGION_PREFIX = "r006-"

    def register_routes(self):
        self.router.get("/v1/public_gateways")(self.list_public_gateways)
        self.router.post("/v1/public_gateways")(self.create_public_gateway)
        self.router.get("/v1/public_gateways/{pgw_id}")(self.get_public_gateway)
        self.router.patch("/v1/public_gateways/{pgw_id}")(self.patch_public_gateway)
        self.router.delete("/v1/public_gateways/{pgw_id}")(self.delete_public_gateway)

        self.router.get("/v1/subnets/{subnet_id}/public_gateway")(self.get_subnet_gateway)
        self.router.put("/v1/subnets/{subnet_id}/public_gateway")(self.attach_subnet_gateway)
        self.router.delete("/v1/subnets/{subnet_id}/public_gateway")(self.detach_subnet_gateway)

    def _make_crn(self, resource_id: str) -> str:
        return f"crn:v1:bluemix:public:is:us-south:a/local-emulator::public-gateway:{resource_id}"

    def _make_href(self, path: str) -> str:
        return f"{self.api_base_url}{path}"

    def _generate_public_ip(self) -> str:
        return f"169.{random.randint(45, 63)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

    def _get_pgw_or_404(self, pgw_id: str):
        pgw = store.get("public_gateways", pgw_id)
        if not pgw:
            return None, self.not_found("PublicGateway", pgw_id)
        return pgw, None

    # ══════════════════════════════════════════════════════════════════
    # PUBLIC GATEWAY CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_public_gateways(self, version: str = Query("2024-06-01")):
        """GET /v1/public_gateways."""
        return self.collection_response(store.list("public_gateways"), "public_gateways")

    async def create_public_gateway(self, request: Request, version: str = Query("2024-06-01")):
        """POST /v1/public_gateways — auto-reserves a floating IP."""
        body = await request.json()
        payload = PublicGatewayCreate(**body)

        vpc = store.get("vpcs", payload.vpc.id)
        if not vpc:
            return self.not_found("VPC", payload.vpc.id)

        # Enforce one gateway per zone per VPC
        zone_name = payload.zone.name
        existing = [
            g for g in store.list("public_gateways")
            if g.get("vpc", {}).get("id") == payload.vpc.id
            and g.get("zone", {}).get("name") == zone_name
        ]
        if existing:
            return self.error_response(
                409, "public_gateway_zone_conflict",
                f"A public gateway already exists for VPC '{payload.vpc.id}' in zone '{zone_name}'."
            )

        pgw_id = store.generate_id(self.REGION_PREFIX)

        # Auto-reserve a floating IP
        fip_id = store.generate_id(self.REGION_PREFIX)
        fip_address = self._generate_public_ip()
        fip = {
            "id": fip_id,
            "address": fip_address,
            "name": f"{payload.name}-fip",
            "href": self._make_href(f"/v1/floating_ips/{fip_id}"),
        }
        # Store it in floating_ips namespace so it shows up in FIP list
        store.put("floating_ips", fip_id, {
            **fip,
            "crn": f"crn:v1:bluemix:public:is:us-south:a/local-emulator::floating-ip:{fip_id}",
            "status": "available",
            "zone": payload.zone.model_dump(),
        })

        pgw = PublicGateway(
            id=pgw_id,
            crn=self._make_crn(pgw_id),
            href=self._make_href(f"/v1/public_gateways/{pgw_id}"),
            name=payload.name,
            vpc=ResourceReference(id=payload.vpc.id, name=vpc.get("name", "")),
            zone=payload.zone,
            status="available",
            floating_ip=fip,
        )

        pgw_dict = pgw.model_dump()
        store.put("public_gateways", pgw_id, pgw_dict)
        return JSONResponse(status_code=201, content=pgw_dict)

    async def get_public_gateway(self, pgw_id: str, version: str = Query("2024-06-01")):
        """GET /v1/public_gateways/{id}."""
        pgw, err = self._get_pgw_or_404(pgw_id)
        if err:
            return err
        return pgw

    async def patch_public_gateway(
        self, pgw_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/public_gateways/{id} — Rename."""
        pgw, err = self._get_pgw_or_404(pgw_id)
        if err:
            return err
        body = await request.json()
        return store.update("public_gateways", pgw_id, body)

    async def delete_public_gateway(self, pgw_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/public_gateways/{id} — Reject if attached to any subnet."""
        pgw, err = self._get_pgw_or_404(pgw_id)
        if err:
            return err

        attached = [
            s for s in store.list("subnets")
            if (s.get("public_gateway") or {}).get("id") == pgw_id
        ]
        if attached:
            return self.error_response(
                409, "public_gateway_in_use",
                f"PublicGateway '{pgw_id}' is still attached to {len(attached)} subnet(s)."
            )

        store.delete("public_gateways", pgw_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # SUBNET / GATEWAY ATTACHMENT
    # ══════════════════════════════════════════════════════════════════

    async def get_subnet_gateway(self, subnet_id: str, version: str = Query("2024-06-01")):
        """GET /v1/subnets/{id}/public_gateway."""
        subnet = store.get("subnets", subnet_id)
        if not subnet:
            return self.not_found("Subnet", subnet_id)
        pgw_ref = subnet.get("public_gateway")
        if not pgw_ref:
            return self.not_found("PublicGateway", f"attached to subnet {subnet_id}")
        pgw = store.get("public_gateways", pgw_ref["id"])
        if not pgw:
            return self.not_found("PublicGateway", pgw_ref["id"])
        return pgw

    async def attach_subnet_gateway(
        self, subnet_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PUT /v1/subnets/{id}/public_gateway — Attach gateway to subnet."""
        subnet = store.get("subnets", subnet_id)
        if not subnet:
            return self.not_found("Subnet", subnet_id)

        body = await request.json()
        pgw_id = body.get("id")
        pgw = store.get("public_gateways", pgw_id)
        if not pgw:
            return self.not_found("PublicGateway", pgw_id)

        store.update("subnets", subnet_id, {
            "public_gateway": {"id": pgw_id, "name": pgw.get("name", ""), "href": pgw.get("href", "")}
        })
        return JSONResponse(status_code=201, content=pgw)

    async def detach_subnet_gateway(self, subnet_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/subnets/{id}/public_gateway — Detach gateway from subnet."""
        subnet = store.get("subnets", subnet_id)
        if not subnet:
            return self.not_found("Subnet", subnet_id)
        store.update("subnets", subnet_id, {"public_gateway": None})
        return JSONResponse(status_code=204, content=None)
