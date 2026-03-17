# ibmcloud-local — Development Status

## Session summary (2026-03-16)

### What was built

This session completed two major feature areas using strict TDD (Red/Green/Refactor):

---

#### Networking endpoints (Red/Green complete)

Four new providers were added, bringing the emulator from VPC-only to a realistic multi-service topology.

| Provider | File | Endpoints | Tests |
|---|---|---|---|
| Security Group rules | `src/providers/vpc.py` | 5 (CRUD per-rule) | `test_vpc_api.py` |
| Network ACLs | `src/providers/network_acl.py` | 10 (CRUD + per-rule) | `test_network_acl_api.py` |
| Public Gateways | `src/providers/public_gateway.py` | 8 (CRUD + subnet attach/detach) | `test_public_gateway_api.py` |
| Load Balancers | `src/providers/load_balancer.py` | 20 (LB + listeners + pools + members) | `test_load_balancer_api.py` |

New model files: `src/models/network_acl.py`, `src/models/public_gateway.py`, `src/models/load_balancer.py`.

Notable behaviours:
- Subnet creation auto-creates a default Network ACL (allow-all inbound + outbound) when none is specified, matching real IBM Cloud behaviour.
- Public Gateway creation auto-reserves a floating IP; only one gateway allowed per zone per VPC (409 on duplicate).
- Load Balancer creation transitions `provisioning_status` from `create_pending` to `active` asynchronously (~2s), matching the real IBM Cloud state machine.
- LB delete rejects with 409 if listeners or pools still exist on the balancer.
- Network ACL delete rejects with 409 if the ACL is attached to a subnet.

---

#### IAM shim (Red/Green complete)

A full local IAM implementation was built so that SDK and CLI callers can authenticate against the emulator using `IAMAuthenticator` rather than `NoAuthAuthenticator`.

**New files:**

| File | Purpose |
|---|---|
| `src/providers/iam.py` | `POST /identity/token`, `GET /identity/keys` |
| `src/middleware/auth.py` | Bearer token enforcement middleware |
| `src/iam/__init__.py` | Package marker |
| `src/iam/policy_store.py` | Loads and queries IBM Cloud IAM policy JSON |
| `src/iam/vpc_action_map.py` | Maps `(HTTP method, URL path)` to IAM action strings |

**Two independent control planes:**

`IBMCLOUD_LOCAL_AUTH` — controls token _verification_:
- `permissive` (default): token must be structurally present (3-segment JWT) and not expired; signature is not checked. Any string works as an API key.
- `strict`: verifies the API key against real IBM Cloud (`iam.cloud.ibm.com`) before issuing a local token. The real `iam_id` is extracted from IBM Cloud's JWT and embedded in the local token.

`IBMCLOUD_LOCAL_AUTHZ` — controls policy _enforcement_:
- `off` (default): all authenticated requests are allowed. No policy file needed.
- `enforce`: loads IBM Cloud IAM policies from `IBMCLOUD_LOCAL_POLICY_FILE` and checks each request against the policy store. 403 with IBM error envelope on denial.

**Role-to-action mapping (enforced when `IBMCLOUD_LOCAL_AUTHZ=enforce`):**

| Role | Allowed action suffixes |
|---|---|
| Viewer | `.list`, `.read` |
| Operator | `.list`, `.read`, `.operate` |
| Editor | `.list`, `.read`, `.operate`, `.create`, `.update`, `.delete` |
| Administrator | all actions for the service |

**Bypass paths** — always allowed regardless of auth/authz mode:
- `/_emulator/*` — control plane (reset, health, state dump)
- `/api/dashboard/*` — dashboard API
- `/identity/*` — IAM token and JWKS endpoints

**Policy file format** (standard IBM Cloud IAM JSON shape):

```json
{
  "policies": [
    {
      "subjects": [{"attributes": [{"name": "iam_id", "value": "IBMid-..."}]}],
      "roles": [{"role_id": "crn:v1:bluemix:public:iam::::role:Viewer"}],
      "resources": [{"attributes": [{"name": "serviceName", "value": "is"}]}]
    }
  ]
}
```

Pull real policies from IBM Cloud: `ibmcloud iam access-policies --output json > policies.json`.

**New dependencies:** `PyJWT[crypto]>=2.8`, `cryptography>=41.0`.

---

#### Architecture change: route registration moved out of lifespan

Providers are now created and routes registered at module import time (`src/server.py` module level) rather than inside the lifespan handler. The lifespan retains only startup/shutdown side effects: state seeding, disk persistence, and startup logging.

This prevents a test-suite issue where repeated `TestClient(app)` instantiation would run the lifespan multiple times, accumulating duplicate routes on the shared FastAPI app object.

---

#### Test infrastructure

| File | Tests | Covers |
|---|---|---|
| `tests/conftest.py` | — | Module-scoped `auth_client` fixture used by all provider tests |
| `tests/test_iam_api.py` | 21 | Token endpoint, JWKS, auth middleware (permissive + strict) |
| `tests/test_policy_store.py` | 17 | PolicyStore unit tests (load, query, role enforcement) |
| `tests/test_authz_enforcement.py` | 17 | VPC action map + end-to-end policy enforcement via middleware |
| `tests/test_iam_strict_mode.py` | 14 | Strict-mode IBM Cloud verification with injected fake HTTP client |

Total: **255 tests, all passing** in ~1.8s.

---

### Known gaps / not yet implemented

#### sync_iam script

A `sync_iam` module that calls the IBM Cloud Policy Management API and writes a local `policies.json` is planned but not yet implemented. For now, policies must be exported manually:

```bash
ibmcloud iam access-policies --output json > policies.json
export IBMCLOUD_LOCAL_AUTHZ=enforce
export IBMCLOUD_LOCAL_POLICY_FILE=./policies.json
```

#### Pagination

List endpoints return all resources without pagination. The real IBM Cloud API uses cursor-based pagination (`start` + `limit` query params). SDKs that auto-paginate will work incorrectly against large result sets.

#### Instance user data / SSH keys

`InstanceCreate` does not model `user_data`, `keys`, or `boot_volume_attachment`. Pydantic will reject payloads that include these fields.

#### Floating IP attachment to network interfaces

`FloatingIpCreate` accepts a `target` field but does not validate the target exists or update the instance's network interface.

#### No pagination

List endpoints return all resources. The real IBM Cloud API uses cursor-based pagination with `start` and `limit` query params and a `next` link in the response.

---

### Roadmap: next features

1. `sync_iam` — CLI command and module to pull IBM Cloud IAM policies into a local file
2. Pagination — `start` + `limit` query params on all list endpoints, `next` link in responses
3. Instance user data / SSH keys — extend `InstanceCreate` model
4. Extended COS, PowerVS, IKS, Code Engine stubs

---

## Session summary (2026-03-15)

### What was fixed

#### Build system (`pyproject.toml`)

The `mise run install` task was broken with:

```
ModuleNotFoundError: No module named 'setuptools.backends'
```

**Root cause:** `build-backend` was set to `setuptools.backends._legacy:_Backend`, which only exists in setuptools >= 71 and was not available in the active environment.

**Fix:** Changed to the standard `setuptools.build_meta` backend, which works with any modern setuptools. Also updated `mise.toml` to use `uv pip install` instead of bare `pip install` to match the `uv`-managed venv.

---

### What was built

#### Test suite

Two test files were added covering the full emulator surface:

| File | Tests | Covers |
|---|---|---|
| `tests/test_state_store.py` | 24 | StateStore unit tests: CRUD, filtering, reset, ID generation, request log cap, snapshot/restore |
| `tests/test_vpc_api.py` | 55 | Full HTTP integration tests for all VPC endpoints + dashboard/control plane |
| `tests/test_resource_manager_api.py` | 11 | Resource Manager CRUD + default group protection |

All 91 tests pass (`pytest tests/ -v` in ~0.4s).

#### Resource Manager provider (`src/providers/resource_manager.py`)

New provider implementing `/v2/resource_groups`:

| Method | Path | Description |
|---|---|---|
| GET | `/v2/resource_groups` | List all resource groups |
| POST | `/v2/resource_groups` | Create a resource group |
| GET | `/v2/resource_groups/{id}` | Get a resource group |
| PATCH | `/v2/resource_groups/{id}` | Rename a resource group |
| DELETE | `/v2/resource_groups/{id}` | Delete (Default group is protected) |

A `Default` resource group (`id: default-resource-group`) is seeded at startup and cannot be deleted, matching real IBM Cloud behaviour.

#### Resource group resolution in VPC creation

Previously, passing `resource_group: {"id": "..."}` was accepted but the `name` in the response came back empty. Now `VpcProvider._resolve_resource_group()` looks up the stored resource group and returns the fully-populated reference. Falls back to `Default` when no group is specified; unknown IDs are passed through without error (permissive mode).

#### DNS modelling on VPC

The `dns` block from the real IBM Cloud VPC API is now fully modelled:

```json
"dns": {
  "enable_hub": true,
  "resolver": {
    "type": "manual",
    "manual_servers": [
      { "address": "192.168.3.4", "zone_affinity": { "name": "us-south-1" } }
    ]
  }
}
```

New Pydantic models: `VpcDns`, `DnsResolver`, `DnsManualServer` in `src/models/vpc.py`. The field is accepted in `VpcCreate` and returned in `Vpc` responses. Defaults to `enable_hub: false, resolver: null`.
