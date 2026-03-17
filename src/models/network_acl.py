"""Pydantic models for IBM Cloud VPC Network ACLs."""

from typing import Optional
from pydantic import BaseModel

from src.models.vpc import ResourceReference, ResourceGroupReference


class NetworkAclRuleCreate(BaseModel):
    """Request body for creating a single ACL rule."""
    name: str = ""
    action: str = "allow"            # "allow" | "deny"
    direction: str = "inbound"       # "inbound" | "outbound"
    protocol: str = "all"            # "all" | "tcp" | "udp" | "icmp"
    source: str = "0.0.0.0/0"
    destination: str = "0.0.0.0/0"
    port_min: Optional[int] = None
    port_max: Optional[int] = None
    priority: int = 100              # lower = higher priority


class NetworkAclRule(NetworkAclRuleCreate):
    """Stored ACL rule — adds an auto-generated ID."""
    id: str = ""


class NetworkAclCreate(BaseModel):
    """Request body for POST /v1/network_acls."""
    name: str
    vpc: ResourceReference
    resource_group: Optional[ResourceGroupReference] = None
    rules: list[NetworkAclRuleCreate] = []


class NetworkAcl(BaseModel):
    """Full network ACL resource."""
    id: str
    crn: str = ""
    href: str = ""
    name: str
    vpc: ResourceReference
    rules: list[NetworkAclRule] = []
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""
