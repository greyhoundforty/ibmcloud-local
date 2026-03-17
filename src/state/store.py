"""
State Store — the in-memory backbone of ibmcloud-local.

Every emulated IBM Cloud service stores its resources here. Think of this as
a lightweight database that lives entirely in RAM. Each service gets its own
isolated "namespace" so VPC resources don't collide with COS buckets, etc.

Architecture note:
    LocalStack uses a similar pattern — each provider maintains state in a
    module-level dict. We centralize it here so the dashboard and CLI can
    introspect all state from one place, and so persistence (snapshotting
    to disk) only needs to hook into one component.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from threading import Lock


class StateStore:
    """
    Thread-safe, namespaced key-value store for emulated resources.

    Each IBM Cloud service (VPC, COS, IKS, etc.) gets a namespace.
    Within a namespace, resources are keyed by their ID.

    Usage:
        store = StateStore()
        store.put("vpc", "vpc-abc123", {"name": "my-vpc", "status": "available"})
        vpc = store.get("vpc", "vpc-abc123")
        all_vpcs = store.list("vpc")
    """

    def __init__(self):
        # _data shape: { "namespace": { "resource_id": { ...resource_dict... } } }
        self._data: dict[str, dict[str, Any]] = {}
        # Lock for thread safety — FastAPI can serve requests concurrently
        self._lock = Lock()
        # Track creation timestamps for each resource (useful for sorting/filtering)
        self._metadata: dict[str, dict[str, dict]] = {}
        # Global request log — the dashboard reads from this
        self._request_log: list[dict] = []
        # Cap the request log so it doesn't eat memory forever
        self._request_log_max = 1000

    def generate_id(self, prefix: str = "") -> str:
        """
        Generate a unique ID that looks like IBM Cloud resource IDs.

        IBM Cloud uses UUIDs for most resources, sometimes with a prefix
        like 'r006-' for VPC resources in us-south. We mimic that pattern.

        Args:
            prefix: Optional prefix like "r006-" (VPC region prefix)

        Returns:
            Something like "r006-a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        """
        return f"{prefix}{uuid.uuid4()}"

    def put(self, namespace: str, resource_id: str, data: dict) -> dict:
        """
        Store a resource. Overwrites if the ID already exists.

        Args:
            namespace: Service namespace like "vpcs", "subnets", "instances"
            resource_id: Unique ID for this resource
            data: The full resource dict (should match IBM Cloud API schema)

        Returns:
            The stored resource dict (with metadata injected)
        """
        with self._lock:
            # Auto-create the namespace bucket if it doesn't exist
            if namespace not in self._data:
                self._data[namespace] = {}
                self._metadata[namespace] = {}

            now = time.time()
            # Inject standard IBM Cloud metadata fields if not present
            data.setdefault("id", resource_id)
            data.setdefault("created_at", _iso_timestamp(now))

            self._data[namespace][resource_id] = data
            self._metadata[namespace][resource_id] = {
                "created_at": now,
                "updated_at": now,
            }
            return data

    def get(self, namespace: str, resource_id: str) -> dict | None:
        """
        Fetch a single resource by namespace + ID.

        Returns None if not found (callers should return 404).
        """
        with self._lock:
            return self._data.get(namespace, {}).get(resource_id)

    def list(self, namespace: str, filters: dict | None = None) -> list[dict]:
        """
        List all resources in a namespace, optionally filtered.

        Args:
            namespace: Service namespace
            filters: Optional dict of {field: value} to match against.
                     Supports simple equality matching on top-level fields.

        Returns:
            List of matching resource dicts
        """
        with self._lock:
            resources = list(self._data.get(namespace, {}).values())

        # Apply simple filters if provided
        if filters:
            for key, value in filters.items():
                resources = [r for r in resources if r.get(key) == value]

        return resources

    def delete(self, namespace: str, resource_id: str) -> bool:
        """
        Remove a resource. Returns True if it existed, False if not.
        """
        with self._lock:
            bucket = self._data.get(namespace, {})
            if resource_id in bucket:
                del bucket[resource_id]
                self._metadata.get(namespace, {}).pop(resource_id, None)
                return True
            return False

    def update(self, namespace: str, resource_id: str, patch: dict) -> dict | None:
        """
        Partial update (PATCH semantics). Merges patch into existing resource.

        Returns the updated resource, or None if not found.
        """
        with self._lock:
            bucket = self._data.get(namespace, {})
            if resource_id not in bucket:
                return None

            # Shallow merge — top-level fields in patch overwrite existing
            bucket[resource_id].update(patch)
            bucket[resource_id]["updated_at"] = _iso_timestamp(time.time())

            # Update metadata timestamp
            if namespace in self._metadata and resource_id in self._metadata[namespace]:
                self._metadata[namespace][resource_id]["updated_at"] = time.time()

            return bucket[resource_id]

    def count(self, namespace: str) -> int:
        """Return how many resources exist in a namespace."""
        with self._lock:
            return len(self._data.get(namespace, {}))

    def namespaces(self) -> dict[str, int]:
        """
        Return a summary of all namespaces and their resource counts.
        Used by the dashboard to show an overview of emulator state.
        """
        with self._lock:
            return {ns: len(items) for ns, items in self._data.items()}

    def reset(self, namespace: str | None = None):
        """
        Wipe state. If namespace is given, only wipe that namespace.
        Otherwise nuke everything — useful for test teardown.
        """
        with self._lock:
            if namespace:
                self._data.pop(namespace, None)
                self._metadata.pop(namespace, None)
            else:
                self._data.clear()
                self._metadata.clear()
                self._request_log.clear()

    # ── Request Logging (for the dashboard) ──────────────────────────

    def log_request(self, entry: dict):
        """
        Append a request/response log entry. The routing middleware calls
        this on every request so the dashboard can show a live activity feed.

        Entry shape:
            {
                "timestamp": "2026-03-13T...",
                "method": "GET",
                "path": "/v1/vpcs",
                "service": "vpc",
                "status_code": 200,
                "duration_ms": 12.4,
            }
        """
        with self._lock:
            self._request_log.append(entry)
            # Trim if over the cap (drop oldest entries)
            if len(self._request_log) > self._request_log_max:
                self._request_log = self._request_log[-self._request_log_max :]

    def get_request_log(self, limit: int = 100) -> list[dict]:
        """Return the most recent N request log entries (newest first)."""
        with self._lock:
            return list(reversed(self._request_log[-limit:]))

    # ── Persistence (optional, for disk snapshots) ───────────────────

    def snapshot_to_disk(self, path: str | Path):
        """
        Dump all state to a JSON file. Called by `ibmcloud-local stop --save`
        or periodically if persistence mode is "disk".
        """
        path = Path(path)
        with self._lock:
            payload = {
                "version": 1,
                "timestamp": _iso_timestamp(time.time()),
                "data": self._data,
            }
        path.write_text(json.dumps(payload, indent=2, default=str))

    def restore_from_disk(self, path: str | Path):
        """
        Load state from a previously saved snapshot.
        Called on startup if persistence mode is "disk".
        """
        path = Path(path)
        if not path.exists():
            return
        payload = json.loads(path.read_text())
        with self._lock:
            self._data = payload.get("data", {})


def _iso_timestamp(ts: float) -> str:
    """Convert a Unix timestamp to ISO 8601 format (UTC), matching IBM Cloud API style."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ── Module-level singleton ───────────────────────────────────────────
# All providers import this same instance. This is the "database" of
# the entire emulator. Similar to how LocalStack uses module-level state.
store = StateStore()
