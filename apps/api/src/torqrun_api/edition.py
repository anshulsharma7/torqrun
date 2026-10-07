"""Editions. The open-source build is the Community Edition; paid features (Torqrun Enterprise)
are a separate package that, when installed and licensed, registers itself here.

The Community code never imports paid code: it calls ``load_extensions`` and asks the
resulting ``Edition`` whether a feature is available.
"""

import importlib.util
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_api.settings import Settings

logger = logging.getLogger(__name__)

# Features that exist only in paid editions; used for clear "upgrade" messages.
PAID_FEATURES = {
    "users": "Multiple users and roles",
    "secrets": "Secrets manager",
    "audit": "Audit log",
    "notifications": "Notifications and alerts",
}
UPGRADE_HINT = (
    "available in Torqrun Team and Enterprise: see /plans or contact anshulshrm12@gmail.com"
)

SecretResolver = Callable[
    [AsyncSession, dict[str, str]], Awaitable[tuple[dict[str, str], list[str]]]
]


class Edition:
    """Community Edition: no paid features."""

    name = "community"

    def has(self, feature: str) -> bool:
        return False

    def license_summary(self) -> dict[str, Any] | None:
        return None

    def features(self) -> list[str]:
        return sorted(f for f in PAID_FEATURES if self.has(f))


async def no_secrets(_: AsyncSession, wanted: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Community: job secrets can't be resolved (the secrets manager is a paid feature)."""
    return {}, sorted(set(wanted.values()))


def load_extensions(app: FastAPI, settings: Settings) -> Edition:
    """Install Torqrun Enterprise if it is present (it checks its own license)."""
    app.state.secret_resolver = no_secrets
    if importlib.util.find_spec("torqrun_ee") is None:
        return Edition()
    from torqrun_ee.api import install  # type: ignore[import-not-found,unused-ignore]

    edition: Edition = install(app, settings)
    return edition


def edition_of(request: Request) -> Edition:
    edition: Edition = request.app.state.edition
    return edition


def require_feature(request: Request, feature: str) -> None:
    if not edition_of(request).has(feature):
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"{PAID_FEATURES.get(feature, feature)} is {UPGRADE_HINT}",
        )
