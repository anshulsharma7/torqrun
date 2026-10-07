"""The externally reachable base URL of the control plane (for install commands)."""

from fastapi import Request


def public_url(request: Request) -> str:
    configured = getattr(request.app.state.settings, "public_url", None)
    if configured:
        return str(configured).rstrip("/")
    # Behind nginx/Caddy, uvicorn's proxy_headers support has already applied X-Forwarded-*.
    return str(request.base_url).rstrip("/")
