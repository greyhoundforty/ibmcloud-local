"""
Integration tests for Public Gateway endpoints.

All tests written RED-first — must fail before the provider exists.
Run: pytest tests/test_public_gateway_api.py -v
"""

import pytest

from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group


@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


@pytest.fixture(scope="module")
def client(auth_client):
    return auth_client


# ── Helpers ────────────────────────────────────────────────────────────

def create_vpc(client, name="pgw-test-vpc"):
    return client.post("/v1/vpcs", json={"name": name}).json()["id"]


def create_subnet(client, vpc_id, name="pgw-subnet", cidr="10.50.0.0/24", zone="us-south-1"):
    return client.post("/v1/subnets", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": zone},
        "ipv4_cidr_block": cidr,
    }).json()


def create_gateway(client, vpc_id, zone="us-south-1", name="test-pgw"):
    return client.post("/v1/public_gateways", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": zone},
    })


# ── Public Gateway CRUD ────────────────────────────────────────────────

def test_list_public_gateways_empty(client):
    r = client.get("/v1/public_gateways")
    assert r.status_code == 200
    assert r.json()["public_gateways"] == []


def test_create_public_gateway(client):
    vpc_id = create_vpc(client)
    r = create_gateway(client, vpc_id)
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-pgw"
    assert data["id"].startswith("r006-")
    assert data["vpc"]["id"] == vpc_id
    assert data["zone"]["name"] == "us-south-1"
    assert data["status"] == "available"


def test_create_public_gateway_auto_assigns_floating_ip(client):
    """A new public gateway should have a floating_ip embedded in the response."""
    vpc_id = create_vpc(client)
    r = create_gateway(client, vpc_id)
    assert r.status_code == 201
    data = r.json()
    assert "floating_ip" in data
    assert data["floating_ip"] is not None
    fip = data["floating_ip"]
    assert "address" in fip
    parts = fip["address"].split(".")
    assert len(parts) == 4


def test_create_public_gateway_invalid_vpc(client):
    r = client.post("/v1/public_gateways", json={
        "name": "bad-pgw",
        "vpc": {"id": "no-vpc"},
        "zone": {"name": "us-south-1"},
    })
    assert r.status_code == 404


def test_create_public_gateway_duplicate_zone_vpc_rejected(client):
    """Only one public gateway allowed per zone per VPC."""
    vpc_id = create_vpc(client)
    create_gateway(client, vpc_id, zone="us-south-1", name="pgw-1")
    r = create_gateway(client, vpc_id, zone="us-south-1", name="pgw-2")
    assert r.status_code == 409
    assert "errors" in r.json()


def test_create_public_gateway_different_zones_allowed(client):
    """Different zones within the same VPC are independent."""
    vpc_id = create_vpc(client)
    r1 = create_gateway(client, vpc_id, zone="us-south-1", name="pgw-z1")
    r2 = create_gateway(client, vpc_id, zone="us-south-2", name="pgw-z2")
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_list_public_gateways(client):
    vpc_id = create_vpc(client)
    create_gateway(client, vpc_id, zone="us-south-1", name="pgw-a")
    create_gateway(client, vpc_id, zone="us-south-2", name="pgw-b")
    r = client.get("/v1/public_gateways")
    assert r.status_code == 200
    assert len(r.json()["public_gateways"]) == 2


def test_get_public_gateway(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    r = client.get(f"/v1/public_gateways/{pgw_id}")
    assert r.status_code == 200
    assert r.json()["id"] == pgw_id


def test_get_public_gateway_not_found(client):
    r = client.get("/v1/public_gateways/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_public_gateway(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id, name="old-pgw").json()["id"]
    r = client.patch(f"/v1/public_gateways/{pgw_id}", json={"name": "new-pgw"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-pgw"


def test_patch_public_gateway_not_found(client):
    r = client.patch("/v1/public_gateways/ghost", json={"name": "x"})
    assert r.status_code == 404


def test_delete_public_gateway(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    r = client.delete(f"/v1/public_gateways/{pgw_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/public_gateways/{pgw_id}").status_code == 404


def test_delete_public_gateway_not_found(client):
    r = client.delete("/v1/public_gateways/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_delete_public_gateway_while_attached_fails(client):
    """Cannot delete a gateway that is still attached to a subnet."""
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    subnet = create_subnet(client, vpc_id)
    subnet_id = subnet["id"]
    # Attach gateway to subnet
    client.put(f"/v1/subnets/{subnet_id}/public_gateway", json={"id": pgw_id})
    r = client.delete(f"/v1/public_gateways/{pgw_id}")
    assert r.status_code == 409
    assert "errors" in r.json()


# ── Subnet / Public Gateway attachment ────────────────────────────────

def test_attach_public_gateway_to_subnet(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    subnet_id = create_subnet(client, vpc_id)["id"]
    r = client.put(f"/v1/subnets/{subnet_id}/public_gateway", json={"id": pgw_id})
    assert r.status_code == 201
    data = r.json()
    assert data["id"] == pgw_id


def test_get_subnet_public_gateway(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    subnet_id = create_subnet(client, vpc_id)["id"]
    client.put(f"/v1/subnets/{subnet_id}/public_gateway", json={"id": pgw_id})
    r = client.get(f"/v1/subnets/{subnet_id}/public_gateway")
    assert r.status_code == 200
    assert r.json()["id"] == pgw_id


def test_get_subnet_public_gateway_not_attached(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)["id"]
    r = client.get(f"/v1/subnets/{subnet_id}/public_gateway")
    assert r.status_code == 404


def test_detach_public_gateway_from_subnet(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    subnet_id = create_subnet(client, vpc_id)["id"]
    client.put(f"/v1/subnets/{subnet_id}/public_gateway", json={"id": pgw_id})
    r = client.delete(f"/v1/subnets/{subnet_id}/public_gateway")
    assert r.status_code == 204
    assert client.get(f"/v1/subnets/{subnet_id}/public_gateway").status_code == 404


def test_attach_nonexistent_gateway_fails(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)["id"]
    r = client.put(f"/v1/subnets/{subnet_id}/public_gateway", json={"id": "ghost"})
    assert r.status_code == 404


def test_attach_gateway_to_nonexistent_subnet_fails(client):
    vpc_id = create_vpc(client)
    pgw_id = create_gateway(client, vpc_id).json()["id"]
    r = client.put("/v1/subnets/ghost/public_gateway", json={"id": pgw_id})
    assert r.status_code == 404
