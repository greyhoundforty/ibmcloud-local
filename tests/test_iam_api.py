"""
Integration tests for IAM token endpoint and JWKS.

Written RED-first — all must fail before IamProvider exists.

Design note: the IBM Cloud IAM token endpoint accepts
application/x-www-form-urlencoded bodies, not JSON.
"""

import base64
import json
import pytest
import jwt as pyjwt

from fastapi.testclient import TestClient

from src.server import app
from src.state.store import store as global_store
from src.providers.resource_manager import ensure_default_resource_group


@pytest.fixture(autouse=True)
def reset_state():
    global_store.reset()
    ensure_default_resource_group()
    yield
    global_store.reset()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ── Helpers ────────────────────────────────────────────────────────────

def get_token(client, apikey="any-local-key"):
    """Request a token using IBM Cloud IAM form-encoded body."""
    return client.post(
        "/identity/token",
        content=f"grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey&apikey={apikey}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def _decode_jwt_unverified(token: str) -> dict:
    """Decode JWT payload without signature verification."""
    payload_b64 = token.split(".")[1]
    # Add padding
    padding = 4 - len(payload_b64) % 4
    payload_b64 += "=" * (padding % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


# ── POST /identity/token — permissive mode ─────────────────────────────

def test_token_returns_200(client):
    r = get_token(client)
    assert r.status_code == 200


def test_token_response_shape(client):
    data = get_token(client).json()
    assert "access_token" in data
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 3600
    assert "expiration" in data
    assert isinstance(data["expiration"], int)


def test_token_is_three_segment_jwt(client):
    token = get_token(client).json()["access_token"]
    parts = token.split(".")
    assert len(parts) == 3, "JWT must have header.payload.signature"


def test_token_payload_has_required_claims(client):
    token = get_token(client).json()["access_token"]
    payload = _decode_jwt_unverified(token)
    assert payload["iss"].endswith("/identity")
    assert "sub" in payload
    assert "iam_id" in payload
    assert payload["scope"] == "ibm openid"
    assert payload["client_id"] == "bx"
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


def test_token_payload_has_iam_apikey_id(client):
    token = get_token(client).json()["access_token"]
    payload = _decode_jwt_unverified(token)
    assert "iam_apikey_id" in payload
    assert payload["iam_apikey_id"].startswith("ApiKey-")


def test_token_missing_apikey_returns_400(client):
    r = client.post(
        "/identity/token",
        content="grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 400
    assert "errors" in r.json()


def test_token_missing_grant_type_returns_400(client):
    r = client.post(
        "/identity/token",
        content="apikey=somekey",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 400
    assert "errors" in r.json()


def test_token_wrong_content_type_returns_400(client):
    r = client.post(
        "/identity/token",
        json={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": "key"},
    )
    assert r.status_code == 400
    assert "errors" in r.json()


# ── GET /identity/keys — JWKS ──────────────────────────────────────────

def test_jwks_returns_200(client):
    r = client.get("/identity/keys")
    assert r.status_code == 200


def test_jwks_has_keys_array(client):
    data = client.get("/identity/keys").json()
    assert "keys" in data
    assert len(data["keys"]) >= 1


def test_jwks_key_has_required_fields(client):
    key = client.get("/identity/keys").json()["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "kid" in key
    assert "n" in key
    assert "e" in key


def test_jwks_can_verify_issued_token(client):
    """Token issued by /identity/token must be verifiable with key from /identity/keys."""
    token = get_token(client).json()["access_token"]
    jwks_data = client.get("/identity/keys").json()

    # Verify manually: decode header to get kid, find key, verify
    header_b64 = token.split(".")[0]
    padding = 4 - len(header_b64) % 4
    header_b64 += "=" * (padding % 4)
    header = json.loads(base64.urlsafe_b64decode(header_b64))

    kid = header.get("kid")
    matching_keys = [k for k in jwks_data["keys"] if k.get("kid") == kid]
    assert len(matching_keys) == 1, f"No JWKS key found for kid={kid}"

    # Construct public key and verify
    from jwt.algorithms import RSAAlgorithm
    public_key = RSAAlgorithm.from_jwk(json.dumps(matching_keys[0]))
    payload = pyjwt.decode(token, public_key, algorithms=["RS256"])
    assert payload["scope"] == "ibm openid"


# ── Auth smoke tests — bearer token enforcement ─────────────────────────

def test_valid_token_accepted_on_vpc_endpoint(client):
    token = get_token(client).json()["access_token"]
    r = client.get("/v1/vpcs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_no_auth_header_returns_401(client):
    r = client.get("/v1/vpcs")
    assert r.status_code == 401
    assert "errors" in r.json()


def test_empty_bearer_returns_401(client):
    r = client.get("/v1/vpcs", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    assert "errors" in r.json()


def test_malformed_bearer_not_three_segments_returns_401(client):
    r = client.get("/v1/vpcs", headers={"Authorization": "Bearer notajwt"})
    assert r.status_code == 401
    assert "errors" in r.json()


def test_control_plane_bypasses_auth(client):
    """/_emulator/* and /api/dashboard/* must not require a token."""
    assert client.get("/_emulator/health").status_code == 200
    assert client.get("/api/dashboard").status_code == 200


def test_identity_endpoints_bypass_auth(client):
    """/identity/* must be reachable without a token (so login works)."""
    r = client.get("/identity/keys")
    assert r.status_code == 200


# ── Auth middleware — mode and edge case tests ─────────────────────────

def test_expired_token_returns_401(client, monkeypatch):
    """A structurally valid JWT with exp in the past must be rejected."""
    import time as _time
    import jwt as _jwt

    # Get the private key from the running IamProvider
    from src.server import app as _app
    next(r for r in _app.routes if hasattr(r, "endpoint"))  # touch app to init
    # Build an expired token directly
    from src.providers.iam import IamProvider
    provider = IamProvider()
    now = int(_time.time())
    payload = {
        "iss": "http://localhost:4515/identity",
        "sub": "iam-ServiceId-local",
        "iam_id": "iam-ServiceId-local",
        "iam_apikey_id": "ApiKey-expired",
        "scope": "ibm openid",
        "client_id": "bx",
        "iat": now - 7200,
        "exp": now - 3600,   # expired 1 hour ago
    }
    expired_token = _jwt.encode(
        payload, provider.private_key, algorithm="RS256", headers={"kid": provider.kid}
    )
    r = client.get("/v1/vpcs", headers={"Authorization": f"Bearer {expired_token}"})
    assert r.status_code == 401
    assert "errors" in r.json()


def test_permissive_mode_accepts_any_structurally_valid_jwt(client, monkeypatch):
    """In permissive mode, signature is not checked — any valid structure passes."""
    monkeypatch.setenv("IBMCLOUD_LOCAL_AUTH", "permissive")
    import time as _time
    import jwt as _jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    # Sign with a completely different key — permissive should still allow it
    wrong_key = rsa.generate_private_key(65537, 2048, default_backend())
    now = int(_time.time())
    token = _jwt.encode(
        {
            "iss": "http://localhost:4515/identity",
            "sub": "someone",
            "iam_id": "someone",
            "scope": "ibm openid",
            "client_id": "bx",
            "iat": now,
            "exp": now + 3600,
        },
        wrong_key,
        algorithm="RS256",
    )
    r = client.get("/v1/vpcs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_401_error_envelope_matches_ibm_cloud_shape(client):
    """The 401 error response must use IBM Cloud error envelope format."""
    r = client.get("/v1/vpcs")
    assert r.status_code == 401
    body = r.json()
    assert "errors" in body
    assert isinstance(body["errors"], list)
    err = body["errors"][0]
    assert "code" in err
    assert "message" in err
