"""Pydantic models for IBM Cloud VPC Public Gateways."""

from typing import Optional
from pydantic import BaseModel

from src.models.vpc import ResourceReference, ZoneReference, ResourceGroupReference


class PublicGatewayCreate(BaseModel):
    """Request body for POST /v1/public_gateways."""
    name: str
    vpc: ResourceReference
    zone: ZoneReference
    resource_group: Optional[ResourceGroupReference] = None
    # floating_ip may be pre-specified; if omitted one is auto-reserved
    floating_ip: Optional[ResourceReference] = None


class PublicGateway(BaseModel):
    """Full public gateway resource."""
    id: str
    crn: str = ""
    href: str = ""
    name: str
    vpc: ResourceReference
    zone: ZoneReference
    status: str = "available"
    floating_ip: Optional[dict] = None  # embedded FIP with address
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""
