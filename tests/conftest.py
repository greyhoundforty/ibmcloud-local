"""
Shared test fixtures.

The auth middleware requires a valid Bearer token on all non-bypass endpoints.
The `auth_headers` fixture obtains a token from /identity/token and returns
the Authorization header dict to pass into TestClient requests.

Existing test modules use a module-scoped `client` fixture and function-scoped
`reset_state`. The `auth_headers` fixture is function-scoped and works with both.
"""

import pytest
from fastapi.testclient import TestClient

from src.server import app


@pytest.fixture(scope="module")
def auth_client():
    """A TestClient with a valid Bearer token pre-attached to every request."""
    with TestClient(app) as c:
        resp = c.post(
            "/identity/token",
            content=(
                "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey"
                "&apikey=local-dev-key"
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token = resp.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
