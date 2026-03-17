"""
Resource Manager models — mirrors the IBM Cloud Resource Manager API.

Real API docs: https://cloud.ibm.com/apidocs/resource-controller/resource-manager
Base URL:      https://resource-controller.cloud.ibm.com
Emulator path: /v2/resource_groups

Resource groups are the top-level organizational unit in IBM Cloud. Every
resource (VPC, subnet, instance, etc.) belongs to exactly one resource group.
"""

from __future__ import annotations
from pydantic import BaseModel


class ResourceGroupCreate(BaseModel):
    """Request body for POST /v2/resource_groups."""
    name: str


class ResourceGroupUpdate(BaseModel):
    """Request body for PATCH /v2/resource_groups/{id}."""
    name: str


class ResourceGroup(BaseModel):
    """
    Full resource group resource.
    Matches GET /v2/resource_groups/{id} response shape.
    """
    id: str
    name: str
    crn: str = ""
    account_id: str = "local-emulator"
    state: str = "ACTIVE"
    default: bool = False
    created_at: str = ""
    updated_at: str = ""
