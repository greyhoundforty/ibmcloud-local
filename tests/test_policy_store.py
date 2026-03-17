"""
Unit tests for PolicyStore — load, index, and query IAM policies.

No FastAPI, no HTTP — pure unit tests against src/iam/policy_store.py.
All tests written RED-first.
"""

import json
import pytest

from pathlib import Path


# ── Fixtures ───────────────────────────────────────────────────────────

def _write_policy_file(tmp_path: Path, policies: list) -> Path:
    p = tmp_path / "iam-policies.json"
    p.write_text(json.dumps({"policies": policies}))
    return p


def _policy(iam_id: str, role: str, service: str = "is") -> dict:
    """Build a minimal IBM Cloud IAM policy dict."""
    return {
        "subjects": [{"attributes": [{"name": "iam_id", "value": iam_id}]}],
        "roles": [{"role_id": f"crn:v1:bluemix:public:iam::::role:{role}"}],
        "resources": [{"attributes": [{"name": "serviceName", "value": service}]}],
    }


@pytest.fixture()
def viewer_policy_file(tmp_path):
    return _write_policy_file(tmp_path, [_policy("IBMid-viewer", "Viewer")])


@pytest.fixture()
def editor_policy_file(tmp_path):
    return _write_policy_file(tmp_path, [_policy("IBMid-editor", "Editor")])


@pytest.fixture()
def admin_policy_file(tmp_path):
    return _write_policy_file(tmp_path, [_policy("IBMid-admin", "Administrator")])


@pytest.fixture()
def multi_policy_file(tmp_path):
    return _write_policy_file(tmp_path, [
        _policy("IBMid-viewer", "Viewer"),
        _policy("IBMid-editor", "Editor"),
        _policy("IBMid-admin", "Administrator"),
    ])


# ── Load tests ─────────────────────────────────────────────────────────

def test_load_from_valid_file(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps is not None


def test_load_raises_for_missing_file(tmp_path):
    from src.iam.policy_store import PolicyStore
    with pytest.raises(FileNotFoundError):
        PolicyStore.load_from_file(tmp_path / "nonexistent.json")


def test_load_raises_for_malformed_json(tmp_path):
    from src.iam.policy_store import PolicyStore
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    with pytest.raises(ValueError):
        PolicyStore.load_from_file(bad)


def test_load_raises_when_policies_key_missing(tmp_path):
    from src.iam.policy_store import PolicyStore
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"something_else": []}))
    with pytest.raises(ValueError):
        PolicyStore.load_from_file(bad)


# ── get_policies_for_identity ──────────────────────────────────────────

def test_get_policies_for_known_identity(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    policies = ps.get_policies_for_identity("IBMid-viewer")
    assert len(policies) == 1


def test_get_policies_for_unknown_identity_returns_empty(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.get_policies_for_identity("IBMid-nobody") == []


def test_get_policies_returns_all_for_identity(multi_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(multi_policy_file)
    assert len(ps.get_policies_for_identity("IBMid-viewer")) == 1
    assert len(ps.get_policies_for_identity("IBMid-editor")) == 1


# ── allows — Viewer role ───────────────────────────────────────────────

def test_viewer_can_list_vpcs(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.allows("IBMid-viewer", "is.vpc.vpc.list") is True


def test_viewer_can_read_vpc(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.allows("IBMid-viewer", "is.vpc.vpc.read") is True


def test_viewer_cannot_create_vpc(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.allows("IBMid-viewer", "is.vpc.vpc.create") is False


def test_viewer_cannot_delete_vpc(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.allows("IBMid-viewer", "is.vpc.vpc.delete") is False


# ── allows — Editor role ───────────────────────────────────────────────

def test_editor_can_create_vpc(editor_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(editor_policy_file)
    assert ps.allows("IBMid-editor", "is.vpc.vpc.create") is True


def test_editor_can_delete_vpc(editor_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(editor_policy_file)
    assert ps.allows("IBMid-editor", "is.vpc.vpc.delete") is True


def test_editor_can_list_instances(editor_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(editor_policy_file)
    assert ps.allows("IBMid-editor", "is.vpc.instance.list") is True


# ── allows — Administrator role ────────────────────────────────────────

def test_administrator_can_do_anything_in_service(admin_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(admin_policy_file)
    for action in ["is.vpc.vpc.list", "is.vpc.vpc.create", "is.vpc.vpc.delete",
                   "is.vpc.instance.create", "is.vpc.subnet.update"]:
        assert ps.allows("IBMid-admin", action) is True, f"Admin blocked on {action}"


# ── allows — no matching policy ────────────────────────────────────────

def test_unknown_identity_denied(viewer_policy_file):
    from src.iam.policy_store import PolicyStore
    ps = PolicyStore.load_from_file(viewer_policy_file)
    assert ps.allows("IBMid-nobody", "is.vpc.vpc.list") is False


def test_wrong_service_denied(tmp_path):
    """A policy for service 'cos' must not grant access to 'is' actions."""
    from src.iam.policy_store import PolicyStore
    p = _write_policy_file(tmp_path, [_policy("IBMid-cos-editor", "Editor", service="cos")])
    ps = PolicyStore.load_from_file(p)
    assert ps.allows("IBMid-cos-editor", "is.vpc.vpc.create") is False
