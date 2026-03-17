"""
Network ACL Provider — emulates IBM Cloud VPC Network ACL API.

Endpoints:
    GET/POST  /v1/network_acls
    GET/PATCH/DELETE  /v1/network_acls/{id}
    GET/POST  /v1/network_acls/{id}/rules
    GET/PATCH/DELETE  /v1/network_acls/{id}/rules/{rule_id}

IBM Cloud API reference: https://cloud.ibm.com/apidocs/vpc/latest#list-network-acls
"""

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.providers.base import BaseProvider
from src.state.store import store
from src.models.network_acl import NetworkAcl, NetworkAclCreate, NetworkAclRule, NetworkAclRuleCreate
from src.models.vpc import ResourceReference


class NetworkAclProvider(BaseProvider):
    service_name = "network_acl"
    api_version = "v1"
    description = "VPC Network ACLs"
    api_base_url = "https://us-south.iaas.cloud.ibm.com"

    REGION_PREFIX = "r006-"

    def register_routes(self):
        self.router.get("/v1/network_acls")(self.list_network_acls)
        self.router.post("/v1/network_acls")(self.create_network_acl)
        self.router.get("/v1/network_acls/{acl_id}")(self.get_network_acl)
        self.router.patch("/v1/network_acls/{acl_id}")(self.patch_network_acl)
        self.router.delete("/v1/network_acls/{acl_id}")(self.delete_network_acl)

        self.router.get("/v1/network_acls/{acl_id}/rules")(self.list_acl_rules)
        self.router.post("/v1/network_acls/{acl_id}/rules")(self.create_acl_rule)
        self.router.get("/v1/network_acls/{acl_id}/rules/{rule_id}")(self.get_acl_rule)
        self.router.patch("/v1/network_acls/{acl_id}/rules/{rule_id}")(self.patch_acl_rule)
        self.router.delete("/v1/network_acls/{acl_id}/rules/{rule_id}")(self.delete_acl_rule)

    def _make_crn(self, resource_id: str) -> str:
        return f"crn:v1:bluemix:public:is:us-south:a/local-emulator::network-acl:{resource_id}"

    def _make_href(self, path: str) -> str:
        return f"{self.api_base_url}{path}"

    def _get_acl_or_404(self, acl_id: str):
        acl = store.get("network_acls", acl_id)
        if not acl:
            return None, self.not_found("NetworkACL", acl_id)
        return acl, None

    def _find_rule(self, acl: dict, rule_id: str):
        return next((r for r in acl.get("rules", []) if r["id"] == rule_id), None)

    def _default_allow_all_rule(self, direction: str) -> dict:
        rule = NetworkAclRule(
            id=store.generate_id(self.REGION_PREFIX),
            name=f"default-allow-{direction}",
            action="allow",
            direction=direction,
            protocol="all",
            source="0.0.0.0/0",
            destination="0.0.0.0/0",
            priority=100,
        )
        return rule.model_dump()

    # ── Public helper: create a default ACL for a subnet ─────────────

    def create_default_acl_for_subnet(self, vpc_id: str, vpc_name: str, subnet_name: str) -> dict:
        """Create a default ACL and return its ResourceReference dict."""
        acl_id = store.generate_id(self.REGION_PREFIX)
        acl = NetworkAcl(
            id=acl_id,
            crn=self._make_crn(acl_id),
            href=self._make_href(f"/v1/network_acls/{acl_id}"),
            name=f"{subnet_name}-default-acl",
            vpc=ResourceReference(id=vpc_id, name=vpc_name),
            rules=[
                NetworkAclRule(**self._default_allow_all_rule("inbound")),
                NetworkAclRule(**self._default_allow_all_rule("outbound")),
            ],
        )
        store.put("network_acls", acl_id, acl.model_dump())
        return {"id": acl_id, "name": acl.name, "href": acl.href}

    # ══════════════════════════════════════════════════════════════════
    # NETWORK ACL CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_network_acls(self, version: str = Query("2024-06-01")):
        """GET /v1/network_acls."""
        acls = store.list("network_acls")
        return self.collection_response(acls, "network_acls")

    async def create_network_acl(self, request: Request, version: str = Query("2024-06-01")):
        """POST /v1/network_acls — Create a network ACL with a default allow-all rule."""
        body = await request.json()
        payload = NetworkAclCreate(**body)

        vpc = store.get("vpcs", payload.vpc.id)
        if not vpc:
            return self.not_found("VPC", payload.vpc.id)

        acl_id = store.generate_id(self.REGION_PREFIX)

        # Seed caller-supplied rules; if none, add default allow-all for both directions
        rules = []
        for rule in payload.rules:
            r = rule.model_dump()
            r["id"] = store.generate_id(self.REGION_PREFIX)
            rules.append(r)

        if not rules:
            rules = [
                self._default_allow_all_rule("inbound"),
                self._default_allow_all_rule("outbound"),
            ]

        acl = NetworkAcl(
            id=acl_id,
            crn=self._make_crn(acl_id),
            href=self._make_href(f"/v1/network_acls/{acl_id}"),
            name=payload.name,
            vpc=ResourceReference(id=payload.vpc.id, name=vpc.get("name", "")),
            rules=rules,
        )

        acl_dict = acl.model_dump()
        store.put("network_acls", acl_id, acl_dict)
        return JSONResponse(status_code=201, content=acl_dict)

    async def get_network_acl(self, acl_id: str, version: str = Query("2024-06-01")):
        """GET /v1/network_acls/{id}."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        return acl

    async def patch_network_acl(
        self, acl_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/network_acls/{id} — Rename."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        body = await request.json()
        return store.update("network_acls", acl_id, body)

    async def delete_network_acl(self, acl_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/network_acls/{id} — Reject if attached to any subnet."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err

        attached = [
            s for s in store.list("subnets")
            if (s.get("network_acl") or {}).get("id") == acl_id
        ]
        if attached:
            return self.error_response(
                409, "network_acl_in_use",
                f"NetworkACL '{acl_id}' is still attached to {len(attached)} subnet(s)."
            )

        store.delete("network_acls", acl_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # NETWORK ACL RULES
    # ══════════════════════════════════════════════════════════════════

    async def list_acl_rules(self, acl_id: str, version: str = Query("2024-06-01")):
        """GET /v1/network_acls/{id}/rules."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        return {"rules": acl.get("rules", [])}

    async def create_acl_rule(
        self, acl_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/network_acls/{id}/rules."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err

        body = await request.json()
        payload = NetworkAclRuleCreate(**body)
        rule = payload.model_dump()
        rule["id"] = store.generate_id(self.REGION_PREFIX)

        rules = acl.get("rules", [])
        rules.append(rule)
        store.update("network_acls", acl_id, {"rules": rules})
        return JSONResponse(status_code=201, content=rule)

    async def get_acl_rule(
        self, acl_id: str, rule_id: str, version: str = Query("2024-06-01")
    ):
        """GET /v1/network_acls/{id}/rules/{rule_id}."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        rule = self._find_rule(acl, rule_id)
        if not rule:
            return self.not_found("NetworkACLRule", rule_id)
        return rule

    async def patch_acl_rule(
        self, acl_id: str, rule_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/network_acls/{id}/rules/{rule_id}."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        rule = self._find_rule(acl, rule_id)
        if not rule:
            return self.not_found("NetworkACLRule", rule_id)

        body = await request.json()
        rule.update(body)

        rules = [rule if r["id"] == rule_id else r for r in acl.get("rules", [])]
        store.update("network_acls", acl_id, {"rules": rules})
        return rule

    async def delete_acl_rule(
        self, acl_id: str, rule_id: str, version: str = Query("2024-06-01")
    ):
        """DELETE /v1/network_acls/{id}/rules/{rule_id}."""
        acl, err = self._get_acl_or_404(acl_id)
        if err:
            return err
        rule = self._find_rule(acl, rule_id)
        if not rule:
            return self.not_found("NetworkACLRule", rule_id)

        rules = [r for r in acl.get("rules", []) if r["id"] != rule_id]
        store.update("network_acls", acl_id, {"rules": rules})
        return JSONResponse(status_code=204, content=None)
