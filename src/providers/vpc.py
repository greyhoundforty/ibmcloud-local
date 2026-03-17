"""
VPC Provider — emulates the IBM Cloud VPC API.

This is the first and most complete provider. It handles:
    - VPCs (CRUD + default resource auto-creation)
    - Subnets (CRUD + CIDR overlap validation)
    - Instances / VSIs (CRUD + lifecycle state machine)
    - Security Groups (CRUD + rule management)
    - Floating IPs (CRUD + target binding)

The real VPC API lives at: https://cloud.ibm.com/apidocs/vpc
All endpoints are prefixed with /v1/ and require a ?version= query param
and ?generation=2 param. We accept but don't enforce these.

Architecture notes:
    - Each resource type gets its own namespace in the state store
    - Create operations auto-generate IDs, CRNs, and hrefs
    - Instance creation triggers an async state machine (pending → running)
    - CIDR overlap detection is real — it validates subnets don't conflict
"""

import asyncio
import ipaddress
import random
from typing import Optional

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.providers.base import BaseProvider
from src.state.store import store
from src.providers.resource_manager import DEFAULT_RESOURCE_GROUP_ID
from src.models.vpc import (
    Vpc, VpcCreate, VpcStatus,
    Subnet, SubnetCreate, SubnetStatus,
    Instance, InstanceCreate, InstanceStatus,
    SecurityGroup, SecurityGroupCreate, SecurityGroupRule, SecurityGroupRuleCreate,
    FloatingIp, FloatingIpCreate,
    ResourceReference, ZoneReference, ResourceGroupReference, VpcDns,
    NetworkInterface,
)


class VpcProvider(BaseProvider):
    """
    Emulates the IBM Cloud VPC Infrastructure API.

    Handles /v1/vpcs, /v1/subnets, /v1/instances, /v1/security_groups,
    and /v1/floating_ips endpoints.
    """

    service_name = "vpc"
    api_version = "v1"
    description = "VPC Infrastructure (compute, network, storage)"
    api_base_url = "https://us-south.iaas.cloud.ibm.com"

    # IBM Cloud VPC resources in us-south get an "r006-" prefix
    REGION_PREFIX = "r006-"

    # Simulated zones — the emulator pretends to be us-south
    ZONES = ["us-south-1", "us-south-2", "us-south-3"]

    def register_routes(self):
        """Wire up all VPC API routes to their handlers."""

        # ── VPC endpoints ────────────────────────────────────────────
        self.router.get("/v1/vpcs")(self.list_vpcs)
        self.router.post("/v1/vpcs")(self.create_vpc)
        self.router.get("/v1/vpcs/{vpc_id}")(self.get_vpc)
        self.router.patch("/v1/vpcs/{vpc_id}")(self.update_vpc)
        self.router.delete("/v1/vpcs/{vpc_id}")(self.delete_vpc)

        # ── Subnet endpoints ─────────────────────────────────────────
        self.router.get("/v1/subnets")(self.list_subnets)
        self.router.post("/v1/subnets")(self.create_subnet)
        self.router.get("/v1/subnets/{subnet_id}")(self.get_subnet)
        self.router.patch("/v1/subnets/{subnet_id}")(self.update_subnet)
        self.router.delete("/v1/subnets/{subnet_id}")(self.delete_subnet)

        # ── Instance endpoints ───────────────────────────────────────
        self.router.get("/v1/instances")(self.list_instances)
        self.router.post("/v1/instances")(self.create_instance)
        self.router.get("/v1/instances/{instance_id}")(self.get_instance)
        self.router.patch("/v1/instances/{instance_id}")(self.update_instance)
        self.router.delete("/v1/instances/{instance_id}")(self.delete_instance)
        # Instance actions (start, stop, reboot)
        self.router.post("/v1/instances/{instance_id}/actions")(self.instance_action)

        # ── Security Group endpoints ─────────────────────────────────
        self.router.get("/v1/security_groups")(self.list_security_groups)
        self.router.post("/v1/security_groups")(self.create_security_group)
        self.router.get("/v1/security_groups/{sg_id}")(self.get_security_group)
        self.router.delete("/v1/security_groups/{sg_id}")(self.delete_security_group)

        # ── Security Group Rule endpoints ────────────────────────────
        self.router.get("/v1/security_groups/{sg_id}/rules")(self.list_sg_rules)
        self.router.post("/v1/security_groups/{sg_id}/rules")(self.create_sg_rule)
        self.router.get("/v1/security_groups/{sg_id}/rules/{rule_id}")(self.get_sg_rule)
        self.router.patch("/v1/security_groups/{sg_id}/rules/{rule_id}")(self.patch_sg_rule)
        self.router.delete("/v1/security_groups/{sg_id}/rules/{rule_id}")(self.delete_sg_rule)

        # ── Floating IP endpoints ────────────────────────────────────
        self.router.get("/v1/floating_ips")(self.list_floating_ips)
        self.router.post("/v1/floating_ips")(self.create_floating_ip)
        self.router.get("/v1/floating_ips/{fip_id}")(self.get_floating_ip)
        self.router.delete("/v1/floating_ips/{fip_id}")(self.delete_floating_ip)

    # ── Helper methods ───────────────────────────────────────────────

    def _resolve_resource_group(
        self, ref: Optional[ResourceGroupReference]
    ) -> ResourceGroupReference:
        """
        Resolve a resource group reference to a fully-populated object.

        Callers often pass only {"id": "..."}.  We look up the stored resource
        group so the response includes the correct name.  Falls back to the
        Default group if the ID is unknown or no ref was provided.
        """
        target_id = ref.id if ref else DEFAULT_RESOURCE_GROUP_ID
        stored = store.get("resource_groups", target_id)
        if stored:
            return ResourceGroupReference(
                id=stored["id"],
                name=stored["name"],
                href=stored.get("href", ""),
            )
        # Caller supplied an id we don't recognise — store it as-is
        if ref:
            return ref
        return ResourceGroupReference()

    def _make_crn(self, resource_type: str, resource_id: str) -> str:
        """
        Generate a fake CRN (Cloud Resource Name) matching IBM Cloud format.
        Real CRNs look like: crn:v1:bluemix:public:is:us-south:a/account_id::vpc:vpc-id
        """
        return f"crn:v1:bluemix:public:is:us-south:a/local-emulator::{resource_type}:{resource_id}"

    def _make_href(self, path: str) -> str:
        """Generate an href URL for a resource (used in API responses)."""
        return f"{self.api_base_url}{path}"

    def _generate_private_ip(self, cidr: str) -> str:
        """
        Generate a plausible private IP within a CIDR block.
        Used when creating instances to assign a primary IP.
        """
        network = ipaddress.IPv4Network(cidr, strict=False)
        # Pick a random host address (skip network and broadcast)
        hosts = list(network.hosts())
        # Skip first few (often reserved for gateway)
        return str(hosts[random.randint(4, min(len(hosts) - 1, 250))])

    def _generate_public_ip(self) -> str:
        """Generate a fake public IP for floating IPs."""
        return f"169.{random.randint(45, 63)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

    # ══════════════════════════════════════════════════════════════════
    # VPC CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_vpcs(
        self,
        version: str = Query("2024-06-01", description="API version date"),
        generation: int = Query(2, description="Infrastructure generation"),
    ):
        """
        GET /v1/vpcs — List all VPCs.

        The real IBM Cloud API supports filtering by resource_group.id,
        pagination via start/limit, and sorting. We implement basic
        pagination here.
        """
        vpcs = store.list("vpcs")
        return self.collection_response(vpcs, "vpcs")

    async def create_vpc(
        self,
        request: Request,
        version: str = Query("2024-06-01"),
        generation: int = Query(2),
    ):
        """
        POST /v1/vpcs — Create a new VPC.

        In real IBM Cloud, creating a VPC also auto-creates:
            - A default security group
            - A default network ACL
            - A default routing table
            - Address prefixes for each zone (if address_prefix_management == "auto")

        We simulate the default security group creation.
        """
        body = await request.json()
        payload = VpcCreate(**body)

        # Resolve resource group — look up the name from the store if only id was given
        rg = self._resolve_resource_group(payload.resource_group)

        # Generate the VPC resource
        vpc_id = store.generate_id(self.REGION_PREFIX)

        vpc = Vpc(
            id=vpc_id,
            crn=self._make_crn("vpc", vpc_id),
            href=self._make_href(f"/v1/vpcs/{vpc_id}"),
            name=payload.name,
            status=VpcStatus.AVAILABLE,
            classic_access=payload.classic_access,
            resource_group=rg,
            dns=payload.dns or VpcDns(),
        )

        # Store the VPC
        vpc_dict = vpc.model_dump()
        store.put("vpcs", vpc_id, vpc_dict)

        # Auto-create a default security group (like real IBM Cloud does)
        default_sg_id = store.generate_id(self.REGION_PREFIX)
        default_sg = SecurityGroup(
            id=default_sg_id,
            crn=self._make_crn("security-group", default_sg_id),
            href=self._make_href(f"/v1/security_groups/{default_sg_id}"),
            name=f"{payload.name}-default-sg",
            vpc=ResourceReference(id=vpc_id, name=payload.name),
            rules=[
                # Default SG allows all outbound and all inbound from same SG
                SecurityGroupRule(
                    id=store.generate_id(""),
                    direction="outbound",
                    protocol="all",
                    ip_version="ipv4",
                ),
            ],
            resource_group=rg,
        )
        store.put("security_groups", default_sg_id, default_sg.model_dump())

        # Update the VPC to reference its default SG
        store.update("vpcs", vpc_id, {
            "default_security_group": {
                "id": default_sg_id,
                "name": default_sg.name,
                "href": default_sg.href,
            }
        })

        return JSONResponse(
            status_code=201,
            content=store.get("vpcs", vpc_id),
        )

    async def get_vpc(self, vpc_id: str, version: str = Query("2024-06-01")):
        """GET /v1/vpcs/{id} — Retrieve a single VPC."""
        vpc = store.get("vpcs", vpc_id)
        if not vpc:
            return self.not_found("VPC", vpc_id)
        return vpc

    async def update_vpc(
        self,
        vpc_id: str,
        request: Request,
        version: str = Query("2024-06-01"),
    ):
        """PATCH /v1/vpcs/{id} — Update VPC name (the main mutable field)."""
        vpc = store.get("vpcs", vpc_id)
        if not vpc:
            return self.not_found("VPC", vpc_id)

        body = await request.json()
        updated = store.update("vpcs", vpc_id, body)
        return updated

    async def delete_vpc(self, vpc_id: str, version: str = Query("2024-06-01")):
        """
        DELETE /v1/vpcs/{id} — Delete a VPC.

        In real IBM Cloud, you can't delete a VPC that still has subnets,
        instances, etc. We enforce that check here.
        """
        vpc = store.get("vpcs", vpc_id)
        if not vpc:
            return self.not_found("VPC", vpc_id)

        # Check for dependent resources (like real IBM Cloud does)
        subnets = store.list("subnets", filters={"vpc": {"id": vpc_id}})
        # Simplified check — look for subnets referencing this VPC
        subnets = [s for s in store.list("subnets") if s.get("vpc", {}).get("id") == vpc_id]
        if subnets:
            return self.error_response(
                409, "vpc_in_use",
                f"VPC '{vpc_id}' still has {len(subnets)} subnet(s). Delete them first."
            )

        store.delete("vpcs", vpc_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # SUBNET CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_subnets(self, version: str = Query("2024-06-01")):
        """GET /v1/subnets — List all subnets across all VPCs."""
        subnets = store.list("subnets")
        return self.collection_response(subnets, "subnets")

    async def create_subnet(self, request: Request, version: str = Query("2024-06-01")):
        """
        POST /v1/subnets — Create a subnet in a VPC.

        Validates:
            - The referenced VPC exists
            - The CIDR block is valid
            - The CIDR doesn't overlap with existing subnets in the same VPC
        """
        body = await request.json()
        payload = SubnetCreate(**body)

        # Validate the VPC exists
        vpc = store.get("vpcs", payload.vpc.id)
        if not vpc:
            return self.not_found("VPC", payload.vpc.id)

        # Validate CIDR format
        try:
            new_network = ipaddress.IPv4Network(payload.ipv4_cidr_block, strict=False)
        except ValueError as e:
            return self.error_response(400, "invalid_cidr", f"Invalid CIDR block: {e}")

        # Check for CIDR overlap with existing subnets in the same VPC
        existing_subnets = [
            s for s in store.list("subnets")
            if s.get("vpc", {}).get("id") == payload.vpc.id
        ]
        for existing in existing_subnets:
            existing_network = ipaddress.IPv4Network(
                existing["ipv4_cidr_block"], strict=False
            )
            if new_network.overlaps(existing_network):
                return self.error_response(
                    409, "cidr_conflict",
                    f"CIDR {payload.ipv4_cidr_block} overlaps with existing subnet "
                    f"'{existing['name']}' ({existing['ipv4_cidr_block']})"
                )

        # Create the subnet
        subnet_id = store.generate_id(self.REGION_PREFIX)
        # Calculate usable IPs (total minus network, broadcast, and 3 reserved)
        total_ips = new_network.num_addresses
        available_ips = max(0, total_ips - 5)

        subnet = Subnet(
            id=subnet_id,
            crn=self._make_crn("subnet", subnet_id),
            href=self._make_href(f"/v1/subnets/{subnet_id}"),
            name=payload.name,
            vpc=ResourceReference(id=payload.vpc.id, name=vpc.get("name", "")),
            zone=payload.zone,
            ipv4_cidr_block=payload.ipv4_cidr_block,
            status=SubnetStatus.AVAILABLE,
            total_ipv4_address_count=total_ips,
            available_ipv4_address_count=available_ips,
        )

        subnet_dict = subnet.model_dump()

        # Resolve network_acl: use explicit ref if provided, else auto-create a default ACL
        if payload.network_acl:
            existing_acl = store.get("network_acls", payload.network_acl.id)
            if existing_acl:
                subnet_dict["network_acl"] = {
                    "id": existing_acl["id"],
                    "name": existing_acl.get("name", ""),
                    "href": existing_acl.get("href", ""),
                }
        else:
            from src.providers.network_acl import NetworkAclProvider  # avoid circular import
            acl_provider = NetworkAclProvider()
            acl_ref = acl_provider.create_default_acl_for_subnet(
                vpc_id=payload.vpc.id,
                vpc_name=vpc.get("name", ""),
                subnet_name=payload.name,
            )
            subnet_dict["network_acl"] = acl_ref

        store.put("subnets", subnet_id, subnet_dict)
        return JSONResponse(status_code=201, content=subnet_dict)

    async def get_subnet(self, subnet_id: str, version: str = Query("2024-06-01")):
        """GET /v1/subnets/{id}."""
        subnet = store.get("subnets", subnet_id)
        if not subnet:
            return self.not_found("Subnet", subnet_id)
        return subnet

    async def update_subnet(
        self, subnet_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/subnets/{id} — Update subnet name."""
        if not store.get("subnets", subnet_id):
            return self.not_found("Subnet", subnet_id)
        body = await request.json()
        return store.update("subnets", subnet_id, body)

    async def delete_subnet(self, subnet_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/subnets/{id}."""
        if not store.get("subnets", subnet_id):
            return self.not_found("Subnet", subnet_id)
        store.delete("subnets", subnet_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # INSTANCE (VSI) CRUD + STATE MACHINE
    # ══════════════════════════════════════════════════════════════════

    async def list_instances(self, version: str = Query("2024-06-01")):
        """GET /v1/instances — List all instances."""
        instances = store.list("instances")
        return self.collection_response(instances, "instances")

    async def create_instance(self, request: Request, version: str = Query("2024-06-01")):
        """
        POST /v1/instances — Create a VSI.

        This is where the state machine comes in. Creating an instance sets
        status to "pending", then we fire off a background task that
        transitions it through: pending → starting → running.

        In real IBM Cloud this takes 30-90 seconds. We simulate it in ~3s.
        """
        body = await request.json()
        payload = InstanceCreate(**body)

        # Validate VPC exists
        vpc = store.get("vpcs", payload.vpc.id)
        if not vpc:
            return self.not_found("VPC", payload.vpc.id)

        # Validate subnet exists
        subnet = store.get("subnets", payload.primary_network_interface.subnet.id)
        if not subnet:
            return self.not_found("Subnet", payload.primary_network_interface.subnet.id)

        # Generate IDs and build the instance
        instance_id = store.generate_id(self.REGION_PREFIX)
        nic_id = store.generate_id("")
        private_ip = self._generate_private_ip(subnet["ipv4_cidr_block"])

        nic = NetworkInterface(
            id=nic_id,
            name=payload.primary_network_interface.name,
            subnet=ResourceReference(
                id=subnet["id"],
                name=subnet["name"],
            ),
            primary_ip={"address": private_ip, "name": f"ip-{private_ip.replace('.', '-')}"},
        )

        instance = Instance(
            id=instance_id,
            crn=self._make_crn("instance", instance_id),
            href=self._make_href(f"/v1/instances/{instance_id}"),
            name=payload.name,
            vpc=ResourceReference(id=payload.vpc.id, name=vpc.get("name", "")),
            zone=payload.zone,
            profile=payload.profile,
            image=payload.image,
            status=InstanceStatus.PENDING,
            primary_network_interface=nic,
            network_interfaces=[nic],
        )

        instance_dict = instance.model_dump()
        store.put("instances", instance_id, instance_dict)

        # Decrement available IPs on the subnet
        store.update("subnets", subnet["id"], {
            "available_ipv4_address_count": subnet.get("available_ipv4_address_count", 251) - 1
        })

        # Fire the async state machine — instance will transition to "running"
        asyncio.create_task(self._instance_state_machine(instance_id))

        return JSONResponse(status_code=201, content=instance_dict)

    async def _instance_state_machine(self, instance_id: str):
        """
        Simulate the VSI lifecycle state transitions.

        Real IBM Cloud: pending (30s) → starting (15s) → running
        Emulator:       pending (1s) → starting (1s) → running

        This runs in the background so the create API returns immediately
        (just like real IBM Cloud — you get back a "pending" instance).
        """
        transitions = [
            (InstanceStatus.STARTING, 1.0),   # After 1s, move to "starting"
            (InstanceStatus.RUNNING, 1.0),     # After 1s more, move to "running"
        ]

        for next_status, delay in transitions:
            await asyncio.sleep(delay)
            instance = store.get("instances", instance_id)
            # If the instance was deleted while booting, stop
            if not instance:
                return
            store.update("instances", instance_id, {"status": next_status.value})

    async def get_instance(self, instance_id: str, version: str = Query("2024-06-01")):
        """GET /v1/instances/{id}."""
        instance = store.get("instances", instance_id)
        if not instance:
            return self.not_found("Instance", instance_id)
        return instance

    async def update_instance(
        self, instance_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/instances/{id} — Update instance name or profile."""
        if not store.get("instances", instance_id):
            return self.not_found("Instance", instance_id)
        body = await request.json()
        return store.update("instances", instance_id, body)

    async def delete_instance(self, instance_id: str, version: str = Query("2024-06-01")):
        """
        DELETE /v1/instances/{id} — Delete (terminate) an instance.

        Sets status to "deleting" first, then removes after a short delay.
        """
        instance = store.get("instances", instance_id)
        if not instance:
            return self.not_found("Instance", instance_id)

        store.update("instances", instance_id, {"status": InstanceStatus.DELETING.value})

        # Clean up after a brief delay (simulates teardown time)
        async def _cleanup():
            await asyncio.sleep(0.5)
            store.delete("instances", instance_id)

        asyncio.create_task(_cleanup())
        return JSONResponse(status_code=204, content=None)

    async def instance_action(
        self,
        instance_id: str,
        request: Request,
        version: str = Query("2024-06-01"),
    ):
        """
        POST /v1/instances/{id}/actions — Start, stop, or reboot an instance.

        Request body: {"type": "start"}, {"type": "stop"}, {"type": "reboot"}

        Triggers state transitions:
            start:  stopped → starting → running
            stop:   running → stopping → stopped
            reboot: running → restarting → running
        """
        instance = store.get("instances", instance_id)
        if not instance:
            return self.not_found("Instance", instance_id)

        body = await request.json()
        action = body.get("type", "").lower()

        current_status = instance.get("status")

        if action == "start" and current_status == "stopped":
            store.update("instances", instance_id, {"status": "starting"})
            # Transition to running after a delay
            async def _start():
                await asyncio.sleep(1.0)
                store.update("instances", instance_id, {"status": "running"})
            asyncio.create_task(_start())

        elif action == "stop" and current_status == "running":
            store.update("instances", instance_id, {"status": "stopping"})
            async def _stop():
                await asyncio.sleep(1.0)
                store.update("instances", instance_id, {"status": "stopped"})
            asyncio.create_task(_stop())

        elif action == "reboot" and current_status == "running":
            store.update("instances", instance_id, {"status": "restarting"})
            async def _reboot():
                await asyncio.sleep(1.5)
                store.update("instances", instance_id, {"status": "running"})
            asyncio.create_task(_reboot())

        else:
            return self.error_response(
                400, "invalid_action",
                f"Cannot perform '{action}' on instance in '{current_status}' state"
            )

        return JSONResponse(status_code=201, content=store.get("instances", instance_id))

    # ══════════════════════════════════════════════════════════════════
    # SECURITY GROUP CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_security_groups(self, version: str = Query("2024-06-01")):
        """GET /v1/security_groups."""
        sgs = store.list("security_groups")
        return self.collection_response(sgs, "security_groups")

    async def create_security_group(
        self, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/security_groups."""
        body = await request.json()
        payload = SecurityGroupCreate(**body)

        vpc = store.get("vpcs", payload.vpc.id)
        if not vpc:
            return self.not_found("VPC", payload.vpc.id)

        sg_id = store.generate_id(self.REGION_PREFIX)

        # Give each rule its own ID
        rules = []
        for rule in payload.rules:
            rule_dict = rule.model_dump()
            rule_dict["id"] = store.generate_id("")
            rules.append(rule_dict)

        sg = SecurityGroup(
            id=sg_id,
            crn=self._make_crn("security-group", sg_id),
            href=self._make_href(f"/v1/security_groups/{sg_id}"),
            name=payload.name,
            vpc=ResourceReference(id=payload.vpc.id, name=vpc.get("name", "")),
            rules=rules,
        )

        sg_dict = sg.model_dump()
        store.put("security_groups", sg_id, sg_dict)
        return JSONResponse(status_code=201, content=sg_dict)

    async def get_security_group(self, sg_id: str, version: str = Query("2024-06-01")):
        """GET /v1/security_groups/{id}."""
        sg = store.get("security_groups", sg_id)
        if not sg:
            return self.not_found("SecurityGroup", sg_id)
        return sg

    async def delete_security_group(self, sg_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/security_groups/{id}."""
        if not store.get("security_groups", sg_id):
            return self.not_found("SecurityGroup", sg_id)
        store.delete("security_groups", sg_id)
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # SECURITY GROUP RULES
    # ══════════════════════════════════════════════════════════════════

    def _get_sg_or_404(self, sg_id: str):
        """Return (sg_dict, None) or (None, error_response)."""
        sg = store.get("security_groups", sg_id)
        if not sg:
            return None, self.not_found("SecurityGroup", sg_id)
        return sg, None

    def _find_rule(self, sg: dict, rule_id: str):
        """Return the rule dict or None."""
        return next((r for r in sg.get("rules", []) if r["id"] == rule_id), None)

    async def list_sg_rules(self, sg_id: str, version: str = Query("2024-06-01")):
        """GET /v1/security_groups/{id}/rules."""
        sg, err = self._get_sg_or_404(sg_id)
        if err:
            return err
        return {"rules": sg.get("rules", [])}

    async def create_sg_rule(
        self, sg_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/security_groups/{id}/rules — Add a rule to a security group."""
        sg, err = self._get_sg_or_404(sg_id)
        if err:
            return err

        body = await request.json()
        payload = SecurityGroupRuleCreate(**body)
        rule = payload.model_dump()
        rule["id"] = store.generate_id(self.REGION_PREFIX)

        rules = sg.get("rules", [])
        rules.append(rule)
        store.update("security_groups", sg_id, {"rules": rules})
        return JSONResponse(status_code=201, content=rule)

    async def get_sg_rule(
        self, sg_id: str, rule_id: str, version: str = Query("2024-06-01")
    ):
        """GET /v1/security_groups/{id}/rules/{rule_id}."""
        sg, err = self._get_sg_or_404(sg_id)
        if err:
            return err
        rule = self._find_rule(sg, rule_id)
        if not rule:
            return self.not_found("SecurityGroupRule", rule_id)
        return rule

    async def patch_sg_rule(
        self, sg_id: str, rule_id: str, request: Request, version: str = Query("2024-06-01")
    ):
        """PATCH /v1/security_groups/{id}/rules/{rule_id} — Update rule fields."""
        sg, err = self._get_sg_or_404(sg_id)
        if err:
            return err
        rule = self._find_rule(sg, rule_id)
        if not rule:
            return self.not_found("SecurityGroupRule", rule_id)

        body = await request.json()
        rule.update(body)

        rules = [rule if r["id"] == rule_id else r for r in sg.get("rules", [])]
        store.update("security_groups", sg_id, {"rules": rules})
        return rule

    async def delete_sg_rule(
        self, sg_id: str, rule_id: str, version: str = Query("2024-06-01")
    ):
        """DELETE /v1/security_groups/{id}/rules/{rule_id}."""
        sg, err = self._get_sg_or_404(sg_id)
        if err:
            return err
        rule = self._find_rule(sg, rule_id)
        if not rule:
            return self.not_found("SecurityGroupRule", rule_id)

        rules = [r for r in sg.get("rules", []) if r["id"] != rule_id]
        store.update("security_groups", sg_id, {"rules": rules})
        return JSONResponse(status_code=204, content=None)

    # ══════════════════════════════════════════════════════════════════
    # FLOATING IP CRUD
    # ══════════════════════════════════════════════════════════════════

    async def list_floating_ips(self, version: str = Query("2024-06-01")):
        """GET /v1/floating_ips."""
        fips = store.list("floating_ips")
        return self.collection_response(fips, "floating_ips")

    async def create_floating_ip(
        self, request: Request, version: str = Query("2024-06-01")
    ):
        """POST /v1/floating_ips — Reserve a floating IP."""
        body = await request.json()
        payload = FloatingIpCreate(**body)

        fip_id = store.generate_id(self.REGION_PREFIX)
        zone = payload.zone or ZoneReference(name=self.ZONES[0])

        fip = FloatingIp(
            id=fip_id,
            crn=self._make_crn("floating-ip", fip_id),
            href=self._make_href(f"/v1/floating_ips/{fip_id}"),
            name=payload.name,
            address=self._generate_public_ip(),
            status="available",
            zone=zone,
            target=payload.target,
        )

        fip_dict = fip.model_dump()
        store.put("floating_ips", fip_id, fip_dict)
        return JSONResponse(status_code=201, content=fip_dict)

    async def get_floating_ip(self, fip_id: str, version: str = Query("2024-06-01")):
        """GET /v1/floating_ips/{id}."""
        fip = store.get("floating_ips", fip_id)
        if not fip:
            return self.not_found("FloatingIP", fip_id)
        return fip

    async def delete_floating_ip(self, fip_id: str, version: str = Query("2024-06-01")):
        """DELETE /v1/floating_ips/{id}."""
        if not store.get("floating_ips", fip_id):
            return self.not_found("FloatingIP", fip_id)
        store.delete("floating_ips", fip_id)
        return JSONResponse(status_code=204, content=None)
