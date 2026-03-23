"""
IAM Provider — local IAM token endpoint and JWKS.

Implements the IBM Cloud IAM API surface needed for SDK/CLI authentication:
    POST /identity/token  — issue a signed local JWT for any API key (permissive)
                            or verify against real IBM Cloud first (strict)
    GET  /identity/keys   — return JWKS so callers can verify issued tokens

Modes (IBMCLOUD_LOCAL_AUTH env var):
    permissive (default) — any non-empty API key gets a token, no IBM Cloud call
    strict               — API key is verified against real IBM Cloud before issuing

The RSA key pair is generated at init and held in memory. Tokens are RS256-signed.
"""

import os
import uuid
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.backends import default_backend
from fastapi import Request

from src.providers.base import BaseProvider


class IamProvider(BaseProvider):
    service_name = "iam"
    api_version = "v1"
    description = "IAM token service (local shim)"
    api_base_url = "http://localhost:4515"

    def __init__(self, http_client=None):
        super().__init__()
        self._private_key: RSAPrivateKey = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend(),
        )
        self._kid = str(uuid.uuid4())
        self._http_client = http_client  # injected for testing strict mode

    def register_routes(self):
        self.router.post("/identity/token")(self.issue_token)
        self.router.get("/identity/keys")(self.jwks)

    # ── Public accessor so middleware can reference the same key ──────

    @property
    def private_key(self) -> RSAPrivateKey:
        return self._private_key

    @property
    def kid(self) -> str:
        return self._kid

    # ── Helpers ───────────────────────────────────────────────────────

    def _build_jwks(self) -> dict:
        """Serialize the public key as a JWKS document."""
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
        from jwt.algorithms import RSAAlgorithm
        import json

        pub: RSAPublicKey = self._private_key.public_key()
        jwk_str = RSAAlgorithm.to_jwk(pub)
        jwk = json.loads(jwk_str)
        jwk["use"] = "sig"
        jwk["alg"] = "RS256"
        jwk["kid"] = self._kid
        return {"keys": [jwk]}

    def _issue_local_jwt(self, iam_id: str, apikey_id: str) -> tuple[str, int]:
        """Sign and return (token, expiration_unix_ts)."""
        now = int(time.time())
        exp = now + 3600
        payload = {
            "iss": f"{self.api_base_url}/identity",
            "sub": iam_id,
            "id": iam_id,  # fetchUserDetails reads claims["id"] for UserID
            "iam_id": iam_id,
            "iam_apikey_id": apikey_id,
            "realmid": "iam",
            "scope": "ibm openid",
            "client_id": "bx",
            "iat": now,
            "exp": exp,
            # fetchUserDetails also reads claims["account"]["bss"] for UserAccount.
            "account": {
                "valid": True,
                "bss": "local-emulator-account",
            },
        }
        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"kid": self._kid},
        )
        return token, exp

    # ── Route handlers ────────────────────────────────────────────────

    async def issue_token(self, request: Request):
        """
        POST /identity/token

        Accepts application/x-www-form-urlencoded with:
            grant_type=urn:ibm:params:oauth:grant-type:apikey
            apikey=<any-string>

        Returns IBM Cloud IAM token response shape.
        """
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" not in content_type:
            return self.error_response(
                400, "invalid_request",
                "Content-Type must be application/x-www-form-urlencoded"
            )

        form = await request.form()
        grant_type = form.get("grant_type", "")
        apikey = form.get("apikey", "")

        if not grant_type:
            return self.error_response(400, "invalid_request", "grant_type is required")
        if not apikey:
            return self.error_response(400, "invalid_request", "apikey is required")

        auth_mode = os.environ.get("IBMCLOUD_LOCAL_AUTH", "permissive")

        if auth_mode == "strict":
            iam_id, err = await self._verify_with_ibm_cloud(apikey)
            if err:
                return err
        else:
            # Permissive: accept any key, use a synthetic identity
            iam_id = "iam-ServiceId-local"

        apikey_id = f"ApiKey-{uuid.uuid4()}"
        token, exp = self._issue_local_jwt(iam_id, apikey_id)

        return {
            "access_token": token,
            "refresh_token": "not_supported",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expiration": exp,
            "scope": "ibm openid",
        }

    async def jwks(self):
        """GET /identity/keys — JWKS document for verifying issued tokens."""
        return self._build_jwks()

    async def _verify_with_ibm_cloud(self, apikey: str):
        """
        Call real IBM Cloud to verify the API key.
        Returns (iam_id, None) on success or (None, error_response) on failure.
        The http_client is injected so tests can supply a fake.
        """
        import httpx

        upstream = os.environ.get(
            "IBMCLOUD_LOCAL_IAM_UPSTREAM", "https://iam.cloud.ibm.com"
        )
        client = self._http_client

        try:
            if client is None:
                async with httpx.AsyncClient() as c:
                    resp = await c.post(
                        f"{upstream}/identity/token",
                        content=(
                            "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey"
                            f"&apikey={apikey}"
                        ),
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        timeout=10.0,
                    )
            else:
                resp = await client.post(
                    f"{upstream}/identity/token",
                    content=(
                        "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey"
                        f"&apikey={apikey}"
                    ),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception:
            return None, self.error_response(
                503, "upstream_unavailable",
                "IBM Cloud IAM is unreachable. Check network connectivity."
            )

        if resp.status_code == 400:
            return None, self.error_response(401, "invalid_apikey", "Invalid IBM Cloud API key.")
        if resp.status_code in (401, 403):
            return None, self.error_response(401, "not_authorized", "API key rejected by IBM Cloud.")
        if resp.status_code >= 500:
            return None, self.error_response(
                503, "upstream_error",
                f"IBM Cloud IAM returned {resp.status_code}. Try again later."
            )

        # Extract iam_id from IBM Cloud's token (decode without verification)
        ibm_token = resp.json().get("access_token", "")
        try:
            ibm_payload = jwt.decode(ibm_token, options={"verify_signature": False})
            iam_id = ibm_payload.get("iam_id", "iam-ServiceId-unknown")
        except Exception:
            iam_id = "iam-ServiceId-unknown"

        return iam_id, None
