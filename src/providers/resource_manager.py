"""
Resource Manager Provider — emulates the IBM Cloud Resource Manager API.

Handles /v2/resource_groups endpoints so that VPCs, instances, and other
resources can reference real resource group IDs stored in the emulator.

Real API: https://cloud.ibm.com/apidocs/resource-controller/resource-manager

A "Default" resource group is created on first startup so that callers
that don't specify a resource_group get a valid reference automatically.
"""

from fastapi import Query, Request
from fastapi.responses import JSONResponse

from src.providers.base import BaseProvider
from src.models.resource_manager import ResourceGroup, ResourceGroupCreate, ResourceGroupUpdate
from src.state.store import store

# Stable ID for the auto-created default group so callers can rely on it.
DEFAULT_RESOURCE_GROUP_ID = "default-resource-group"


def ensure_default_resource_group():
    """
    Create the built-in Default resource group if it doesn't exist yet.
    Called from the server lifespan so it's always present at startup.
    """
    if store.get("resource_groups", DEFAULT_RESOURCE_GROUP_ID):
        return
    rg = ResourceGroup(
        id=DEFAULT_RESOURCE_GROUP_ID,
        name="Default",
        crn=f"crn:v1:bluemix:public:resource-controller::a/local-emulator::resource-group:{DEFAULT_RESOURCE_GROUP_ID}",
        default=True,
    )
    store.put("resource_groups", DEFAULT_RESOURCE_GROUP_ID, rg.model_dump())


class ResourceManagerProvider(BaseProvider):
    """Emulates the IBM Cloud Resource Manager API (/v2/resource_groups)."""

    service_name = "resource-manager"
    api_version = "v2"
    description = "Resource Manager (resource groups)"
    api_base_url = "https://resource-controller.cloud.ibm.com"

    def register_routes(self):
        self.router.get("/v2/resource_groups")(self.list_resource_groups)
        self.router.post("/v2/resource_groups")(self.create_resource_group)
        self.router.get("/v2/resource_groups/{rg_id}")(self.get_resource_group)
        self.router.patch("/v2/resource_groups/{rg_id}")(self.update_resource_group)
        self.router.delete("/v2/resource_groups/{rg_id}")(self.delete_resource_group)

    async def list_resource_groups(
        self,
        account_id: str = Query(None, description="Filter by account ID (ignored in emulator)"),
    ):
        """GET /v2/resource_groups — List all resource groups."""
        groups = store.list("resource_groups")
        return {"resources": groups, "rows_count": len(groups)}

    async def create_resource_group(self, request: Request):
        """POST /v2/resource_groups — Create a new resource group."""
        body = await request.json()
        payload = ResourceGroupCreate(**body)

        rg_id = store.generate_id()
        rg = ResourceGroup(
            id=rg_id,
            name=payload.name,
            crn=f"crn:v1:bluemix:public:resource-controller::a/local-emulator::resource-group:{rg_id}",
        )
        store.put("resource_groups", rg_id, rg.model_dump())
        return JSONResponse(status_code=201, content=store.get("resource_groups", rg_id))

    async def get_resource_group(self, rg_id: str):
        """GET /v2/resource_groups/{id}."""
        rg = store.get("resource_groups", rg_id)
        if not rg:
            return self.not_found("ResourceGroup", rg_id)
        return rg

    async def update_resource_group(self, rg_id: str, request: Request):
        """PATCH /v2/resource_groups/{id} — Rename a resource group."""
        if not store.get("resource_groups", rg_id):
            return self.not_found("ResourceGroup", rg_id)
        body = await request.json()
        payload = ResourceGroupUpdate(**body)
        updated = store.update("resource_groups", rg_id, {"name": payload.name})
        return updated

    async def delete_resource_group(self, rg_id: str):
        """
        DELETE /v2/resource_groups/{id}.
        The Default group cannot be deleted (matches real IBM Cloud behavior).
        """
        if rg_id == DEFAULT_RESOURCE_GROUP_ID:
            return self.error_response(
                409, "resource_group_in_use",
                "The Default resource group cannot be deleted."
            )
        if not store.get("resource_groups", rg_id):
            return self.not_found("ResourceGroup", rg_id)
        store.delete("resource_groups", rg_id)
        return JSONResponse(status_code=204, content=None)
