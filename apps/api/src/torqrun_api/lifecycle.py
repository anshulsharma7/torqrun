"""Server lifecycle signals shared by the app factory and long-running endpoints."""

from typing import Any


def shutting_down(app: Any) -> bool:
    """True once the server stopped accepting connections (wired by __main__; False in tests)."""
    check = getattr(app.state, "is_shutting_down", None)
    return bool(check and check())
