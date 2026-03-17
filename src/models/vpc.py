"""
VPC Models — Pydantic models that mirror the IBM Cloud VPC API response schemas.

These models serve two purposes:
1. Validate incoming request bodies (create/update payloads)
2. Serialize outgoing responses to match the real IBM Cloud API format

The IBM Cloud VPC API docs live at:
    https://cloud.ibm.com/apidocs/vpc

We model a subset of the full API — enough to be useful for development
and integration testing. Each model has comments noting where we simplify.

Naming convention: *Create models are for request bodies, the base models
are for responses (they include server-generated fields like id, crn, href).
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional


# ── Enums for lifecycle states ───────────────────────────────────────
# These mirror the actual IBM Cloud state machines. The emulator will
# transition resources through these states to simulate real behavior.

class VpcStatus(str, Enum):
    """VPC status values from the IBM Cloud API."""
    AVAILABLE = "available"
    DELETING = "deleting"
    FAILED = "failed"
    PENDING = "pending"


class SubnetStatus(str, Enum):
    AVAILABLE = "available"
    DELETING = "deleting"
    FAILED = "failed"
    PENDING = "pending"


class InstanceStatus(str, Enum):
    """
    VSI lifecycle states. In real IBM Cloud, an instance transitions:
    pending → starting → running → stopping → stopped → deleting
    The emulator simulates these transitions with configurable delays.
    """
    DELETING = "deleting"
    FAILED = "failed"
    PAUSING = "pausing"
    PAUSED = "paused"
    PENDING = "pending"
    RESTARTING = "restarting"
    RESUMING = "resuming"
    RUNNING = "running"
    STARTING = "starting"
    STOPPED = "stopped"
    STOPPING = "stopping"


class SecurityGroupRuleDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class SecurityGroupRuleProtocol(str, Enum):
    ALL = "all"
    ICMP = "icmp"
    TCP = "tcp"
    UDP = "udp"


# ── Shared / nested models ──────────────────────────────────────────

class ResourceReference(BaseModel):
    """
    IBM Cloud resources cross-reference each other with this shape.
    For example, a subnet references its VPC via {"id": "...", "name": "...", "href": "..."}.
    """
    id: str
    name: str = ""
    href: str = ""
    crn: str = ""


class ZoneReference(BaseModel):
    """Reference to an availability zone like us-south-1."""
    name: str
    href: str = ""


class ResourceGroupReference(BaseModel):
    """Every IBM Cloud resource belongs to a resource group."""
    id: str = "default-resource-group"
    name: str = "Default"
    href: str = ""


# ── DNS models (used in VPC) ─────────────────────────────────────────

class DnsManualServer(BaseModel):
    """A manually configured DNS server address, optionally zone-affined."""
    address: str
    zone_affinity: Optional[ZoneReference] = None


class DnsResolver(BaseModel):
    """
    DNS resolver config for a VPC.
    type: "system" (IBM-managed), "manual" (user-specified), "delegated" (hub VPC)
    """
    type: str = "system"
    manual_servers: list[DnsManualServer] = []


class VpcDns(BaseModel):
    """
    VPC-level DNS configuration.
    enable_hub: makes this VPC a DNS hub that other VPCs can delegate to.
    """
    enable_hub: bool = False
    resolver: Optional[DnsResolver] = None


# ── VPC ──────────────────────────────────────────────────────────────

class VpcCreate(BaseModel):
    """
    Request body for POST /v1/vpcs.
    Matches the full IBM Cloud VPC API create schema including dns and resource_group.
    """
    name: str = Field(..., description="Human-readable name for the VPC")
    resource_group: Optional[ResourceGroupReference] = None
    classic_access: bool = False
    address_prefix_management: str = "auto"
    dns: Optional[VpcDns] = None


class Vpc(BaseModel):
    """
    Full VPC resource as returned by the API.
    Matches the shape of GET /v1/vpcs/{id} responses.
    """
    id: str
    crn: str = ""
    href: str = ""
    name: str
    status: VpcStatus = VpcStatus.AVAILABLE
    classic_access: bool = False
    resource_group: ResourceGroupReference = ResourceGroupReference()
    dns: VpcDns = VpcDns()
    created_at: str = ""
    # IBM Cloud includes these in real responses
    default_network_acl: Optional[ResourceReference] = None
    default_routing_table: Optional[ResourceReference] = None
    default_security_group: Optional[ResourceReference] = None


# ── Subnet ───────────────────────────────────────────────────────────

class SubnetCreate(BaseModel):
    """Request body for POST /v1/subnets."""
    name: str
    vpc: ResourceReference = Field(..., description="VPC this subnet belongs to")
    zone: ZoneReference = Field(..., description="Zone like us-south-1")
    ipv4_cidr_block: str = Field(
        ...,
        description="CIDR block like 10.240.0.0/24. Must not overlap with other subnets in the VPC."
    )
    resource_group: Optional[ResourceGroupReference] = None
    network_acl: Optional[ResourceReference] = None


class Subnet(BaseModel):
    """Full subnet resource."""
    id: str
    crn: str = ""
    href: str = ""
    name: str
    vpc: ResourceReference
    zone: ZoneReference
    ipv4_cidr_block: str
    status: SubnetStatus = SubnetStatus.AVAILABLE
    available_ipv4_address_count: int = 251  # /24 gives ~251 usable
    total_ipv4_address_count: int = 256
    resource_group: ResourceGroupReference = ResourceGroupReference()
    network_acl: Optional[ResourceReference] = None
    created_at: str = ""


# ── Security Group ───────────────────────────────────────────────────

class SecurityGroupRuleCreate(BaseModel):
    """A single rule in a security group."""
    direction: SecurityGroupRuleDirection
    protocol: SecurityGroupRuleProtocol = SecurityGroupRuleProtocol.ALL
    ip_version: str = "ipv4"
    # For TCP/UDP rules
    port_min: Optional[int] = None
    port_max: Optional[int] = None
    # Remote can be a CIDR, another SG, etc. Simplified here.
    remote: Optional[dict] = None


class SecurityGroupCreate(BaseModel):
    """Request body for POST /v1/security_groups."""
    name: str
    vpc: ResourceReference
    rules: list[SecurityGroupRuleCreate] = []
    resource_group: Optional[ResourceGroupReference] = None


class SecurityGroupRule(SecurityGroupRuleCreate):
    """A stored rule with its own ID."""
    id: str = ""


class SecurityGroup(BaseModel):
    """Full security group resource."""
    id: str
    crn: str = ""
    href: str = ""
    name: str
    vpc: ResourceReference
    rules: list[SecurityGroupRule] = []
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""


# ── Instance (VSI) ───────────────────────────────────────────────────

class NetworkInterfaceCreate(BaseModel):
    """Primary network interface config for instance creation."""
    name: str = "eth0"
    subnet: ResourceReference


class InstanceProfileReference(BaseModel):
    """
    Reference to a VSI profile (e.g., bx2-2x8, cx2-4x8).
    The emulator doesn't enforce actual compute — it just tracks the profile name.
    """
    name: str = "bx2-2x8"


class ImageReference(BaseModel):
    """Reference to an OS image."""
    id: str = "default-image"
    name: str = "ibm-ubuntu-24-04-minimal-amd64-1"


class InstanceCreate(BaseModel):
    """
    Request body for POST /v1/instances.
    Simplified — real API has boot_volume_attachment, keys, user_data, etc.
    """
    name: str
    vpc: ResourceReference
    zone: ZoneReference
    profile: InstanceProfileReference = InstanceProfileReference()
    image: ImageReference = ImageReference()
    primary_network_interface: NetworkInterfaceCreate
    resource_group: Optional[ResourceGroupReference] = None


class NetworkInterface(BaseModel):
    """A network interface attached to an instance."""
    id: str = ""
    name: str = "eth0"
    subnet: ResourceReference
    primary_ip: dict = {}  # Simplified — real API has a nested ReservedIP object


class Instance(BaseModel):
    """
    Full VSI instance resource.
    Matches GET /v1/instances/{id} response shape.
    """
    id: str
    crn: str = ""
    href: str = ""
    name: str
    vpc: ResourceReference
    zone: ZoneReference
    profile: InstanceProfileReference = InstanceProfileReference()
    image: ImageReference = ImageReference()
    status: InstanceStatus = InstanceStatus.PENDING
    primary_network_interface: Optional[NetworkInterface] = None
    network_interfaces: list[NetworkInterface] = []
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""
    # Bandwidth, vCPU, memory — tracked for display but not enforced
    vcpu: dict = Field(default_factory=lambda: {"architecture": "amd64", "count": 2})
    memory: int = 8  # GB


# ── Floating IP ──────────────────────────────────────────────────────

class FloatingIpCreate(BaseModel):
    """Request body for POST /v1/floating_ips."""
    name: str
    zone: Optional[ZoneReference] = None
    target: Optional[ResourceReference] = None  # Network interface to bind to
    resource_group: Optional[ResourceGroupReference] = None


class FloatingIp(BaseModel):
    """Full floating IP resource."""
    id: str
    crn: str = ""
    href: str = ""
    name: str
    address: str = ""  # The actual IP — we generate a fake one
    status: str = "available"
    zone: ZoneReference = ZoneReference(name="us-south-1")
    target: Optional[ResourceReference] = None
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""
