"""Self-hosted agent distribution: the install script and agent wheels, served by the control
plane itself so servers can install an agent with nothing but curl and the control plane URL.

Contents are public on purpose (no secrets: the enrollment token is passed on the command line).
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, PlainTextResponse

from torqrun_api.deps import SettingsDep
from torqrun_api.public_url import public_url

router = APIRouter(prefix="/agent", tags=["agent install"])

WHEEL = re.compile(r"^[A-Za-z0-9_.+-]+\.whl$")


def _assets(settings: SettingsDep) -> Path:
    return Path(settings.agent_assets_dir)


@router.get("/install.sh", response_class=PlainTextResponse)
async def install_script(request: Request, settings: SettingsDep) -> PlainTextResponse:
    script = _assets(settings) / "install-agent.sh"
    if not script.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="agent installer not bundled with this build"
        )
    body = script.read_text().replace("__TORQRUN_SERVER_URL__", public_url(request))
    return PlainTextResponse(body, media_type="text/x-shellscript")


@router.get("/dist/")
async def list_dist(settings: SettingsDep) -> list[str]:
    dist = _assets(settings) / "dist"
    if not dist.is_dir():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="agent packages not bundled with this build"
        )
    return sorted(p.name for p in dist.iterdir() if WHEEL.match(p.name))


@router.get("/dist/{filename}")
async def get_dist(filename: str, settings: SettingsDep) -> FileResponse:
    path = _assets(settings) / "dist" / filename
    # Name pattern + is_file on a path built from a validated name: no traversal possible.
    if not WHEEL.match(filename) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such package")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)
