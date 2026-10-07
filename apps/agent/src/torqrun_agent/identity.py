"""Persistent agent identity (agent ID + credential), stored with owner-only permissions."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

IDENTITY_FILE = "identity.json"


@dataclass(frozen=True)
class Identity:
    server_url: str
    agent_id: UUID
    name: str
    credential: str
    # ISO timestamp of the last credential (re)issue; drives automatic rotation.
    issued_at: str | None = None


def identity_path(state_dir: Path) -> Path:
    return state_dir / IDENTITY_FILE


def load(state_dir: Path) -> Identity | None:
    path = identity_path(state_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Identity(
        server_url=data["server_url"],
        agent_id=UUID(data["agent_id"]),
        name=data["name"],
        credential=data["credential"],
        issued_at=data.get("issued_at"),
    )


def save(state_dir: Path, identity: Identity) -> Path:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = identity_path(state_dir)
    tmp = path.with_suffix(".tmp")
    # Create with 0600 from the start; never briefly world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({**asdict(identity), "agent_id": str(identity.agent_id)}, f, indent=2)
    os.replace(tmp, path)
    return path
