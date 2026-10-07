import stat
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from torqrun_agent import identity
from torqrun_agent.config import AgentSettings


def test_refuses_plain_http_to_remote_server() -> None:
    with pytest.raises(ValidationError, match="refusing plain http"):
        AgentSettings(server_url="http://control.example.com")


@pytest.mark.parametrize(
    "url", ["https://control.example.com/", "http://127.0.0.1:8000", "http://localhost"]
)
def test_accepts_tls_or_local_http(url: str) -> None:
    assert AgentSettings(server_url=url).server_url == url.rstrip("/")


def test_insecure_http_requires_explicit_opt_in() -> None:
    s = AgentSettings(server_url="http://api:8000", allow_insecure_http=True)
    assert s.server_url == "http://api:8000"


def test_queues_and_tags_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TORQRUN_AGENT_SERVER_URL", "https://x")
    monkeypatch.setenv("TORQRUN_AGENT_QUEUES", "default, gpu")
    monkeypatch.setenv("TORQRUN_AGENT_TAGS", "linux,eu-west")
    s = AgentSettings()  # values come from the environment
    assert s.queues == ["default", "gpu"]
    assert s.tags == ["linux", "eu-west"]


def test_identity_round_trip_with_owner_only_permissions(tmp_path: Path) -> None:
    state = tmp_path / "state"
    ident = identity.Identity("https://x", uuid.uuid4(), "a1", "tqa_secret")
    path = identity.save(state, ident)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert identity.load(state) == ident
    assert identity.load(tmp_path / "missing") is None
