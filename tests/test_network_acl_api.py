"""
Integration tests for Network ACL endpoints.

All tests are written RED-first — they must fail before the provider exists.
Run: pytest tests/test_network_acl_api.py -v
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

def create_vpc(client, name="acl-test-vpc"):
    return client.post("/v1/vpcs", json={"name": name}).json()["id"]


def create_subnet(client, vpc_id, name="acl-test-subnet", cidr="10.10.0.0/24"):
    return client.post("/v1/subnets", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": "us-south-1"},
        "ipv4_cidr_block": cidr,
    })


def create_acl(client, vpc_id, name="test-acl"):
    return client.post("/v1/network_acls", json={
        "name": name,
        "vpc": {"id": vpc_id},
    })


# ── Network ACL CRUD ───────────────────────────────────────────────────

def test_list_network_acls_empty(client):
    r = client.get("/v1/network_acls")
    assert r.status_code == 200
    assert r.json()["network_acls"] == []


def test_create_network_acl(client):
    vpc_id = create_vpc(client)
    r = create_acl(client, vpc_id, "my-acl")
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "my-acl"
    assert data["id"].startswith("r006-")
    assert data["vpc"]["id"] == vpc_id


def test_create_network_acl_auto_creates_default_rule(client):
    """A newly created ACL should have at least one default allow-all rule."""
    vpc_id = create_vpc(client)
    r = create_acl(client, vpc_id)
    data = r.json()
    assert len(data["rules"]) >= 1
    rule = data["rules"][0]
    assert rule["action"] in ("allow", "deny")
    assert rule["direction"] in ("inbound", "outbound")


def test_create_network_acl_invalid_vpc(client):
    r = client.post("/v1/network_acls", json={"name": "bad", "vpc": {"id": "no-vpc"}})
    assert r.status_code == 404


def test_list_network_acls(client):
    vpc_id = create_vpc(client)
    create_acl(client, vpc_id, "acl-a")
    create_acl(client, vpc_id, "acl-b")
    r = client.get("/v1/network_acls")
    assert r.status_code == 200
    assert len(r.json()["network_acls"]) == 2


def test_get_network_acl(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.get(f"/v1/network_acls/{acl_id}")
    assert r.status_code == 200
    assert r.json()["id"] == acl_id


def test_get_network_acl_not_found(client):
    r = client.get("/v1/network_acls/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_network_acl(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id, "old-name").json()["id"]
    r = client.patch(f"/v1/network_acls/{acl_id}", json={"name": "new-name"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_patch_network_acl_not_found(client):
    r = client.patch("/v1/network_acls/ghost", json={"name": "x"})
    assert r.status_code == 404


def test_delete_network_acl(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.delete(f"/v1/network_acls/{acl_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/network_acls/{acl_id}").status_code == 404


def test_delete_network_acl_not_found(client):
    r = client.delete("/v1/network_acls/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_delete_network_acl_attached_to_subnet_fails(client):
    """Cannot delete an ACL that is still attached to a subnet."""
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id, "busy-acl").json()["id"]
    create_subnet(client, vpc_id, name="acl-subnet", cidr="10.20.0.0/24")
    # Attach the ACL to the subnet
    subnets = client.get("/v1/subnets").json()["subnets"]
    subnet_id = subnets[0]["id"]
    client.patch(f"/v1/subnets/{subnet_id}", json={"network_acl": {"id": acl_id}})
    r = client.delete(f"/v1/network_acls/{acl_id}")
    assert r.status_code == 409
    assert "errors" in r.json()


# ── Network ACL Rules ──────────────────────────────────────────────────

def test_list_acl_rules(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.get(f"/v1/network_acls/{acl_id}/rules")
    assert r.status_code == 200
    assert "rules" in r.json()


def test_create_acl_rule(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.post(f"/v1/network_acls/{acl_id}/rules", json={
        "name": "allow-http",
        "action": "allow",
        "direction": "inbound",
        "protocol": "tcp",
        "source": "0.0.0.0/0",
        "destination": "0.0.0.0/0",
        "port_min": 80,
        "port_max": 80,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["action"] == "allow"
    assert data["direction"] == "inbound"
    assert data["protocol"] == "tcp"
    assert data["port_min"] == 80
    assert data["id"].startswith("r006-")


def test_create_acl_rule_deny(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.post(f"/v1/network_acls/{acl_id}/rules", json={
        "name": "deny-all-outbound",
        "action": "deny",
        "direction": "outbound",
        "protocol": "all",
        "source": "0.0.0.0/0",
        "destination": "0.0.0.0/0",
    })
    assert r.status_code == 201
    assert r.json()["action"] == "deny"


def test_create_acl_rule_on_missing_acl(client):
    r = client.post("/v1/network_acls/ghost/rules", json={
        "name": "r", "action": "allow", "direction": "inbound",
        "protocol": "all", "source": "0.0.0.0/0", "destination": "0.0.0.0/0",
    })
    assert r.status_code == 404


def test_get_acl_rule(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    rule_id = client.post(f"/v1/network_acls/{acl_id}/rules", json={
        "name": "get-me", "action": "allow", "direction": "inbound",
        "protocol": "all", "source": "0.0.0.0/0", "destination": "0.0.0.0/0",
    }).json()["id"]
    r = client.get(f"/v1/network_acls/{acl_id}/rules/{rule_id}")
    assert r.status_code == 200
    assert r.json()["id"] == rule_id


def test_get_acl_rule_not_found(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.get(f"/v1/network_acls/{acl_id}/rules/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_acl_rule(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    rule_id = client.post(f"/v1/network_acls/{acl_id}/rules", json={
        "name": "patch-me", "action": "allow", "direction": "inbound",
        "protocol": "tcp", "source": "0.0.0.0/0", "destination": "0.0.0.0/0",
        "port_min": 22, "port_max": 22,
    }).json()["id"]
    r = client.patch(f"/v1/network_acls/{acl_id}/rules/{rule_id}", json={
        "action": "deny",
        "port_min": 2222,
        "port_max": 2222,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "deny"
    assert data["port_min"] == 2222


def test_patch_acl_rule_not_found(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.patch(f"/v1/network_acls/{acl_id}/rules/ghost", json={"action": "deny"})
    assert r.status_code == 404
    assert "errors" in r.json()


def test_delete_acl_rule(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    rule_id = client.post(f"/v1/network_acls/{acl_id}/rules", json={
        "name": "del-me", "action": "allow", "direction": "inbound",
        "protocol": "all", "source": "0.0.0.0/0", "destination": "0.0.0.0/0",
    }).json()["id"]
    r = client.delete(f"/v1/network_acls/{acl_id}/rules/{rule_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/network_acls/{acl_id}/rules/{rule_id}").status_code == 404


def test_delete_acl_rule_not_found(client):
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id).json()["id"]
    r = client.delete(f"/v1/network_acls/{acl_id}/rules/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── Subnet + ACL integration ───────────────────────────────────────────

def test_subnet_gets_auto_acl_on_creation(client):
    """A subnet created without specifying a network_acl should get one automatically."""
    vpc_id = create_vpc(client)
    r = create_subnet(client, vpc_id)
    assert r.status_code == 201
    data = r.json()
    assert "network_acl" in data
    assert data["network_acl"] is not None
    assert data["network_acl"]["id"] != ""


def test_subnet_create_with_explicit_acl(client):
    """Creating a subnet with a network_acl reference should bind that ACL."""
    vpc_id = create_vpc(client)
    acl_id = create_acl(client, vpc_id, "explicit-acl").json()["id"]
    r = client.post("/v1/subnets", json={
        "name": "acl-bound-subnet",
        "vpc": {"id": vpc_id},
        "zone": {"name": "us-south-1"},
        "ipv4_cidr_block": "10.30.0.0/24",
        "network_acl": {"id": acl_id},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["network_acl"]["id"] == acl_id


def test_subnet_acl_is_queryable_after_creation(client):
    """The auto-created ACL for a subnet should be retrievable by ID."""
    vpc_id = create_vpc(client)
    subnet = create_subnet(client, vpc_id).json()
    acl_id = subnet["network_acl"]["id"]
    r = client.get(f"/v1/network_acls/{acl_id}")
    assert r.status_code == 200
    assert r.json()["id"] == acl_id
