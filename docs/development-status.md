# ibmcloud-local — Development Status

## Session summary (2026-03-20)

### Goal

Extend the emulator's JWT payload to fix an IBM Terraform provider panic, determine the correct provider version and endpoint configuration for local testing, and establish the boundaries of what "bluestack" can support today.

---

### What worked

#### JWT `id` claim fix

The IBM Terraform provider panics at `fetchUserDetails` in `config.go:4117` because it does a hard type-assertion on `claims["id"]`:

```go
user.UserID = claims["id"].(string)       // line 4117 — panics if nil
user.UserAccount = claims["account"]["bss"].(string)  // line 4118
```

The `account.bss` claim was already present from the previous session. Adding the `id` claim resolved the panic:

```python
# src/providers/iam.py — _issue_local_jwt()
"id": iam_id,   # IBM Terraform provider fetchUserDetails reads claims["id"]
```

All 35 IAM tests continue to pass after the change.

#### Provider version pinning to 1.79.0

IBM Terraform provider versions >= ~1.82 introduced a paired `RequiredWith` schema constraint:

- `iam_profile_name` requires `ibmcloud_account_id`
- `ibmcloud_account_id` requires `iam_profile_name`

This constraint fires at `PrepareProviderConfig` (schema validation, before `ConfigureFunc`) regardless of whether either attribute is set in the provider block or in environment variables. There is no documented workaround. Provider version `1.79.0` predates this constraint and works cleanly with just `ibmcloud_api_key` + `region`.

#### VPC endpoint environment variable

Version 1.79.0 reads the VPC/IS endpoint from `RIAAS_ENDPOINT`, not `IBMCLOUD_IS_NG_API_ENDPOINT` (which was introduced in a later release). The `bluestack-env.sh` helper now exports both:

```bash
export RIAAS_ENDPOINT="${BLUESTACK_URL}/v1"
export IBMCLOUD_IS_NG_API_ENDPOINT="${BLUESTACK_URL}/v1"
```

This makes the script forward-compatible if the provider version is later bumped.

#### Endpoint variable audit (v1.79.0)

| Service | Env var (v1.79.0) | Value |
|---|---|---|
| IAM | `IBMCLOUD_IAM_API_ENDPOINT` | `http://localhost:4515` |
| VPC | `RIAAS_ENDPOINT` | `http://localhost:4515/v1` |
| Transit Gateway | `IBMCLOUD_TG_API_ENDPOINT` | `http://localhost:4515/v1` |

#### IC_IAM_TOKEN removed from bluestack-env.sh

Pre-fetching a token and exporting it as `IC_IAM_TOKEN` triggers a different provider auth code path in v1.79.0 that expects a paired `iam_refresh_token`. Removing it and letting the provider authenticate via `ibmcloud_api_key` against `IBMCLOUD_IAM_API_ENDPOINT` is both simpler and more correct.

#### terraform plan partially succeeded

With the above fixes in place, `terraform plan` progressed further than ever:
- IAM auth succeeded (provider parsed the JWT without panic)
- `ibm_tg_gateway` was fully planned (Transit Gateway endpoint works end-to-end)
- `ibm_tg_connection` resources were planned correctly

---

### What did not work

#### `ibm_is_vpc` CustomizeDiff token error

After planning Transit Gateway resources successfully, the provider calls `CustomizeDiff` for `ibm_is_vpc` (from the `terraform-ibm-modules/vpc` module). This hook makes a live API call — likely to validate instance profiles or image availability — and returns:

```
Your authentication token is not valid
severity: error
resource: ibm_is_vpc
operation: CustomizeDiff
```

Root cause is not fully isolated. Candidates:
1. `CustomizeDiff` calls an endpoint the emulator does not implement (e.g. `GET /v1/instance/profiles`, `GET /v1/images`, or `GET /v1/regions`)
2. The token is reaching the emulator but the middleware rejects it for a reason not yet traced
3. The VPC module's `CustomizeDiff` may call a global IBM Cloud catalog endpoint that cannot be redirected

**Status: blocked.** The Terraform path is set aside for now. curl and the Python SDK work correctly and are the recommended interfaces for local testing.

---

### Current interface recommendations

| Interface | Status | Notes |
|---|---|---|
| Python SDK (`ibm-vpc`, `ibm-cloud-networking-services`) | Working | Set `set_service_url("http://localhost:4515/v1")` |
| curl | Working | See README quick-start |
| Terraform (TGW resources) | Partially working | Plan succeeds, apply not tested |
| Terraform (VPC module) | Blocked | `CustomizeDiff` token error |

---

### Next steps: VPC endpoint gaps

The VPC provider currently implements: VPCs, Subnets, Instances, Security Groups, Floating IPs, Network ACLs, Public Gateways, and Load Balancers.

The following are missing and needed to support realistic Terraform and SDK workflows:

#### High priority (block common workflows)

| Endpoint group | Why needed |
|---|---|
| `GET /v1/instance/profiles` | Required by Terraform `ibm_is_instance` validation and `ibm_is_vpc` CustomizeDiff; blocks `terraform plan` |
| `GET /v1/images` / `GET /v1/images/{id}` | Instance creation references an image by ID; SDK callers look up images before creating VSIs |
| `GET /v1/regions` / `GET /v1/regions/{name}/zones` | Provider validates region/zone at plan time; also needed by the VPC module |
| `GET /v1/keys` / `POST /v1/keys` | SSH key management; `InstanceCreate` currently ignores the `keys` field entirely |

#### Medium priority (extend existing resources)

| Endpoint group | Why needed |
|---|---|
| `GET /v1/volumes` / `POST /v1/volumes` | Block storage; `InstanceCreate` ignores `boot_volume_attachment` |
| `GET /v1/instances/{id}/volume_attachments` | Attach/detach volumes to instances |
| `GET /v1/instances/{id}/network_interfaces` | Multi-NIC instances; floating IP attachment to specific interfaces |
| `POST /v1/instances/{id}/actions` | Start/stop/reboot already exist but are not wired to the VPC module's expected action format |
| `GET /v1/subnets/{id}/reserved_ips` | Subnet IP tracking; some SDK calls enumerate reserved IPs |

#### Lower priority (completeness)

| Endpoint group | Notes |
|---|---|
| `GET /v1/snapshots` | Volume snapshots |
| `GET /v1/vpn_gateways` | VPN connectivity |
| `GET /v1/endpoint_gateways` | VPE (Virtual Private Endpoint) |
| `GET /v1/flow_log_collectors` | Flow log configuration |
| `GET /v1/instance/templates` | Instance templates / auto-scale groups |
| `GET /v1/placement_groups` | Placement group constraints |
| `GET /v1/dedicated_hosts` | Dedicated host management |
| `GET /v1/bare_metal_servers` | Bare metal (optional — complex) |

#### Pagination (cross-cutting)

All list endpoints currently return every resource in a single response. The real IBM Cloud API uses cursor-based pagination:

```json
{
  "limit": 50,
  "next": { "href": "...", "start": "<cursor>" },
  "resources": [...]
}
```

SDKs that call `list_all_*` (auto-paginating wrappers) will misbehave without this. A single pagination middleware or helper on `StateStore` would address all list endpoints at once.

---

### Suggested implementation order

1. **Static catalog endpoints** — `GET /v1/instance/profiles`, `GET /v1/regions`, `GET /v1/regions/{name}/zones`, `GET /v1/images` — these return fixed data and unblock Terraform `CustomizeDiff`
2. **SSH Keys** — straightforward CRUD, unblocks instance creation with key injection
3. **Volumes + volume attachments** — extend instance model to accept `boot_volume_attachment`
4. **Pagination** — implement once across all list endpoints
5. **Network interfaces** — extend instance response, wire floating IP attachment

---

## Session summary (2026-03-17)

### What was built

#### Transit Gateway provider (Red/Green complete)

New provider emulating the IBM Cloud Transit Gateway API (`https://transit.cloud.ibm.com/v1`).

**New files:**

| File | Purpose |
|---|---|
| `src/models/transit_gateway.py` | Pydantic models — `TransitGatewayCreate`, `TransitGatewayUpdate`, `TransitGatewayConnectionCreate`, response types |
| `src/providers/transit_gateway.py` | `TransitGatewayProvider` — 10 endpoints |
| `tests/test_transit_gateway_api.py` | 35 tests, all passing |

**Endpoints:**

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/transit_gateways` | List all |
| POST | `/v1/transit_gateways` | Create; 409 on duplicate name |
| GET | `/v1/transit_gateways/{id}` | Get; includes live `connection_count` |
| PATCH | `/v1/transit_gateways/{id}` | Rename / toggle global routing |
| DELETE | `/v1/transit_gateways/{id}` | 409 if connections still attached |
| GET | `/v1/transit_gateways/{id}/connections` | List connections for one gateway |
| POST | `/v1/transit_gateways/{id}/connections` | VPC and PowerVS types; 409 on duplicate `network_id` |
| GET | `/v1/transit_gateways/{id}/connections/{conn_id}` | Get one connection |
| DELETE | `/v1/transit_gateways/{id}/connections/{conn_id}` | Detach |
| GET | `/v1/connections` | Global — all connections across all gateways, each with nested `transit_gateway` reference |

All endpoints require `?version=YYYY-MM-DD` query parameter, matching the real IBM Cloud API contract.

**Notable behaviours:**
- `global` JSON key is aliased via `Field(alias="global")` to work around Python's reserved keyword.
- Connections are stored in per-gateway namespaces (`tgw_connections_{id}`) for clean isolation.
- Deleting a gateway with active connections returns 409 — connections must be removed first.
- Duplicate `network_id` within the same gateway returns 409 (prevents attaching the same VPC/workspace twice).
- The global `/v1/connections` endpoint aggregates across all gateways and injects a `transit_gateway` reference on each entry, matching the real API shape.
- Supported `network_type` values: `vpc`, `power_virtual_server` (others accepted but not validated).

#### SDK URL fix

The IBM Cloud VPC Python SDK sets `DEFAULT_SERVICE_URL = 'https://us-south.iaas.cloud.ibm.com/v1'` and appends resource paths (e.g. `/vpcs`) relative to that base. `set_service_url("http://localhost:4515")` therefore sends requests to `http://localhost:4515/vpcs` (404), not `/v1/vpcs`.

Correct usage:
```python
svc.set_service_url("http://localhost:4515/v1")   # include /v1
```

The README and environment variable examples were updated accordingly (`IBMCLOUD_VPC_URL=http://localhost:4515/v1`).

#### GitHub Actions CI

`.github/workflows/ci.yml` added with two jobs:
- **Lint** — `ruff check src/ cli/ tests/` on Python 3.12
- **Test** — `pytest tests/ -v --tb=short` on Python 3.11 and 3.12 in parallel

`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` set at workflow level to silence Node.js 20 deprecation warnings ahead of the June 2026 forced cutover.

#### Architecture fix: route registration at module level

Providers are now instantiated and `app.include_router()` called at `server.py` module import time, not inside the `lifespan` handler. This prevents duplicate route accumulation when `TestClient(app)` is constructed multiple times across the test suite.

---

Total test count after this session: **290 tests, all passing**.

---

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
