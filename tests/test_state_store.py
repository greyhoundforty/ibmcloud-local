"""Unit tests for the StateStore."""

import json

import pytest

from src.state.store import StateStore


@pytest.fixture()
def store():
    s = StateStore()
    return s


def test_put_and_get(store):
    data = {"name": "test-vpc", "status": "available"}
    stored = store.put("vpcs", "vpc-123", data)
    assert stored["id"] == "vpc-123"
    assert stored["name"] == "test-vpc"
    result = store.get("vpcs", "vpc-123")
    assert result == stored


def test_get_missing_returns_none(store):
    assert store.get("vpcs", "does-not-exist") is None


def test_get_missing_namespace_returns_none(store):
    assert store.get("nonexistent_ns", "any-id") is None


def test_put_injects_id_and_created_at(store):
    stored = store.put("vpcs", "vpc-abc", {"name": "x"})
    assert stored["id"] == "vpc-abc"
    assert "created_at" in stored


def test_put_does_not_overwrite_existing_id(store):
    """put() should not clobber an id already in the data dict."""
    stored = store.put("vpcs", "vpc-key", {"id": "custom-id"})
    assert stored["id"] == "custom-id"


def test_list_empty_namespace(store):
    assert store.list("vpcs") == []


def test_list_returns_all(store):
    store.put("vpcs", "a", {"name": "a"})
    store.put("vpcs", "b", {"name": "b"})
    assert len(store.list("vpcs")) == 2


def test_list_with_filter(store):
    store.put("subnets", "s1", {"vpc_id": "v1", "name": "s1"})
    store.put("subnets", "s2", {"vpc_id": "v2", "name": "s2"})
    results = store.list("subnets", filters={"vpc_id": "v1"})
    assert len(results) == 1
    assert results[0]["name"] == "s1"


def test_delete_existing(store):
    store.put("vpcs", "vpc-del", {"name": "x"})
    assert store.delete("vpcs", "vpc-del") is True
    assert store.get("vpcs", "vpc-del") is None


def test_delete_missing_returns_false(store):
    assert store.delete("vpcs", "ghost") is False


def test_update_merges_fields(store):
    store.put("vpcs", "vpc-u", {"name": "old", "status": "pending"})
    updated = store.update("vpcs", "vpc-u", {"name": "new"})
    assert updated["name"] == "new"
    assert updated["status"] == "pending"


def test_update_missing_returns_none(store):
    assert store.update("vpcs", "ghost", {"name": "x"}) is None


def test_update_sets_updated_at(store):
    store.put("vpcs", "vpc-ts", {"name": "x"})
    store.update("vpcs", "vpc-ts", {"name": "y"})
    resource = store.get("vpcs", "vpc-ts")
    # updated_at is ISO string — just verify it's present and changed
    assert "updated_at" in resource


def test_count(store):
    store.put("vpcs", "a", {})
    store.put("vpcs", "b", {})
    assert store.count("vpcs") == 2
    assert store.count("subnets") == 0


def test_namespaces_summary(store):
    store.put("vpcs", "v1", {})
    store.put("subnets", "s1", {})
    store.put("subnets", "s2", {})
    ns = store.namespaces()
    assert ns["vpcs"] == 1
    assert ns["subnets"] == 2


def test_reset_all(store):
    store.put("vpcs", "v1", {})
    store.put("subnets", "s1", {})
    store.reset()
    assert store.list("vpcs") == []
    assert store.list("subnets") == []


def test_reset_namespace(store):
    store.put("vpcs", "v1", {})
    store.put("subnets", "s1", {})
    store.reset("vpcs")
    assert store.list("vpcs") == []
    assert len(store.list("subnets")) == 1


def test_generate_id_includes_prefix(store):
    id_ = store.generate_id("r006-")
    assert id_.startswith("r006-")


def test_generate_id_unique(store):
    ids = {store.generate_id() for _ in range(100)}
    assert len(ids) == 100


def test_request_log_append_and_retrieve(store):
    entry = {"method": "GET", "path": "/v1/vpcs", "status_code": 200}
    store.log_request(entry)
    log = store.get_request_log()
    assert len(log) == 1
    assert log[0]["path"] == "/v1/vpcs"


def test_request_log_newest_first(store):
    store.log_request({"seq": 1})
    store.log_request({"seq": 2})
    log = store.get_request_log()
    assert log[0]["seq"] == 2
    assert log[1]["seq"] == 1


def test_request_log_cap(store):
    store._request_log_max = 5
    for i in range(10):
        store.log_request({"seq": i})
    assert len(store._request_log) == 5
    # Oldest entries should have been trimmed
    seqs = [e["seq"] for e in store._request_log]
    assert 0 not in seqs


def test_snapshot_and_restore(store, tmp_path):
    store.put("vpcs", "vpc-snap", {"name": "snapshot-test"})
    snap_file = tmp_path / "state.json"
    store.snapshot_to_disk(snap_file)

    assert snap_file.exists()
    payload = json.loads(snap_file.read_text())
    assert payload["version"] == 1
    assert "vpcs" in payload["data"]

    new_store = StateStore()
    new_store.restore_from_disk(snap_file)
    assert new_store.get("vpcs", "vpc-snap")["name"] == "snapshot-test"


def test_restore_missing_file_is_noop(store, tmp_path):
    """restore_from_disk on a non-existent path should not raise."""
    store.restore_from_disk(tmp_path / "nope.json")
    assert store.namespaces() == {}
