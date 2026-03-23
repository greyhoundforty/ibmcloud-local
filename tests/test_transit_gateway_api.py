"""
Integration tests for the Transit Gateway provider.

Written RED-first — all must fail before TransitGatewayProvider exists.

API surface covered:
  GET    /v1/transit_gateways                              list
  POST   /v1/transit_gateways                              create
  GET    /v1/transit_gateways/{id}                         get
  PATCH  /v1/transit_gateways/{id}                         update
  DELETE /v1/transit_gateways/{id}                         delete
  GET    /v1/transit_gateways/{id}/connections             list connections
  POST   /v1/transit_gateways/{id}/connections             create connection
  GET    /v1/transit_gateways/{id}/connections/{conn_id}   get connection
  DELETE /v1/transit_gateways/{id}/connections/{conn_id}   delete connection
  GET    /v1/connections                                    list all connections (global)

Note: all requests include ?version=2024-01-01 as required by the real API.
"""

import pytest
from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group

VERSION = "version=2024-01-01"


@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


@pytest.fixture(scope="module")
def client(auth_client):
    return auth_client


# ── helpers ──────────────────────────────────────────────────────────

def create_gateway(client, name="tgw-test", location="us-south", global_routing=False):
    return client.post(
        f"/v1/transit_gateways?{VERSION}",
        json={"name": name, "location": location, "global": global_routing},
    )


def create_vpc_connection(client, tgw_id, vpc_crn="crn:v1:bluemix:public:is:us-south:a/abc::vpc:r006-123", name="conn-vpc-1"):
    return client.post(
        f"/v1/transit_gateways/{tgw_id}/connections?{VERSION}",
        json={"network_type": "vpc", "network_id": vpc_crn, "name": name},
    )


def create_powervs_connection(client, tgw_id, ws_crn="crn:v1:bluemix:public:power-iaas:us-south:a/abc::workspace:ws-123", name="conn-pvs-1"):
    return client.post(
        f"/v1/transit_gateways/{tgw_id}/connections?{VERSION}",
        json={"network_type": "power_virtual_server", "network_id": ws_crn, "name": name},
    )


# ── POST /v1/transit_gateways ─────────────────────────────────────────

def test_create_gateway_returns_201(client):
    r = create_gateway(client)
    assert r.status_code == 201


def test_create_gateway_response_shape(client):
    data = create_gateway(client).json()
    assert data["name"] == "tgw-test"
    assert data["location"] == "us-south"
    assert "id" in data
    assert "crn" in data
    assert "created_at" in data
    assert data["status"] in ("available", "pending")
    assert data["global"] is False


def test_create_gateway_global_routing(client):
    data = create_gateway(client, global_routing=True).json()
    assert data["global"] is True


def test_create_gateway_missing_name_returns_400(client):
    r = client.post(f"/v1/transit_gateways?{VERSION}", json={"location": "us-south"})
    assert r.status_code == 400
    assert "errors" in r.json()


def test_create_gateway_missing_location_returns_400(client):
    r = client.post(f"/v1/transit_gateways?{VERSION}", json={"name": "tgw-x"})
    assert r.status_code == 400
    assert "errors" in r.json()


def test_create_gateway_duplicate_name_returns_409(client):
    create_gateway(client, name="tgw-dup")
    r = create_gateway(client, name="tgw-dup")
    assert r.status_code == 409
    assert "errors" in r.json()


# ── GET /v1/transit_gateways ─────────────────────────────────────────

def test_list_gateways_empty(client):
    r = client.get(f"/v1/transit_gateways?{VERSION}")
    assert r.status_code == 200
    data = r.json()
    assert "transit_gateways" in data
    assert data["transit_gateways"] == []


def test_list_gateways_returns_created(client):
    create_gateway(client, name="tgw-a")
    create_gateway(client, name="tgw-b")
    data = client.get(f"/v1/transit_gateways?{VERSION}").json()
    assert len(data["transit_gateways"]) == 2
    names = {g["name"] for g in data["transit_gateways"]}
    assert names == {"tgw-a", "tgw-b"}


# ── GET /v1/transit_gateways/{id} ─────────────────────────────────────

def test_get_gateway_returns_200(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.get(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    assert r.status_code == 200
    assert r.json()["id"] == tgw_id


def test_get_gateway_not_found_returns_404(client):
    r = client.get(f"/v1/transit_gateways/does-not-exist?{VERSION}")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── PATCH /v1/transit_gateways/{id} ──────────────────────────────────

def test_update_gateway_name(client):
    tgw_id = create_gateway(client, name="tgw-old").json()["id"]
    r = client.patch(
        f"/v1/transit_gateways/{tgw_id}?{VERSION}",
        json={"name": "tgw-new"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "tgw-new"


def test_update_gateway_not_found_returns_404(client):
    r = client.patch(f"/v1/transit_gateways/nope?{VERSION}", json={"name": "x"})
    assert r.status_code == 404


# ── DELETE /v1/transit_gateways/{id} ─────────────────────────────────

def test_delete_gateway_returns_204(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.delete(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    assert r.status_code == 204


def test_delete_gateway_removes_it(client):
    tgw_id = create_gateway(client).json()["id"]
    client.delete(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    r = client.get(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    assert r.status_code == 404


def test_delete_gateway_not_found_returns_404(client):
    r = client.delete(f"/v1/transit_gateways/nope?{VERSION}")
    assert r.status_code == 404


def test_delete_gateway_with_connections_returns_409(client):
    tgw_id = create_gateway(client).json()["id"]
    create_vpc_connection(client, tgw_id)
    r = client.delete(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    assert r.status_code == 409
    assert "errors" in r.json()


# ── POST /v1/transit_gateways/{id}/connections ────────────────────────

def test_create_vpc_connection_returns_201(client):
    tgw_id = create_gateway(client).json()["id"]
    r = create_vpc_connection(client, tgw_id)
    assert r.status_code == 201


def test_create_vpc_connection_response_shape(client):
    tgw_id = create_gateway(client).json()["id"]
    data = create_vpc_connection(client, tgw_id).json()
    assert "id" in data
    assert data["network_type"] == "vpc"
    assert "network_id" in data
    assert data["name"] == "conn-vpc-1"
    assert "status" in data
    assert "created_at" in data


def test_create_powervs_connection_returns_201(client):
    tgw_id = create_gateway(client).json()["id"]
    r = create_powervs_connection(client, tgw_id)
    assert r.status_code == 201


def test_create_powervs_connection_network_type(client):
    tgw_id = create_gateway(client).json()["id"]
    data = create_powervs_connection(client, tgw_id).json()
    assert data["network_type"] == "power_virtual_server"


def test_create_connection_missing_network_type_returns_400(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.post(
        f"/v1/transit_gateways/{tgw_id}/connections?{VERSION}",
        json={"network_id": "crn:v1:..."},
    )
    assert r.status_code == 400
    assert "errors" in r.json()


def test_create_connection_gateway_not_found_returns_404(client):
    r = create_vpc_connection(client, "nonexistent-gw")
    assert r.status_code == 404
    assert "errors" in r.json()


def test_create_connection_duplicate_network_id_returns_409(client):
    tgw_id = create_gateway(client).json()["id"]
    vpc_crn = "crn:v1:bluemix:public:is:us-south:a/abc::vpc:r006-dup"
    create_vpc_connection(client, tgw_id, vpc_crn=vpc_crn, name="conn-1")
    r = create_vpc_connection(client, tgw_id, vpc_crn=vpc_crn, name="conn-2")
    assert r.status_code == 409
    assert "errors" in r.json()


# ── GET /v1/transit_gateways/{id}/connections ─────────────────────────

def test_list_connections_empty(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.get(f"/v1/transit_gateways/{tgw_id}/connections?{VERSION}")
    assert r.status_code == 200
    data = r.json()
    assert "connections" in data
    assert data["connections"] == []


def test_list_connections_returns_created(client):
    tgw_id = create_gateway(client).json()["id"]
    create_vpc_connection(client, tgw_id, name="c1")
    create_powervs_connection(client, tgw_id, name="c2")
    data = client.get(f"/v1/transit_gateways/{tgw_id}/connections?{VERSION}").json()
    assert len(data["connections"]) == 2


def test_list_connections_gateway_not_found_returns_404(client):
    r = client.get(f"/v1/transit_gateways/nope/connections?{VERSION}")
    assert r.status_code == 404


# ── GET /v1/transit_gateways/{id}/connections/{conn_id} ──────────────

def test_get_connection_returns_200(client):
    tgw_id = create_gateway(client).json()["id"]
    conn_id = create_vpc_connection(client, tgw_id).json()["id"]
    r = client.get(f"/v1/transit_gateways/{tgw_id}/connections/{conn_id}?{VERSION}")
    assert r.status_code == 200
    assert r.json()["id"] == conn_id


def test_get_connection_not_found_returns_404(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.get(f"/v1/transit_gateways/{tgw_id}/connections/nope?{VERSION}")
    assert r.status_code == 404
    assert "errors" in r.json()


# ── DELETE /v1/transit_gateways/{id}/connections/{conn_id} ───────────

def test_delete_connection_returns_204(client):
    tgw_id = create_gateway(client).json()["id"]
    conn_id = create_vpc_connection(client, tgw_id).json()["id"]
    r = client.delete(f"/v1/transit_gateways/{tgw_id}/connections/{conn_id}?{VERSION}")
    assert r.status_code == 204


def test_delete_connection_removes_it(client):
    tgw_id = create_gateway(client).json()["id"]
    conn_id = create_vpc_connection(client, tgw_id).json()["id"]
    client.delete(f"/v1/transit_gateways/{tgw_id}/connections/{conn_id}?{VERSION}")
    r = client.get(f"/v1/transit_gateways/{tgw_id}/connections/{conn_id}?{VERSION}")
    assert r.status_code == 404


def test_delete_connection_not_found_returns_404(client):
    tgw_id = create_gateway(client).json()["id"]
    r = client.delete(f"/v1/transit_gateways/{tgw_id}/connections/nope?{VERSION}")
    assert r.status_code == 404


def test_after_delete_connection_gateway_can_be_deleted(client):
    tgw_id = create_gateway(client).json()["id"]
    conn_id = create_vpc_connection(client, tgw_id).json()["id"]
    client.delete(f"/v1/transit_gateways/{tgw_id}/connections/{conn_id}?{VERSION}")
    r = client.delete(f"/v1/transit_gateways/{tgw_id}?{VERSION}")
    assert r.status_code == 204


# ── GET /v1/connections (global) ──────────────────────────────────────

def test_global_connections_empty(client):
    r = client.get(f"/v1/connections?{VERSION}")
    assert r.status_code == 200
    data = r.json()
    assert "connections" in data
    assert data["connections"] == []


def test_global_connections_includes_all_gateways(client):
    tgw1 = create_gateway(client, name="tgw-1").json()["id"]
    tgw2 = create_gateway(client, name="tgw-2").json()["id"]
    create_vpc_connection(client, tgw1, name="c1")
    create_powervs_connection(client, tgw2, name="c2")
    data = client.get(f"/v1/connections?{VERSION}").json()
    assert len(data["connections"]) == 2


def test_global_connections_include_transit_gateway_reference(client):
    tgw_id = create_gateway(client, name="tgw-ref").json()["id"]
    create_vpc_connection(client, tgw_id)
    conns = client.get(f"/v1/connections?{VERSION}").json()["connections"]
    assert len(conns) == 1
    assert "transit_gateway" in conns[0]
    assert conns[0]["transit_gateway"]["id"] == tgw_id
