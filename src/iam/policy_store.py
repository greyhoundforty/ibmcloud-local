"""
PolicyStore — loads and queries IBM Cloud IAM policies for local enforcement.

Policies are pulled from real IBM Cloud via sync_iam and stored in a local
JSON file. This module reads that file and answers allow/deny questions.

IBM Cloud IAM policy JSON shape:
{
  "policies": [
    {
      "subjects": [{"attributes": [{"name": "iam_id", "value": "IBMid-..."}]}],
      "roles":    [{"role_id": "crn:v1:bluemix:public:iam::::role:Viewer"}],
      "resources":[{"attributes": [{"name": "serviceName", "value": "is"}]}]
    }
  ]
}

Role → allowed action suffixes:
    Viewer        — .list, .read
    Operator      — Viewer + .operate
    Editor        — Operator + .create, .update, .delete
    Administrator — all actions for the matching service
"""

from __future__ import annotations

import json
from pathlib import Path


# Map the short role name (extracted from role_id CRN) to allowed action suffixes.
# Administrator is handled separately (wildcard for the service).
_ROLE_SUFFIXES: dict[str, set[str]] = {
    "Viewer":        {".list", ".read"},
    "Operator":      {".list", ".read", ".operate"},
    "Editor":        {".list", ".read", ".operate", ".create", ".update", ".delete"},
    "Administrator": set(),  # sentinel — grants all actions for the service
}

_ADMIN_ROLE = "Administrator"


def _extract_role_name(role_id: str) -> str:
    """'crn:v1:bluemix:public:iam::::role:Editor' → 'Editor'"""
    return role_id.rsplit(":", 1)[-1]


def _extract_iam_id(subject: dict) -> str | None:
    for attr in subject.get("attributes", []):
        if attr.get("name") == "iam_id":
            return attr.get("value")
    return None


def _extract_service_name(resource: dict) -> str | None:
    for attr in resource.get("attributes", []):
        if attr.get("name") == "serviceName":
            return attr.get("value")
    return None


class PolicyStore:
    """Queryable store of IBM Cloud IAM policies loaded from a local file."""

    def __init__(self, policies: list[dict]) -> None:
        # Index policies by iam_id for O(1) lookup
        self._by_identity: dict[str, list[dict]] = {}
        for policy in policies:
            for subject in policy.get("subjects", []):
                iam_id = _extract_iam_id(subject)
                if iam_id:
                    self._by_identity.setdefault(iam_id, []).append(policy)

    # ── Class methods ─────────────────────────────────────────────────

    @classmethod
    def load_from_file(cls, path: Path | str) -> "PolicyStore":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Policy file not found: {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Policy file is not valid JSON: {exc}") from exc
        if "policies" not in data:
            raise ValueError(f"Policy file missing 'policies' key: {path}")
        return cls(data["policies"])

    # ── Query methods ─────────────────────────────────────────────────

    def get_policies_for_identity(self, iam_id: str) -> list[dict]:
        return self._by_identity.get(iam_id, [])

    def allows(self, iam_id: str, action: str) -> bool:
        """
        Return True if the identity has a policy granting the requested action.

        action format: "<service>.<resource-type>.<verb>"
        e.g. "is.vpc.vpc.create", "is.vpc.instance.list"

        The service prefix is the first segment (e.g. "is").
        """
        action_service = action.split(".")[0] if "." in action else action
        action_suffix = "." + action.rsplit(".", 1)[-1] if "." in action else ""

        for policy in self.get_policies_for_identity(iam_id):
            # Check resource service matches
            policy_services = {
                _extract_service_name(r)
                for r in policy.get("resources", [])
            }
            if action_service not in policy_services:
                continue

            # Check each role grants the action
            for role_ref in policy.get("roles", []):
                role_name = _extract_role_name(role_ref.get("role_id", ""))
                if role_name == _ADMIN_ROLE:
                    return True
                allowed_suffixes = _ROLE_SUFFIXES.get(role_name, set())
                if action_suffix in allowed_suffixes:
                    return True

        return False
