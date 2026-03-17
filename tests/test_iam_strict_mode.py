"""
Unit and integration tests for IamProvider strict-mode API key verification.

Written RED-first. Unit tests call _verify_with_ibm_cloud() directly with an
injected fake HTTP client. Integration tests hit POST /identity/token with
IBMCLOUD_LOCAL_AUTH=strict and a mutable fake client injected via a
module-scoped fixture (to avoid lifespan re-runs accumulating duplicate routes).

No real IBM Cloud calls are made anywhere in this file.
"""

import base64
import json
import os
import time

import jwt as pyjwt
import pytest

from fastapi.testclient import TestClient

from src.providers.iam import IamProvider
from src.server import app
from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group


# ── Fake HTTP infrastructure ────────────────────────────────────────────


def _make_ibm_token(iam_id: str = "IBMid-real-user-123") -> str:
    """
    Build a minimal HS256 JWT that looks like an IBM Cloud token.
    IamProvider decodes without signature verification, so any signing key works.
    """
    return pyjwt.encode(
        {
            "iam_id": iam_id,
            "sub": iam_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        "fake-secret-key-32bytes-padding!",
        algorithm="HS256",
    )


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class FakeHttpClient:
    """Async-compatible fake for injecting controlled IBM Cloud responses."""

    def __init__(self, status_code: int, body: dict):
        self._response = _FakeResponse(status_code, body)
        self.call_count = 0
        self.last_url: str = ""

    async def post(self, url, **kwargs):
        self.call_count += 1
        self.last_url = url
        return self._response


class MutableFakeHttpClient:
    """Fake client whose response can be reconfigured between tests."""

    def __init__(self):
        self._status = 200
        self._body: dict = {}
        self.call_count = 0
        self.last_url = ""
        self.raise_on_next = False

    def configure(self, status_code: int, body: dict):
        self._status = status_code
        self._body = body
        self.raise_on_next = False

    def configure_raise(self):
        self.raise_on_next = True

    async def post(self, url, **kwargs):
        self.call_count += 1
        self.last_url = url
        if self.raise_on_next:
            raise ConnectionError("Network unreachable")
        return _FakeResponse(self._status, self._body)


class RaisingHttpClient:
    """Fake that simulates a hard network failure."""

    async def post(self, url, **kwargs):
        raise ConnectionError("Network unreachable")


# ── State reset for unit tests ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


# ── Shared TestClient for integration tests ─────────────────────────────
# A module-scoped client avoids re-running lifespan (which registers routes
# and creates a new IamProvider), preventing stale route bindings.


@pytest.fixture(scope="module")
def strict_env():
    """Set IBMCLOUD_LOCAL_AUTH=strict for the duration of the module."""
    old = os.environ.get("IBMCLOUD_LOCAL_AUTH")
    os.environ["IBMCLOUD_LOCAL_AUTH"] = "strict"
    yield
    if old is None:
        os.environ.pop("IBMCLOUD_LOCAL_AUTH", None)
    else:
        os.environ["IBMCLOUD_LOCAL_AUTH"] = old


@pytest.fixture(scope="module")
def integration_client(strict_env):
    """
    Single TestClient for all integration tests.
    Injects a MutableFakeHttpClient onto the module-level IamProvider so each
    test can configure the IBM Cloud response without restarting the server.
    """
    from src.server import _iam_provider as provider
    mutable = MutableFakeHttpClient()
    old_client = provider._http_client
    provider._http_client = mutable
    with TestClient(app) as c:
        yield c, mutable
    provider._http_client = old_client


# ── Unit tests: _verify_with_ibm_cloud ─────────────────────────────────

async def test_valid_apikey_returns_iam_id_from_ibm_token():
    """Successful IBM Cloud response → iam_id extracted from IBM token payload."""
    ibm_token = _make_ibm_token("IBMid-real-123")
    client = FakeHttpClient(200, {"access_token": ibm_token})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("valid-key")

    assert err is None
    assert iam_id == "IBMid-real-123"


async def test_ibm_returns_400_gives_401_error():
    """IBM Cloud rejects the key with 400 → caller gets 401."""
    client = FakeHttpClient(400, {"errorCode": "BXNIM0415E"})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("bad-key")

    assert iam_id is None
    assert err is not None
    assert err.status_code == 401


async def test_ibm_returns_401_gives_401_error():
    client = FakeHttpClient(401, {"error": "not_authorized"})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert iam_id is None
    assert err is not None
    assert err.status_code == 401


async def test_ibm_returns_403_gives_401_error():
    client = FakeHttpClient(403, {"error": "forbidden"})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert iam_id is None
    assert err is not None
    assert err.status_code == 401


async def test_ibm_returns_500_gives_503_error():
    client = FakeHttpClient(500, {"error": "internal_server_error"})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert iam_id is None
    assert err is not None
    assert err.status_code == 503


async def test_network_error_gives_503():
    """Network failure → 503, not an unhandled exception."""
    provider = IamProvider(http_client=RaisingHttpClient())

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert iam_id is None
    assert err is not None
    assert err.status_code == 503


async def test_malformed_ibm_token_uses_fallback_iam_id():
    """If IBM Cloud's token can't be decoded, fall back gracefully."""
    client = FakeHttpClient(200, {"access_token": "not-a-jwt-at-all"})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert err is None
    assert iam_id is not None
    assert isinstance(iam_id, str)


async def test_missing_access_token_in_ibm_response_uses_fallback():
    """IBM Cloud 200 with no access_token field → fallback, not crash."""
    client = FakeHttpClient(200, {})
    provider = IamProvider(http_client=client)

    iam_id, err = await provider._verify_with_ibm_cloud("key")

    assert err is None
    assert iam_id is not None


async def test_calls_upstream_env_var_url(monkeypatch):
    """The upstream URL must honour IBMCLOUD_LOCAL_IAM_UPSTREAM."""
    monkeypatch.setenv("IBMCLOUD_LOCAL_IAM_UPSTREAM", "https://custom-iam.example.com")
    ibm_token = _make_ibm_token("IBMid-abc")
    client = FakeHttpClient(200, {"access_token": ibm_token})
    provider = IamProvider(http_client=client)

    await provider._verify_with_ibm_cloud("key")

    assert "custom-iam.example.com" in client.last_url


# ── Integration tests: POST /identity/token with strict mode ───────────


def _post_token(client, apikey="any-key"):
    return client.post(
        "/identity/token",
        content=(
            "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey"
            f"&apikey={apikey}"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def test_strict_mode_valid_apikey_issues_token(integration_client):
    """Valid IBM Cloud key in strict mode → local JWT returned."""
    c, fake = integration_client
    ibm_token = _make_ibm_token("IBMid-real-123")
    fake.configure(200, {"access_token": ibm_token})

    r = _post_token(c)

    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "Bearer"


def test_strict_mode_bad_apikey_returns_401(integration_client):
    """Invalid IBM Cloud key in strict mode → 401 with IBM error envelope."""
    c, fake = integration_client
    fake.configure(400, {"errorCode": "BXNIM0415E"})

    r = _post_token(c, apikey="bad-key")

    assert r.status_code == 401
    body = r.json()
    assert "errors" in body
    err = body["errors"][0]
    assert "code" in err
    assert "message" in err


def test_strict_mode_local_jwt_carries_real_iam_id(integration_client):
    """Local JWT issued in strict mode must embed iam_id from IBM Cloud's token."""
    c, fake = integration_client
    real_iam_id = "IBMid-verified-user-999"
    ibm_token = _make_ibm_token(real_iam_id)
    fake.configure(200, {"access_token": ibm_token})

    r = _post_token(c)

    assert r.status_code == 200
    local_token = r.json()["access_token"]
    payload_b64 = local_token.split(".")[1]
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    assert payload["iam_id"] == real_iam_id


def test_strict_mode_upstream_500_returns_503(integration_client):
    """IBM Cloud 500 in strict mode → our endpoint returns 503."""
    c, fake = integration_client
    fake.configure(500, {"error": "server_error"})

    r = _post_token(c)

    assert r.status_code == 503
    assert "errors" in r.json()


def test_strict_mode_network_error_returns_503(integration_client):
    """Network failure during IBM Cloud verification → 503."""
    c, fake = integration_client
    fake.configure_raise()

    r = _post_token(c)

    assert r.status_code == 503
    assert "errors" in r.json()
