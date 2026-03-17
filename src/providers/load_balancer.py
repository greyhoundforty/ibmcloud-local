"""
Load Balancer Provider — emulates IBM Cloud VPC Load Balancer API.

Three-level hierarchy: load balancer → listeners → pools → members
Listeners and pools are stored inline in the LB dict.
Members are stored inline in the pool dict.

IBM Cloud API reference: https://cloud.ibm.com/apidocs/vpc/latest#list-load-balancers
"""

import asyncio

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.providers.base import BaseProvider
from src.state.store import store
from src.models.load_balancer import (
    LoadBalancer, LoadBalancerCreate,
    Listener, ListenerCreate,
    Pool, PoolCreate,
    PoolMember, PoolMemberCreate,
)
from src.models.vpc import ResourceReference


class LoadBalancerProvider(BaseProvider):
    service_name = "load_balancer"
    api_version = "v1"
    description = "VPC Load Balancers"
    api_base_url = "https://us-south.iaas.cloud.ibm.com"

    REGION_PREFIX = "r006-"

    def register_routes(self):
        # LB CRUD
        self.router.get("/v1/load_balancers")(self.list_load_balancers)
        self.router.post("/v1/load_balancers")(self.create_load_balancer)
        self.router.get("/v1/load_balancers/{lb_id}")(self.get_load_balancer)
        self.router.patch("/v1/load_balancers/{lb_id}")(self.patch_load_balancer)
        self.router.delete("/v1/load_balancers/{lb_id}")(self.delete_load_balancer)

        # Listeners
        self.router.get("/v1/load_balancers/{lb_id}/listeners")(self.list_listeners)
        self.router.post("/v1/load_balancers/{lb_id}/listeners")(self.create_listener)
        self.router.get("/v1/load_balancers/{lb_id}/listeners/{listener_id}")(self.get_listener)
        self.router.patch(
            "/v1/load_balancers/{lb_id}/listeners/{listener_id}"
        )(self.patch_listener)
        self.router.delete(
            "/v1/load_balancers/{lb_id}/listeners/{listener_id}"
        )(self.delete_listener)

        # Pools
        self.router.get("/v1/load_balancers/{lb_id}/pools")(self.list_pools)
        self.router.post("/v1/load_balancers/{lb_id}/pools")(self.create_pool)
        self.router.get("/v1/load_balancers/{lb_id}/pools/{pool_id}")(self.get_pool)
        self.router.patch("/v1/load_balancers/{lb_id}/pools/{pool_id}")(self.patch_pool)
        self.router.delete("/v1/load_balancers/{lb_id}/pools/{pool_id}")(self.delete_pool)

        # Members
        self.router.get(
            "/v1/load_balancers/{lb_id}/pools/{pool_id}/members"
        )(self.list_members)
        self.router.post(
            "/v1/load_balancers/{lb_id}/pools/{pool_id}/members"
        )(self.create_member)
        self.router.get(
            "/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}"
        )(self.get_member)
        self.router.patch(
            "/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}"
        )(self.patch_member)
        self.router.delete(
            "/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}"
        )(self.delete_member)

    # ── Helpers ──────────────────────────────────────────────────────

    def _make_crn(self, resource_id: str) -> str:
        return f"crn:v1:bluemix:public:is:us-south:a/local-emulator::load-balancer:{resource_id}"

    def _make_href(self, path: str) -> str:
        return f"{self.api_base_url}{path}"

    def _short_id(self, full_id: str) -> str:
        return full_id.replace("r006-", "")[:8]

    def _get_lb_or_404(self, lb_id: str):
        lb = store.get("load_balancers", lb_id)
        if not lb:
            return None, self.not_found("LoadBalancer", lb_id)
        return lb, None

    def _find_listener(self, lb: dict, listener_id: str):
        return next((item for item in lb.get("_listeners", []) if item["id"] == listener_id), None)

    def _find_pool(self, lb: dict, pool_id: str):
        return next((p for p in lb.get("_pools", []) if p["id"] == pool_id), None)

    def _find_member(self, pool: dict, member_id: str):
        return next((m for m in pool.get("_members", []) if m["id"] == member_id), None)

    async def _activate_lb(self, lb_id: str):
        """Transition LB from create_pending → active after ~2s (matches real IBM Cloud)."""
        await asyncio.sleep(2.0)
        lb = store.get("load_balancers", lb_id)
        if lb:
            store.update("load_balancers", lb_id, {
                "provisioning_status": "active",
                "operating_status": "online",
            })

    # ══════════════════════════════════════════════════════════════════
    # LOAD BALANCER CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_load_balancers(self, version: str = Query("2024-06-01")):
        """GET /v1/load_balancers."""
        lbs = store.list("load_balancers")
        # Strip internal keys before returning
        return self.collection_response(
            [{k: v for k, v in lb.items() if not k.startswith("_")} for lb in lbs],
            "load_balancers",
        )

    async def create_load_balancer(self, request: Request, version: str = Query("2024-06-01")):
        """POST /v1/load_balancers."""
        body = await request.json()
        payload = LoadBalancerCreate(**body)

        # Validate subnets exist
        subnet_refs = []
        for subnet_ref in payload.subnets:
            subnet = store.get("subnets", subnet_ref.id)
            if not subnet:
                return self.not_found("Subnet", subnet_ref.id)
            subnet_refs.append(ResourceReference(id=subnet["id"], name=subnet.get("name", "")))

        lb_id = store.generate_id(self.REGION_PREFIX)
        short = self._short_id(lb_id)

        lb = LoadBalancer(
            id=lb_id,
            crn=self._make_crn(lb_id),
            href=self._make_href(f"/v1/load_balancers/{lb_id}"),
            name=payload.name,
            hostname=f"{short}.lb.appdomain.cloud",
            is_public=payload.is_public,
            subnets=subnet_refs,
            provisioning_status="create_pending",
            operating_status="offline",
        )

        lb_dict = lb.model_dump()
        # Internal storage for nested resources (not exposed in API responses)
        lb_dict["_listeners"] = []
        lb_dict["_pools"] = []

        store.put("load_balancers", lb_id, lb_dict)
        asyncio.create_task(self._activate_lb(lb_id))

        return JSONResponse(
            status_code=201,
            content={k: v for k, v in lb_dict.items() if not k.startswith("_")},
        )

    async def get_load_balancer(self, lb_id: str, version: str = Query("2024-06-01")):
        """GET /v1/load_balancers/{id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        return {k: v for k, v in lb.items() if not k.startswith("_")}

    async def patch_load_balancer(
        self, lb_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/load_balancers/{id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        body = await request.json()
        updated = store.update("load_balancers", lb_id, body)
        return {k: v for k, v in updated.items() if not k.startswith("_")}

    async def delete_load_balancer(self, lb_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/load_balancers/{id}."""
        _, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        store.delete("load_balancers", lb_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # LISTENERS
    # ══════════════════════════════════════════════════════════════════

    async def list_listeners(self, lb_id: str, version: str = Query("2024-06-01")):
        """GET /v1/load_balancers/{id}/listeners."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        return {"listeners": lb.get("_listeners", [])}

    async def create_listener(
        self, lb_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/load_balancers/{id}/listeners."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err

        body = await request.json()
        payload = ListenerCreate(**body)
        listener_id = store.generate_id(self.REGION_PREFIX)

        listener = Listener(
            id=listener_id,
            href=self._make_href(f"/v1/load_balancers/{lb_id}/listeners/{listener_id}"),
            port=payload.port,
            protocol=payload.protocol,
            default_pool=payload.default_pool,
        )
        listener_dict = listener.model_dump()

        listeners = lb.get("_listeners", [])
        listeners.append(listener_dict)
        store.update("load_balancers", lb_id, {"_listeners": listeners})
        return JSONResponse(status_code=201, content=listener_dict)

    async def get_listener(
        self, lb_id: str, listener_id: str, version: str = Query("2024-06-01")
    ):
        """GET /v1/load_balancers/{id}/listeners/{listener_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        listener = self._find_listener(lb, listener_id)
        if not listener:
            return self.not_found("Listener", listener_id)
        return listener

    async def patch_listener(
        self, lb_id: str, listener_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/load_balancers/{id}/listeners/{listener_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        listener = self._find_listener(lb, listener_id)
        if not listener:
            return self.not_found("Listener", listener_id)

        body = await request.json()
        listener.update(body)
        listeners = [listener if item["id"] == listener_id else item for item in lb.get("_listeners", [])]
        store.update("load_balancers", lb_id, {"_listeners": listeners})
        return listener

    async def delete_listener(
        self, lb_id: str, listener_id: str, version: str = Query("2024-06-01")
    ):
        """DELETE /v1/load_balancers/{id}/listeners/{listener_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        if not self._find_listener(lb, listener_id):
            return self.not_found("Listener", listener_id)
        listeners = [item for item in lb.get("_listeners", []) if item["id"] != listener_id]
        store.update("load_balancers", lb_id, {"_listeners": listeners})
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # POOLS
    # ══════════════════════════════════════════════════════════════════

    async def list_pools(self, lb_id: str, version: str = Query("2024-06-01")):
        """GET /v1/load_balancers/{id}/pools."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        pools = [{k: v for k, v in p.items() if not k.startswith("_")}
                 for p in lb.get("_pools", [])]
        return {"pools": pools}

    async def create_pool(
        self, lb_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/load_balancers/{id}/pools."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err

        body = await request.json()
        payload = PoolCreate(**body)
        pool_id = store.generate_id(self.REGION_PREFIX)

        pool = Pool(
            id=pool_id,
            href=self._make_href(f"/v1/load_balancers/{lb_id}/pools/{pool_id}"),
            name=payload.name,
            algorithm=payload.algorithm,
            protocol=payload.protocol,
            health_monitor=payload.health_monitor or {},
        )
        pool_dict = pool.model_dump()
        pool_dict["_members"] = []

        pools = lb.get("_pools", [])
        pools.append(pool_dict)
        store.update("load_balancers", lb_id, {"_pools": pools})

        return JSONResponse(
            status_code=201,
            content={k: v for k, v in pool_dict.items() if not k.startswith("_")},
        )

    async def get_pool(self, lb_id: str, pool_id: str, version: str = Query("2024-06-01")):
        """GET /v1/load_balancers/{id}/pools/{pool_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        pool = self._find_pool(lb, pool_id)
        if not pool:
            return self.not_found("Pool", pool_id)
        return {k: v for k, v in pool.items() if not k.startswith("_")}

    async def patch_pool(
        self, lb_id: str, pool_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/load_balancers/{id}/pools/{pool_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        pool = self._find_pool(lb, pool_id)
        if not pool:
            return self.not_found("Pool", pool_id)

        body = await request.json()
        pool.update(body)
        pools = [pool if p["id"] == pool_id else p for p in lb.get("_pools", [])]
        store.update("load_balancers", lb_id, {"_pools": pools})
        return {k: v for k, v in pool.items() if not k.startswith("_")}

    async def delete_pool(self, lb_id: str, pool_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/load_balancers/{id}/pools/{pool_id}."""
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return err
        if not self._find_pool(lb, pool_id):
            return self.not_found("Pool", pool_id)
        pools = [p for p in lb.get("_pools", []) if p["id"] != pool_id]
        store.update("load_balancers", lb_id, {"_pools": pools})
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # POOL MEMBERS
    # ══════════════════════════════════════════════════════════════════

    def _get_pool_in_lb(self, lb_id: str, pool_id: str):
        lb, err = self._get_lb_or_404(lb_id)
        if err:
            return None, None, err
        pool = self._find_pool(lb, pool_id)
        if not pool:
            return None, None, self.not_found("Pool", pool_id)
        return lb, pool, None

    async def list_members(
        self, lb_id: str, pool_id: str, version: str = Query("2024-06-01")
    ):
        """GET /v1/load_balancers/{id}/pools/{pool_id}/members."""
        _, pool, err = self._get_pool_in_lb(lb_id, pool_id)
        if err:
            return err
        return {"members": pool.get("_members", [])}

    async def create_member(
        self, lb_id: str, pool_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/load_balancers/{id}/pools/{pool_id}/members."""
        lb, pool, err = self._get_pool_in_lb(lb_id, pool_id)
        if err:
            return err

        body = await request.json()
        payload = PoolMemberCreate(**body)
        member_id = store.generate_id(self.REGION_PREFIX)

        member = PoolMember(
            id=member_id,
            href=self._make_href(
                f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}"
            ),
            target=payload.target,
            port=payload.port,
            weight=payload.weight,
        )
        member_dict = member.model_dump()

        members = pool.get("_members", [])
        members.append(member_dict)
        pool["_members"] = members

        pools = [pool if p["id"] == pool_id else p for p in lb.get("_pools", [])]
        store.update("load_balancers", lb_id, {"_pools": pools})
        return JSONResponse(status_code=201, content=member_dict)

    async def get_member(
        self, lb_id: str, pool_id: str, member_id: str, version: str = Query("2024-06-01")
    ):
        """GET /v1/load_balancers/{id}/pools/{pool_id}/members/{member_id}."""
        _, pool, err = self._get_pool_in_lb(lb_id, pool_id)
        if err:
            return err
        member = self._find_member(pool, member_id)
        if not member:
            return self.not_found("PoolMember", member_id)
        return member

    async def patch_member(
        self, lb_id: str, pool_id: str, member_id: str,
        request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/load_balancers/{id}/pools/{pool_id}/members/{member_id}."""
        lb, pool, err = self._get_pool_in_lb(lb_id, pool_id)
        if err:
            return err
        member = self._find_member(pool, member_id)
        if not member:
            return self.not_found("PoolMember", member_id)

        body = await request.json()
        member.update(body)

        pool["_members"] = [
            member if m["id"] == member_id else m for m in pool.get("_members", [])
        ]
        pools = [pool if p["id"] == pool_id else p for p in lb.get("_pools", [])]
        store.update("load_balancers", lb_id, {"_pools": pools})
        return member

    async def delete_member(
        self, lb_id: str, pool_id: str, member_id: str, version: str = Query("2024-06-01")
    ):
        """DELETE /v1/load_balancers/{id}/pools/{pool_id}/members/{member_id}."""
        lb, pool, err = self._get_pool_in_lb(lb_id, pool_id)
        if err:
            return err
        if not self._find_member(pool, member_id):
            return self.not_found("PoolMember", member_id)

        pool["_members"] = [m for m in pool.get("_members", []) if m["id"] != member_id]
        pools = [pool if p["id"] == pool_id else p for p in lb.get("_pools", [])]
        store.update("load_balancers", lb_id, {"_pools": pools})
        return JSONResponse(status_code=204, content=None)
