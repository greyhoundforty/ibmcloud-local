# ibmcloud-local

A local development emulator for IBM Cloud services. Point your IBM Cloud SDK, CLI, or Terraform provider at `http://localhost:4515` instead of real IBM Cloud endpoints — no account, no cost, no rate limits.

Built on FastAPI with Pydantic v2 models that match the real IBM Cloud API shapes. Includes a local IAM shim so standard `IAMAuthenticator` usage works without modification.

---

## Requirements

- Python 3.12+
- [mise](https://mise.jdx.dev/) (recommended) or a manual venv

---

## Installation

```bash
git clone <repo>
cd ibmcloud-local
mise run install
```

Without mise:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Starting the server

```bash
mise run dev      # hot-reload on port 4515
mise run start    # production mode
```

Or directly:

```bash
uvicorn src.server:app --host 0.0.0.0 --port 4515 --reload
```

The server prints a startup summary listing every registered route:

```
============================================================
  IBM Cloud Local Emulator
  Port: 4515
============================================================
  ✓ iam          → 2 routes
  ✓ resource-manager → 5 routes
  ✓ vpc          → 29 routes
  ✓ network_acl  → 10 routes
  ✓ public_gateway → 8 routes
  ✓ load_balancer → 20 routes
------------------------------------------------------------
  Dashboard: http://localhost:4515/api/dashboard
```

---

## Connecting your tools

### IBM Cloud Python SDK

```python
from ibm_vpc import VpcV1
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator

auth = IAMAuthenticator(
    apikey="any-string-works",
    url="http://localhost:4515",          # /identity/token is appended automatically
    disable_ssl_verification=True,
)
svc = VpcV1(authenticator=auth)
svc.set_service_url("http://localhost:4515/v1")

# List VPCs
result = svc.list_vpcs().get_result()
print(result["vpcs"])
```

Any non-empty string is accepted as an API key in the default permissive mode.

### Environment variables (all SDK languages)

```bash
export IBMCLOUD_VPC_AUTH_TYPE=iam
export IBMCLOUD_VPC_APIKEY=local-dev-key
export IBMCLOUD_VPC_IAM_URL=http://localhost:4515
export IBMCLOUD_VPC_URL=http://localhost:4515/v1
```

### IBM Cloud CLI

Use an isolated config directory to avoid overwriting your real IBM Cloud login:

```bash
export IBMCLOUD_HOME=/tmp/ibmcloud-local-home
mkdir -p $IBMCLOUD_HOME/.bluemix
echo '{"IAMEndpoint":"http://localhost:4515"}' > $IBMCLOUD_HOME/.bluemix/config.json

ibmcloud login --apikey local-dev-key --no-region
ibmcloud is vpcs
```

### Terraform (IBM Cloud provider)

```hcl
provider "ibm" {
  ibmcloud_api_key = "local-dev-key"
  region           = "us-south"
  # Override both the VPC and IAM endpoints
  visibility       = "private"
}
```

Point the provider at the emulator by setting the endpoint URLs:

```bash
export IC_API_KEY=local-dev-key
export IBMCLOUD_IAM_API_ENDPOINT=http://localhost:4515
export IBMCLOUD_IS_NG_API_ENDPOINT=http://localhost:4515
terraform plan
```

### curl / httpx

Get a token first, then use it:

```bash
TOKEN=$(curl -s -X POST http://localhost:4515/identity/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey&apikey=local-dev" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:4515/v1/vpcs \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## Authentication modes

The emulator has two independent authentication controls.

### Token verification (`IBMCLOUD_LOCAL_AUTH`)

Controls how bearer tokens are validated on every request.

| Value | Behaviour |
|---|---|
| `permissive` (default) | Token must be present and structurally valid (3-segment JWT). Signature and expiry are not checked. Any API key is accepted at `/identity/token`. |
| `strict` | The API key is verified against real IBM Cloud before a local token is issued. The real `iam_id` from your IBM Cloud account is embedded in the local JWT. |

Strict mode is useful when you want the emulator to only issue tokens to users who have a valid IBM Cloud API key, while still running locally.

```bash
export IBMCLOUD_LOCAL_AUTH=strict
```

### Policy enforcement (`IBMCLOUD_LOCAL_AUTHZ`)

Controls whether IBM Cloud IAM policies are enforced per request.

| Value | Behaviour |
|---|---|
| `off` (default) | All authenticated requests are allowed. No policy file needed. |
| `enforce` | Each request is checked against a local copy of IBM Cloud IAM policies. Returns 403 if the caller's role does not permit the action. |

To use enforcement, export your real IBM Cloud IAM policies and point the emulator at them:

```bash
ibmcloud iam access-policies --output json > policies.json

export IBMCLOUD_LOCAL_AUTHZ=enforce
export IBMCLOUD_LOCAL_POLICY_FILE=./policies.json
```

When both `IBMCLOUD_LOCAL_AUTH=strict` and `IBMCLOUD_LOCAL_AUTHZ=enforce` are set, the full flow is:

1. Caller provides a real IBM Cloud API key.
2. Emulator verifies the key with IBM Cloud, gets back the real `iam_id`.
3. Local JWT is issued containing that `iam_id`.
4. On each VPC API call, the `iam_id` is looked up in the local policy file.
5. If the caller's role grants the requested action, the request proceeds. Otherwise, 403.

This lets a team share one emulator instance where each member's real IBM Cloud permissions are reflected locally — the same role boundaries they have in production apply in the emulator.

---

## Implemented services

### VPC (`/v1/`)

| Resource | Endpoints |
|---|---|
| VPCs | list, create, get, update, delete |
| Subnets | list, create, get, update, delete; public gateway attach/detach |
| Instances | list, create, get, update, delete, actions (start/stop/reboot) |
| Security Groups | list, create, get, delete; rule CRUD |
| Floating IPs | list, create, get, delete |
| Network ACLs | list, create, get, update, delete; rule CRUD |
| Public Gateways | list, create, get, update, delete |
| Load Balancers | list, create, get, update, delete; listeners, pools, members |

### Resource Manager (`/v2/`)

| Resource | Endpoints |
|---|---|
| Resource Groups | list, create, get, rename, delete |

A `Default` resource group is seeded at startup and cannot be deleted.

### IAM (`/identity/`)

| Endpoint | Purpose |
|---|---|
| `POST /identity/token` | Issue a local RS256-signed JWT for any API key |
| `GET /identity/keys` | JWKS document for verifying issued tokens |

---

## Control plane endpoints

These endpoints manage the emulator itself and never require authentication.

| Endpoint | Purpose |
|---|---|
| `GET /_emulator/health` | Health check |
| `GET /_emulator/state` | Dump full in-memory state (debug) |
| `POST /_emulator/reset` | Wipe all state |
| `POST /_emulator/reset/{namespace}` | Wipe one namespace (e.g. `vpcs`) |
| `GET /api/dashboard` | Service summary and resource counts |
| `GET /api/dashboard/routes` | Full route table |
| `GET /api/dashboard/requests` | Recent request log |

---

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `IBMCLOUD_LOCAL_PORT` | `4515` | Server port |
| `IBMCLOUD_LOCAL_HOST` | `0.0.0.0` | Bind address |
| `IBMCLOUD_LOCAL_AUTH` | `permissive` | Token verification mode (`permissive` or `strict`) |
| `IBMCLOUD_LOCAL_AUTHZ` | `off` | Policy enforcement mode (`off` or `enforce`) |
| `IBMCLOUD_LOCAL_POLICY_FILE` | — | Path to IBM Cloud IAM policy JSON file (required when `AUTHZ=enforce`) |
| `IBMCLOUD_LOCAL_IAM_UPSTREAM` | `https://iam.cloud.ibm.com` | IBM Cloud IAM endpoint (used in strict auth mode) |
| `IBMCLOUD_LOCAL_PERSISTENCE` | `memory` | State persistence (`memory` or `disk`) |
| `IBMCLOUD_LOCAL_LOG_LEVEL` | `info` | Log verbosity |

---

## Running tests

```bash
mise run test          # full suite
pytest tests/ -v       # verbose output
pytest tests/test_vpc_api.py -v   # single file
pytest tests/test_vpc_api.py::test_create_vpc -v   # single test
```

255 tests, ~1.8s.

---

## Development workflow

```bash
mise run lint          # ruff check src/ cli/ tests/
mise run test          # pytest tests/ -v
mise run dev           # start with hot-reload
```

### Adding a new service provider

1. Create `src/models/<service>.py` with Pydantic request/response models.
2. Create `src/providers/<service>.py` subclassing `BaseProvider`.
3. Implement `register_routes()` and route handler methods.
4. Add the provider to `_providers` in `src/server.py`.
5. Write tests in `tests/test_<service>_api.py` — Red first, then Green.

---

## Project layout

```
src/
  server.py             FastAPI app, middleware, control plane endpoints
  routing.py            Route registry (powers dashboard + CLI)
  providers/
    base.py             BaseProvider — shared helpers and error formatting
    iam.py              IAM token endpoint and JWKS
    vpc.py              VPC, Subnets, Instances, Security Groups, Floating IPs
    network_acl.py      Network ACLs
    public_gateway.py   Public Gateways
    load_balancer.py    Load Balancers (listeners, pools, members)
    resource_manager.py Resource Groups
  models/
    vpc.py              Pydantic models for VPC resources
    network_acl.py
    public_gateway.py
    load_balancer.py
  iam/
    policy_store.py     Loads and queries IBM Cloud IAM policy JSON
    vpc_action_map.py   Maps HTTP method + URL path to IAM action strings
  middleware/
    auth.py             Bearer token validation + policy enforcement
    request_logger.py   Request logging for the dashboard feed
  state/
    store.py            Singleton in-memory state store (namespace-keyed)

tests/
  conftest.py           Shared auth_client fixture
  test_vpc_api.py
  test_network_acl_api.py
  test_public_gateway_api.py
  test_load_balancer_api.py
  test_resource_manager_api.py
  test_iam_api.py
  test_iam_strict_mode.py
  test_policy_store.py
  test_authz_enforcement.py
  test_state_store.py

cli/
  ibmcloud_local.py     CLI entry point (ibmcloud-local command)

docs/
  development-status.md Session notes and implementation history
```
