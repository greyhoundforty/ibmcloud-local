"""
Tests for VPC action map and end-to-end policy enforcement.

Written RED-first. Tests use monkeypatch to activate enforcement
(IBMCLOUD_LOCAL_AUTHZ=enforce) and inject a policy file.
"""

import json
import pytest

from fastapi.testclient import TestClient
from src.server import app
from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


def _write_policies(tmp_path, policies: list) -> str:
    p = tmp_path / "test-policies.json"
    p.write_text(json.dumps({"policies": policies}))
    return str(p)


def _policy(iam_id: str, role: str, service: str = "is") -> dict:
    return {
        "subjects": [{"attributes": [{"name": "iam_id", "value": iam_id}]}],
        "roles": [{"role_id": f"crn:v1:bluemix:public:iam::::role:{role}"}],
        "resources": [{"attributes": [{"name": "serviceName", "value": service}]}],
    }


def _get_token(client, apikey: str = "local-dev") -> str:
    r = client.post(
        "/identity/token",
        content=(
            f"grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey&apikey={apikey}"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return r.json()["access_token"]


# ── VPC action map tests ───────────────────────────────────────────────

def test_action_map_get_vpcs():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("GET", "/v1/vpcs") == "is.vpc.vpc.list"


def test_action_map_post_vpcs():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("POST", "/v1/vpcs") == "is.vpc.vpc.create"


def test_action_map_get_vpc_by_id():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("GET", "/v1/vpcs/r006-abc123") == "is.vpc.vpc.read"


def test_action_map_patch_vpc():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("PATCH", "/v1/vpcs/r006-abc123") == "is.vpc.vpc.update"


def test_action_map_delete_vpc():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("DELETE", "/v1/vpcs/r006-abc123") == "is.vpc.vpc.delete"


def test_action_map_get_instances():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("GET", "/v1/instances") == "is.vpc.instance.list"


def test_action_map_post_instances():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("POST", "/v1/instances") == "is.vpc.instance.create"


def test_action_map_unmapped_path_returns_none():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("GET", "/some/unknown/path") is None


def test_action_map_emulator_path_returns_none():
    from src.iam.vpc_action_map import resolve_action
    assert resolve_action("GET", "/_emulator/health") is None


# ── Policy enforcement integration tests ──────────────────────────────

def test_viewer_can_list_vpcs_when_authz_enforced(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [_policy("iam-ServiceId-local", "Viewer")])
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.get("/v1/vpcs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_viewer_blocked_from_creating_vpc(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [_policy("iam-ServiceId-local", "Viewer")])
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.post(
            "/v1/vpcs",
            json={"name": "blocked-vpc"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 403
    assert "errors" in r.json()


def test_editor_can_create_vpc_when_authz_enforced(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [_policy("iam-ServiceId-local", "Editor")])
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.post(
            "/v1/vpcs",
            json={"name": "editor-vpc"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 201


def test_administrator_can_delete_vpc(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [_policy("iam-ServiceId-local", "Administrator")])
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        vpc_id = client.post("/v1/vpcs", json={"name": "admin-vpc"}, headers=headers).json()["id"]
        r = client.delete(f"/v1/vpcs/{vpc_id}", headers=headers)
    assert r.status_code == 204


def test_no_policy_denies_write(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [])  # empty — no policies
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.post(
            "/v1/vpcs",
            json={"name": "denied-vpc"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 403


def test_403_uses_ibm_error_envelope(tmp_path, monkeypatch):
    policy_file = _write_policies(tmp_path, [_policy("iam-ServiceId-local", "Viewer")])
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.post(
            "/v1/vpcs",
            json={"name": "x"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 403
    body = r.json()
    assert "errors" in body
    err = body["errors"][0]
    assert "code" in err
    assert "message" in err


def test_emulator_paths_bypass_policy_enforcement(tmp_path, monkeypatch):
    """/_emulator/* must be accessible regardless of role."""
    policy_file = _write_policies(tmp_path, [])  # no policies at all
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTHZ", "enforce")
    monkeypatch.setenv("IBMCLOUD_LOCAL_POLICY_FILE", policy_file)

    with TestClient(app) as client:
        assert client.get("/_emulator/health").status_code == 200


def test_authz_off_by_default_allows_all(monkeypatch):
    """Without IBMCLOUD_LOCAL_AUTHZ=enforce, all requests pass through."""
    monkeypatch.delenv("IBMCLOUD_LOCAL_AUTHZ", raising=False)

    with TestClient(app) as client:
        token = _get_token(client)
        r = client.post(
            "/v1/vpcs",
            json={"name": "no-authz-vpc"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 201
