"""Pydantic models for the Transit Gateway service."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class ResourceGroupRef(BaseModel):
    id: str
    href: Optional[str] = None


class ZoneRef(BaseModel):
    name: str


# ── Request models ────────────────────────────────────────────────────

class TransitGatewayCreate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    global_routing: bool = Field(False, alias="global")
    resource_group: Optional[ResourceGroupRef] = None
    gre_enhanced_route_propagation: Optional[bool] = None

    model_config = {"populate_by_name": True}


class TransitGatewayUpdate(BaseModel):
    name: Optional[str] = None
    global_routing: Optional[bool] = Field(None, alias="global")
    gre_enhanced_route_propagation: Optional[bool] = None

    model_config = {"populate_by_name": True}


class TransitGatewayConnectionCreate(BaseModel):
    network_type: Optional[str] = None
    network_id: Optional[str] = None
    name: Optional[str] = None
    network_account_id: Optional[str] = None
    zone: Optional[ZoneRef] = None
    prefix_filters_default: Optional[str] = None


# ── Response models ───────────────────────────────────────────────────

class TransitGateway(BaseModel):
    id: str
    crn: str
    name: str
    location: str
    status: str
    global_routing: bool
    created_at: str
    updated_at: Optional[str] = None
    connection_count: int = 0
    resource_group: Optional[ResourceGroupRef] = None

    def to_response(self) -> dict:
        """Serialize to IBM Cloud API shape (global_routing → 'global')."""
        d = self.model_dump()
        d["global"] = d.pop("global_routing")
        return d


class TransitGatewayConnection(BaseModel):
    id: str
    name: str
    network_type: str
    network_id: Optional[str] = None
    status: str
    request_status: str
    created_at: str
    updated_at: str
    zone: Optional[ZoneRef] = None
    prefix_filters_default: Optional[str] = None
