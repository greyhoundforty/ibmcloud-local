"""
Integration tests for Load Balancer endpoints.

All tests written RED-first — must fail before the provider exists.
Hierarchy: load balancer → listeners → pools → members

Run: pytest tests/test_load_balancer_api.py -v
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

def create_vpc(client, name="lb-test-vpc"):
    return client.post("/v1/vpcs", json={"name": name}).json()["id"]


def create_subnet(client, vpc_id, name="lb-subnet", cidr="10.60.0.0/24"):
    return client.post("/v1/subnets", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": "us-south-1"},
        "ipv4_cidr_block": cidr,
    }).json()["id"]


def create_lb(client, subnet_id, name="test-lb", is_public=True):
    return client.post("/v1/load_balancers", json={
        "name": name,
        "is_public": is_public,
        "subnets": [{"id": subnet_id}],
    })


def create_listener(client, lb_id, port=80, protocol="http"):
    return client.post(f"/v1/load_balancers/{lb_id}/listeners", json={
        "port": port,
        "protocol": protocol,
    })


def create_pool(client, lb_id, name="test-pool", algorithm="round_robin", protocol="http"):
    return client.post(f"/v1/load_balancers/{lb_id}/pools", json={
        "name": name,
        "algorithm": algorithm,
        "protocol": protocol,
        "health_monitor": {"type": "http", "delay": 5, "max_retries": 2, "timeout": 2},
    })


def create_member(client, lb_id, pool_id, address="10.0.0.5", port=80):
    return client.post(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members", json={
        "target": {"address": address},
        "port": port,
    })


# ── Load Balancer CRUD ─────────────────────────────────────────────────

def test_list_load_balancers_empty(client):
    r = client.get("/v1/load_balancers")
    assert r.status_code == 200
    assert r.json()["load_balancers"] == []


def test_create_load_balancer(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    r = create_lb(client, subnet_id)
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-lb"
    assert data["id"].startswith("r006-")
    assert data["is_public"] is True
    assert len(data["subnets"]) == 1
    assert data["subnets"][0]["id"] == subnet_id


def test_create_load_balancer_starts_pending(client):
    """New LB should start with provisioning_status=create_pending."""
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    r = create_lb(client, subnet_id)
    assert r.status_code == 201
    data = r.json()
    assert data["provisioning_status"] == "create_pending"


def test_create_load_balancer_has_hostname(client):
    """LB response should include a fake hostname."""
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    r = create_lb(client, subnet_id)
    data = r.json()
    assert "hostname" in data
    assert data["hostname"].endswith(".lb.appdomain.cloud")


def test_create_load_balancer_invalid_subnet(client):
    r = client.post("/v1/load_balancers", json={
        "name": "bad-lb",
        "is_public": True,
        "subnets": [{"id": "no-such-subnet"}],
    })
    assert r.status_code == 404


def test_list_load_balancers(client):
    vpc_id = create_vpc(client)
    s1 = create_subnet(client, vpc_id, name="s1", cidr="10.61.0.0/24")
    s2 = create_subnet(client, vpc_id, name="s2", cidr="10.62.0.0/24")
    create_lb(client, s1, name="lb-a")
    create_lb(client, s2, name="lb-b")
    r = client.get("/v1/load_balancers")
    assert len(r.json()["load_balancers"]) == 2


def test_get_load_balancer(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}")
    assert r.status_code == 200
    assert r.json()["id"] == lb_id


def test_get_load_balancer_not_found(client):
    r = client.get("/v1/load_balancers/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_load_balancer(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id, name="old-lb").json()["id"]
    r = client.patch(f"/v1/load_balancers/{lb_id}", json={"name": "new-lb"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-lb"


def test_patch_load_balancer_not_found(client):
    r = client.patch("/v1/load_balancers/ghost", json={"name": "x"})
    assert r.status_code == 404


def test_delete_load_balancer(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/load_balancers/{lb_id}").status_code == 404


def test_delete_load_balancer_not_found(client):
    r = client.delete("/v1/load_balancers/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── Listeners ─────────────────────────────────────────────────────────

def test_list_listeners_empty(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/listeners")
    assert r.status_code == 200
    assert r.json()["listeners"] == []


def test_create_listener(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = create_listener(client, lb_id, port=443, protocol="https")
    assert r.status_code == 201
    data = r.json()
    assert data["port"] == 443
    assert data["protocol"] == "https"
    assert data["id"].startswith("r006-")


def test_create_listener_on_missing_lb(client):
    r = client.post("/v1/load_balancers/ghost/listeners", json={"port": 80, "protocol": "http"})
    assert r.status_code == 404


def test_get_listener(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    listener_id = create_listener(client, lb_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/listeners/{listener_id}")
    assert r.status_code == 200
    assert r.json()["id"] == listener_id


def test_get_listener_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/listeners/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_listener(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    listener_id = create_listener(client, lb_id, port=80).json()["id"]
    r = client.patch(f"/v1/load_balancers/{lb_id}/listeners/{listener_id}", json={"port": 8080})
    assert r.status_code == 200
    assert r.json()["port"] == 8080


def test_delete_listener(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    listener_id = create_listener(client, lb_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/listeners/{listener_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/load_balancers/{lb_id}/listeners/{listener_id}").status_code == 404


def test_delete_listener_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/listeners/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── Pools ─────────────────────────────────────────────────────────────

def test_list_pools_empty(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools")
    assert r.status_code == 200
    assert r.json()["pools"] == []


def test_create_pool(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = create_pool(client, lb_id)
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-pool"
    assert data["algorithm"] == "round_robin"
    assert data["id"].startswith("r006-")


def test_create_pool_on_missing_lb(client):
    r = client.post("/v1/load_balancers/ghost/pools", json={
        "name": "p", "algorithm": "round_robin", "protocol": "http",
        "health_monitor": {"type": "http", "delay": 5, "max_retries": 2, "timeout": 2},
    })
    assert r.status_code == 404


def test_get_pool(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}")
    assert r.status_code == 200
    assert r.json()["id"] == pool_id


def test_get_pool_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_pool(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id, name="old-pool").json()["id"]
    r = client.patch(f"/v1/load_balancers/{lb_id}/pools/{pool_id}", json={"name": "new-pool"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-pool"


def test_delete_pool(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/pools/{pool_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}").status_code == 404


def test_delete_pool_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/pools/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── Pool Members ───────────────────────────────────────────────────────

def test_list_members_empty(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members")
    assert r.status_code == 200
    assert r.json()["members"] == []


def test_create_member(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = create_member(client, lb_id, pool_id, address="10.0.0.5", port=8080)
    assert r.status_code == 201
    data = r.json()
    assert data["target"]["address"] == "10.0.0.5"
    assert data["port"] == 8080
    assert data["id"].startswith("r006-")
    assert data["health"] == "ok"
    assert data["weight"] == 50


def test_create_member_on_missing_pool(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    r = client.post(f"/v1/load_balancers/{lb_id}/pools/ghost/members", json={
        "target": {"address": "10.0.0.5"}, "port": 80,
    })
    assert r.status_code == 404


def test_list_members_after_create(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    create_member(client, lb_id, pool_id, "10.0.0.5", 80)
    create_member(client, lb_id, pool_id, "10.0.0.6", 80)
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members")
    assert len(r.json()["members"]) == 2


def test_get_member(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    member_id = create_member(client, lb_id, pool_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}")
    assert r.status_code == 200
    assert r.json()["id"] == member_id


def test_get_member_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_patch_member(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    member_id = create_member(client, lb_id, pool_id).json()["id"]
    r = client.patch(
        f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}",
        json={"weight": 100, "port": 9090},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["weight"] == 100
    assert data["port"] == 9090


def test_delete_member(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    member_id = create_member(client, lb_id, pool_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}")
    assert r.status_code == 204
    r2 = client.get(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/{member_id}")
    assert r2.status_code == 404


def test_delete_member_not_found(client):
    vpc_id = create_vpc(client)
    subnet_id = create_subnet(client, vpc_id)
    lb_id = create_lb(client, subnet_id).json()["id"]
    pool_id = create_pool(client, lb_id).json()["id"]
    r = client.delete(f"/v1/load_balancers/{lb_id}/pools/{pool_id}/members/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()
