"""Test helper: sign a client in as the first administrator."""

from typing import Any

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "correct-horse-battery"
CLIENT_HEADER = {"X-Torqrun-Client": "tests"}


def authenticate(client: Any) -> None:  # TestClient or httpx.Client
    """Run first-run setup (creates the admin and a session cookie) and send the CSRF header
    on every later request, like the web UI does."""
    client.headers.update(CLIENT_HEADER)
    r = client.post(
        "/api/v1/auth/setup",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "Admin"},
    )
    assert r.status_code == 201, r.text
