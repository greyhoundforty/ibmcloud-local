"""
Integration tests for the VPC API endpoints.

Uses FastAPI's TestClient to exercise the full request/response cycle.
The module-level `store` singleton is reset between tests via the
/_emulator/reset endpoint so tests are fully isolated.
"""

import pytest

from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group


@pytest.fixture(autouse=True)
def reset_state():
    """Wipe all emulator state before each test, then re-seed the default resource group."""
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


@pytest.fixture(scope="module")
def client(auth_client):
    return auth_client


# ── Helpers ───────────────────────────────────────────────────────────

def create_vpc(client, name="test-vpc"):
    return client.post("/v1/vpcs", json={"name": name})


def create_subnet(client, vpc_id, name="test-subnet", cidr="10.0.0.0/24"):
    return client.post("/v1/subnets", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": "us-south-1"},
        "ipv4_cidr_block": cidr,
    })


def create_instance(client, vpc_id, subnet_id, name="test-instance"):
    return client.post("/v1/instances", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "zone": {"name": "us-south-1"},
        "profile": {"name": "bx2-2x8"},
        "image": {"id": "ibm-ubuntu-22-04"},
        "primary_network_interface": {
            "name": "eth0",
            "subnet": {"id": subnet_id},
        },
    })


# ── Health / Control Plane ────────────────────────────────────────────

def test_health(client):
    r = client.get("/_emulator/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert "vpc" in data["services"]


def test_dashboard_overview(client):
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert data["emulator"] == "ibmcloud-local"
    assert "services" in data


def test_dashboard_routes(client):
    r = client.get("/api/dashboard/routes")
    assert r.status_code == 200
    data = r.json()
    assert data["total_routes"] > 0
    paths = [route["path"] for route in data["routes"]]
    assert any("/v1/vpcs" in p for p in paths)


def test_emulator_reset(client):
    create_vpc(client, "to-be-wiped")
    client.post("/_emulator/reset")
    r = client.get("/v1/vpcs")
    assert r.json()["vpcs"] == []


def test_emulator_reset_namespace(client):
    create_vpc(client, "stays")
    # Create a security group separately via state store to test ns reset
    r = client.post("/_emulator/reset/instances")
    assert r.status_code == 200
    # VPCs should still exist
    assert len(client.get("/v1/vpcs").json()["vpcs"]) == 1


def test_dump_state(client):
    create_vpc(client, "state-vpc")
    r = client.get("/_emulator/state")
    assert r.status_code == 200
    data = r.json()
    assert "vpcs" in data["namespaces"]


# ── VPC CRUD ──────────────────────────────────────────────────────────

def test_create_vpc(client):
    r = create_vpc(client, "my-vpc")
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "my-vpc"
    assert data["status"] == "available"
    assert data["id"].startswith("r006-")
    assert data["crn"].startswith("crn:v1:bluemix:public:is")
    assert "default_security_group" in data


def test_create_vpc_auto_creates_security_group(client):
    r = create_vpc(client, "sg-test-vpc")
    vpc = r.json()
    sg_id = vpc["default_security_group"]["id"]

    r2 = client.get(f"/v1/security_groups/{sg_id}")
    assert r2.status_code == 200
    sg = r2.json()
    assert sg["name"] == "sg-test-vpc-default-sg"


def test_list_vpcs_empty(client):
    r = client.get("/v1/vpcs")
    assert r.status_code == 200
    assert r.json()["vpcs"] == []


def test_list_vpcs(client):
    create_vpc(client, "vpc-a")
    create_vpc(client, "vpc-b")
    r = client.get("/v1/vpcs")
    assert len(r.json()["vpcs"]) == 2


def test_get_vpc(client):
    vpc_id = create_vpc(client, "get-me").json()["id"]
    r = client.get(f"/v1/vpcs/{vpc_id}")
    assert r.status_code == 200
    assert r.json()["id"] == vpc_id


def test_get_vpc_not_found(client):
    r = client.get("/v1/vpcs/does-not-exist")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_update_vpc(client):
    vpc_id = create_vpc(client, "old-name").json()["id"]
    r = client.patch(f"/v1/vpcs/{vpc_id}", json={"name": "new-name"})
    assert r.status_code == 200
    assert r.json()["name"] == "new-name"


def test_update_vpc_not_found(client):
    r = client.patch("/v1/vpcs/ghost", json={"name": "x"})
    assert r.status_code == 404


def test_delete_vpc(client):
    vpc_id = create_vpc(client, "del-vpc").json()["id"]
    # Remove the auto-created subnet dependency (none here), just delete
    r = client.delete(f"/v1/vpcs/{vpc_id}")
    assert r.status_code == 204


def test_delete_vpc_with_subnets_fails(client):
    vpc_id = create_vpc(client, "busy-vpc").json()["id"]
    create_subnet(client, vpc_id)
    r = client.delete(f"/v1/vpcs/{vpc_id}")
    assert r.status_code == 409
    assert "errors" in r.json()


def test_delete_vpc_not_found(client):
    r = client.delete("/v1/vpcs/ghost")
    assert r.status_code == 404


# ── Subnet CRUD ───────────────────────────────────────────────────────

def test_create_subnet(client):
    vpc_id = create_vpc(client).json()["id"]
    r = create_subnet(client, vpc_id, cidr="10.1.0.0/24")
    assert r.status_code == 201
    data = r.json()
    assert data["ipv4_cidr_block"] == "10.1.0.0/24"
    assert data["status"] == "available"
    assert data["vpc"]["id"] == vpc_id


def test_create_subnet_invalid_vpc(client):
    r = create_subnet(client, "no-such-vpc")
    assert r.status_code == 404


def test_create_subnet_cidr_overlap(client):
    vpc_id = create_vpc(client).json()["id"]
    create_subnet(client, vpc_id, name="s1", cidr="10.0.0.0/24")
    r = create_subnet(client, vpc_id, name="s2", cidr="10.0.0.0/25")  # overlaps
    assert r.status_code == 409
    assert "errors" in r.json()


def test_create_subnet_non_overlapping_cidrs(client):
    vpc_id = create_vpc(client).json()["id"]
    r1 = create_subnet(client, vpc_id, name="s1", cidr="10.0.0.0/24")
    r2 = create_subnet(client, vpc_id, name="s2", cidr="10.0.1.0/24")
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_list_subnets(client):
    vpc_id = create_vpc(client).json()["id"]
    create_subnet(client, vpc_id, name="s1", cidr="10.0.0.0/24")
    create_subnet(client, vpc_id, name="s2", cidr="10.0.1.0/24")
    r = client.get("/v1/subnets")
    assert len(r.json()["subnets"]) == 2


def test_get_subnet(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    r = client.get(f"/v1/subnets/{subnet_id}")
    assert r.status_code == 200
    assert r.json()["id"] == subnet_id


def test_get_subnet_not_found(client):
    r = client.get("/v1/subnets/ghost")
    assert r.status_code == 404


def test_update_subnet(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id, name="old").json()["id"]
    r = client.patch(f"/v1/subnets/{subnet_id}", json={"name": "new"})
    assert r.status_code == 200
    assert r.json()["name"] == "new"


def test_delete_subnet(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    r = client.delete(f"/v1/subnets/{subnet_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/subnets/{subnet_id}").status_code == 404


# ── Instance CRUD ─────────────────────────────────────────────────────

def test_create_instance(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    r = create_instance(client, vpc_id, subnet_id)
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "test-instance"
    assert data["status"] == "pending"
    assert data["id"].startswith("r006-")
    nic = data["primary_network_interface"]
    assert nic["subnet"]["id"] == subnet_id
    assert "address" in nic["primary_ip"]


def test_create_instance_invalid_vpc(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    r = create_instance(client, "bad-vpc", subnet_id)
    assert r.status_code == 404


def test_create_instance_invalid_subnet(client):
    vpc_id = create_vpc(client).json()["id"]
    r = create_instance(client, vpc_id, "bad-subnet")
    assert r.status_code == 404


def test_list_instances(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    create_instance(client, vpc_id, subnet_id, "i1")
    create_instance(client, vpc_id, subnet_id, "i2")
    r = client.get("/v1/instances")
    assert len(r.json()["instances"]) == 2


def test_get_instance(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    instance_id = create_instance(client, vpc_id, subnet_id).json()["id"]
    r = client.get(f"/v1/instances/{instance_id}")
    assert r.status_code == 200
    assert r.json()["id"] == instance_id


def test_get_instance_not_found(client):
    r = client.get("/v1/instances/ghost")
    assert r.status_code == 404


def test_update_instance(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    instance_id = create_instance(client, vpc_id, subnet_id).json()["id"]
    r = client.patch(f"/v1/instances/{instance_id}", json={"name": "renamed"})
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"


def test_delete_instance(client):
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    instance_id = create_instance(client, vpc_id, subnet_id).json()["id"]
    r = client.delete(f"/v1/instances/{instance_id}")
    assert r.status_code == 204


def test_delete_instance_not_found(client):
    r = client.delete("/v1/instances/ghost")
    assert r.status_code == 404


def test_instance_action_invalid_state(client):
    """stop on a pending instance should return 400."""
    vpc_id = create_vpc(client).json()["id"]
    subnet_id = create_subnet(client, vpc_id).json()["id"]
    instance_id = create_instance(client, vpc_id, subnet_id).json()["id"]
    # Instance is "pending" — stopping it should fail
    r = client.post(f"/v1/instances/{instance_id}/actions", json={"type": "stop"})
    assert r.status_code == 400
    assert "errors" in r.json()


def test_instance_action_on_missing_instance(client):
    r = client.post("/v1/instances/ghost/actions", json={"type": "start"})
    assert r.status_code == 404


# ── Security Group CRUD ───────────────────────────────────────────────

def test_create_security_group(client):
    vpc_id = create_vpc(client).json()["id"]
    r = client.post("/v1/security_groups", json={
        "name": "my-sg",
        "vpc": {"id": vpc_id},
        "rules": [],
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "my-sg"
    assert data["vpc"]["id"] == vpc_id


def test_create_security_group_invalid_vpc(client):
    r = client.post("/v1/security_groups", json={
        "name": "bad-sg",
        "vpc": {"id": "no-vpc"},
        "rules": [],
    })
    assert r.status_code == 404


def test_list_security_groups(client):
    # Creating a VPC auto-creates one default SG
    create_vpc(client, "v1")
    create_vpc(client, "v2")
    r = client.get("/v1/security_groups")
    assert r.status_code == 200
    assert len(r.json()["security_groups"]) == 2


def test_get_security_group(client):
    vpc_id = create_vpc(client).json()["id"]
    r = client.post("/v1/security_groups", json={
        "name": "get-sg", "vpc": {"id": vpc_id}, "rules": []
    })
    sg_id = r.json()["id"]
    r2 = client.get(f"/v1/security_groups/{sg_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == sg_id


def test_get_security_group_not_found(client):
    r = client.get("/v1/security_groups/ghost")
    assert r.status_code == 404


def test_delete_security_group(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = client.post("/v1/security_groups", json={
        "name": "del-sg", "vpc": {"id": vpc_id}, "rules": []
    }).json()["id"]
    r = client.delete(f"/v1/security_groups/{sg_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/security_groups/{sg_id}").status_code == 404


def test_delete_security_group_not_found(client):
    r = client.delete("/v1/security_groups/ghost")
    assert r.status_code == 404


# ── Floating IP CRUD ──────────────────────────────────────────────────

def test_create_floating_ip(client):
    r = client.post("/v1/floating_ips", json={
        "name": "my-fip",
        "zone": {"name": "us-south-1"},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "my-fip"
    assert data["status"] == "available"
    # Address should look like an IP
    parts = data["address"].split(".")
    assert len(parts) == 4


def test_list_floating_ips(client):
    client.post("/v1/floating_ips", json={"name": "fip1", "zone": {"name": "us-south-1"}})
    client.post("/v1/floating_ips", json={"name": "fip2", "zone": {"name": "us-south-1"}})
    r = client.get("/v1/floating_ips")
    assert len(r.json()["floating_ips"]) == 2


def test_get_floating_ip(client):
    fip_id = client.post("/v1/floating_ips", json={
        "name": "get-fip", "zone": {"name": "us-south-1"}
    }).json()["id"]
    r = client.get(f"/v1/floating_ips/{fip_id}")
    assert r.status_code == 200
    assert r.json()["id"] == fip_id


def test_get_floating_ip_not_found(client):
    r = client.get("/v1/floating_ips/ghost")
    assert r.status_code == 404


def test_delete_floating_ip(client):
    fip_id = client.post("/v1/floating_ips", json={
        "name": "del-fip", "zone": {"name": "us-south-1"}
    }).json()["id"]
    r = client.delete(f"/v1/floating_ips/{fip_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/floating_ips/{fip_id}").status_code == 404


def test_delete_floating_ip_not_found(client):
    r = client.delete("/v1/floating_ips/ghost")
    assert r.status_code == 404


# ── Resource group + DNS on VPC ───────────────────────────────────────

def test_create_vpc_with_known_resource_group(client):
    """Resource group name should be resolved from the store."""
    rg_id = client.post("/v2/resource_groups", json={"name": "my-team"}).json()["id"]
    r = client.post("/v1/vpcs", json={"name": "rg-vpc", "resource_group": {"id": rg_id}})
    assert r.status_code == 201
    data = r.json()
    assert data["resource_group"]["id"] == rg_id
    assert data["resource_group"]["name"] == "my-team"


def test_create_vpc_with_default_resource_group(client):
    """No resource_group in body → falls back to Default group."""
    r = create_vpc(client, "default-rg-vpc")
    assert r.status_code == 201
    rg = r.json()["resource_group"]
    assert rg["id"] == "default-resource-group"
    assert rg["name"] == "Default"


def test_create_vpc_with_unknown_resource_group_id(client):
    """Unknown RG id is stored as-is (emulator is permissive)."""
    r = client.post("/v1/vpcs", json={
        "name": "unknown-rg-vpc",
        "resource_group": {"id": "fee82deba12e4c0fb69c3b09d1f12345"},
    })
    assert r.status_code == 201
    assert r.json()["resource_group"]["id"] == "fee82deba12e4c0fb69c3b09d1f12345"


def test_create_vpc_with_dns_config(client):
    """Full dns block from the real API should be accepted and returned."""
    r = client.post("/v1/vpcs", json={
        "name": "dns-vpc",
        "address_prefix_management": "auto",
        "classic_access": False,
        "dns": {
            "enable_hub": True,
            "resolver": {
                "type": "manual",
                "manual_servers": [
                    {"address": "192.168.3.4", "zone_affinity": {"name": "us-south-1"}}
                ],
            },
        },
    })
    assert r.status_code == 201
    dns = r.json()["dns"]
    assert dns["enable_hub"] is True
    assert dns["resolver"]["type"] == "manual"
    assert dns["resolver"]["manual_servers"][0]["address"] == "192.168.3.4"


def test_create_vpc_dns_defaults(client):
    """VPC created without dns block returns sensible dns defaults."""
    r = create_vpc(client, "no-dns-vpc")
    dns = r.json()["dns"]
    assert dns["enable_hub"] is False
    assert dns["resolver"] is None


# ── Security Group Rules ──────────────────────────────────────────────

def _create_sg(client, vpc_id, name="rule-test-sg"):
    return client.post("/v1/security_groups", json={
        "name": name,
        "vpc": {"id": vpc_id},
        "rules": [],
    }).json()["id"]


def test_list_sg_rules_empty(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.get(f"/v1/security_groups/{sg_id}/rules")
    assert r.status_code == 200
    assert r.json()["rules"] == []


def test_create_sg_rule_tcp(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound",
        "protocol": "tcp",
        "port_min": 80,
        "port_max": 80,
        "remote": {"cidr_block": "0.0.0.0/0"},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["direction"] == "inbound"
    assert data["protocol"] == "tcp"
    assert data["port_min"] == 80
    assert data["port_max"] == 80
    assert data["id"].startswith("r006-")


def test_create_sg_rule_icmp(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "outbound",
        "protocol": "icmp",
        "remote": {"cidr_block": "0.0.0.0/0"},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["protocol"] == "icmp"
    assert data["direction"] == "outbound"


def test_create_sg_rule_all_protocol(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound",
        "protocol": "all",
        "remote": {"cidr_block": "10.0.0.0/8"},
    })
    assert r.status_code == 201
    assert r.json()["protocol"] == "all"


def test_list_sg_rules_after_create(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound", "protocol": "tcp",
        "port_min": 22, "port_max": 22,
        "remote": {"cidr_block": "0.0.0.0/0"},
    })
    client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "outbound", "protocol": "all",
        "remote": {"cidr_block": "0.0.0.0/0"},
    })
    r = client.get(f"/v1/security_groups/{sg_id}/rules")
    assert r.status_code == 200
    assert len(r.json()["rules"]) == 2


def test_get_sg_rule(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    rule_id = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound", "protocol": "tcp",
        "port_min": 443, "port_max": 443,
        "remote": {"cidr_block": "0.0.0.0/0"},
    }).json()["id"]
    r = client.get(f"/v1/security_groups/{sg_id}/rules/{rule_id}")
    assert r.status_code == 200
    assert r.json()["id"] == rule_id
    assert r.json()["port_min"] == 443


def test_get_sg_rule_not_found(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.get(f"/v1/security_groups/{sg_id}/rules/no-such-rule")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_get_sg_rule_sg_not_found(client):
    r = client.get("/v1/security_groups/ghost-sg/rules/any-rule")
    assert r.status_code == 404


def test_patch_sg_rule(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    rule_id = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound", "protocol": "tcp",
        "port_min": 8080, "port_max": 8080,
        "remote": {"cidr_block": "0.0.0.0/0"},
    }).json()["id"]
    r = client.patch(f"/v1/security_groups/{sg_id}/rules/{rule_id}", json={
        "port_min": 8000,
        "port_max": 9000,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["port_min"] == 8000
    assert data["port_max"] == 9000


def test_patch_sg_rule_not_found(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.patch(f"/v1/security_groups/{sg_id}/rules/ghost", json={"port_min": 1})
    assert r.status_code == 404
    assert "errors" in r.json()


def test_delete_sg_rule(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    rule_id = client.post(f"/v1/security_groups/{sg_id}/rules", json={
        "direction": "inbound", "protocol": "all",
        "remote": {"cidr_block": "0.0.0.0/0"},
    }).json()["id"]
    r = client.delete(f"/v1/security_groups/{sg_id}/rules/{rule_id}")
    assert r.status_code == 204
    assert client.get(f"/v1/security_groups/{sg_id}/rules/{rule_id}").status_code == 404


def test_delete_sg_rule_not_found(client):
    vpc_id = create_vpc(client).json()["id"]
    sg_id = _create_sg(client, vpc_id)
    r = client.delete(f"/v1/security_groups/{sg_id}/rules/ghost")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_create_sg_rule_on_missing_sg(client):
    r = client.post("/v1/security_groups/ghost/rules", json={
        "direction": "inbound", "protocol": "all",
        "remote": {"cidr_block": "0.0.0.0/0"},
    })
    assert r.status_code == 404


# ── Dashboard request log ─────────────────────────────────────────────

def test_dashboard_requests_logged(client):
    create_vpc(client, "logged")
    r = client.get("/api/dashboard/requests")
    assert r.status_code == 200
    data = r.json()
    assert data["total_logged"] > 0
    paths = [req["path"] for req in data["requests"]]
    assert any("/v1/vpcs" in p for p in paths)
