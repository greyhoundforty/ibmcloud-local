"""Integration tests for the Resource Manager API (/v2/resource_groups)."""

import pytest

from src.state.store import store as global_store
from src.providers.resource_manager import DEFAULT_RESOURCE_GROUP_ID


@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    # Re-seed the default resource group (normally done in lifespan)
    from src.providers.resource_manager import ensure_default_resource_group
    ensure_default_resource_group()
    yield
    global_store.reset()


@pytest.fixture(scope="module")
def client(auth_client):
    return auth_client


# ── Default resource group ────────────────────────────────────────────

def test_default_resource_group_exists(client):
    r = client.get(f"/v2/resource_groups/{DEFAULT_RESOURCE_GROUP_ID}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == DEFAULT_RESOURCE_GROUP_ID
    assert data["name"] == "Default"
    assert data["default"] is True


def test_list_includes_default(client):
    r = client.get("/v2/resource_groups")
    assert r.status_code == 200
    ids = [rg["id"] for rg in r.json()["resources"]]
    assert DEFAULT_RESOURCE_GROUP_ID in ids


# ── CRUD ──────────────────────────────────────────────────────────────

def test_create_resource_group(client):
    r = client.post("/v2/resource_groups", json={"name": "my-team"})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "my-team"
    assert data["state"] == "ACTIVE"
    assert data["crn"].startswith("crn:v1:bluemix")


def test_list_resource_groups(client):
    client.post("/v2/resource_groups", json={"name": "team-a"})
    client.post("/v2/resource_groups", json={"name": "team-b"})
    r = client.get("/v2/resource_groups")
    assert r.status_code == 200
    assert r.json()["rows_count"] >= 3  # default + 2


def test_get_resource_group(client):
    rg_id = client.post("/v2/resource_groups", json={"name": "get-me"}).json()["id"]
    r = client.get(f"/v2/resource_groups/{rg_id}")
    assert r.status_code == 200
    assert r.json()["id"] == rg_id


def test_get_resource_group_not_found(client):
    r = client.get("/v2/resource_groups/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_update_resource_group(client):
    rg_id = client.post("/v2/resource_groups", json={"name": "old-name"}).json()["id"]
    r = client.patch(f"/v2/resource_groups/{rg_id}", json={"name": "new-name"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_update_resource_group_not_found(client):
    r = client.patch("/v2/resource_groups/ghost", json={"name": "x"})
    assert r.status_code == 404


def test_delete_resource_group(client):
    rg_id = client.post("/v2/resource_groups", json={"name": "temp"}).json()["id"]
    r = client.delete(f"/v2/resource_groups/{rg_id}")
    assert r.status_code == 204
    assert client.get(f"/v2/resource_groups/{rg_id}").status_code == 404


def test_delete_default_resource_group_forbidden(client):
    r = client.delete(f"/v2/resource_groups/{DEFAULT_RESOURCE_GROUP_ID}")
    assert r.status_code == 409
    assert "errors" in r.json()


def test_delete_resource_group_not_found(client):
    r = client.delete("/v2/resource_groups/ghost")
    assert r.status_code == 404
