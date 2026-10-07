"""``torqrun-agent`` command line: register, start, status."""

import argparse
import asyncio
import logging
import platform
import signal
import sys
from dataclasses import replace
from datetime import UTC, datetime

from pydantic import ValidationError

from torqrun_agent import __version__, identity
from torqrun_agent.client import AuthError, ControlPlane, ControlPlaneError
from torqrun_agent.config import AgentSettings
from torqrun_agent.runner import Agent, detect_capabilities
from torqrun_protocol.agent import EnrollRequest, HeartbeatRequest

logger = logging.getLogger("torqrun_agent")


def _settings() -> AgentSettings:
    try:
        return AgentSettings()  # values come from the environment
    except ValidationError as exc:
        lines = [f"  {'.'.join(map(str, e['loc'])) or 'config'}: {e['msg']}" for e in exc.errors()]
        sys.exit("invalid agent configuration (TORQRUN_AGENT_* variables):\n" + "\n".join(lines))


async def register(settings: AgentSettings) -> identity.Identity:
    existing = identity.load(settings.state_dir)
    if existing is not None:
        logger.info("already registered as %s (%s)", existing.name, existing.agent_id)
        return existing
    if settings.enrollment_token is None:
        sys.exit("not registered and TORQRUN_AGENT_ENROLLMENT_TOKEN is not set")
    client = ControlPlane(settings.server_url, ca_file=_ca(settings))
    try:
        response = await client.enroll(
            EnrollRequest(
                enrollment_token=settings.enrollment_token.get_secret_value(),
                name=settings.name,
                hostname=platform.node(),
                os=platform.system().lower(),
                arch=platform.machine(),
                agent_version=__version__,
                max_slots=settings.max_slots,
                queues=settings.queues,
                tags=settings.tags,
                capabilities=detect_capabilities(settings)[0],
            )
        )
    except ControlPlaneError as exc:
        sys.exit(f"enrollment failed: {exc}")
    finally:
        await client.aclose()
    ident = identity.Identity(
        server_url=settings.server_url,
        agent_id=response.agent_id,
        name=settings.name,
        credential=response.credential,
        issued_at=datetime.now(UTC).isoformat(),
    )
    path = identity.save(settings.state_dir, ident)
    logger.info(
        "registered as %s (%s), queues %s; identity saved to %s",
        ident.name,
        ident.agent_id,
        ",".join(response.queues),
        path,
    )
    return ident


async def start(settings: AgentSettings) -> None:
    ident = await register(settings)
    if ident.server_url != settings.server_url:
        sys.exit(
            f"identity in {settings.state_dir} belongs to {ident.server_url}, "
            f"not {settings.server_url}; use a different TORQRUN_AGENT_STATE_DIR"
        )
    client = ControlPlane(settings.server_url, ident.credential, ca_file=_ca(settings))
    agent = Agent(settings, client, ident)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent.stop)
    logger.info(
        "agent %s started: %d slot(s), queues %s, server %s",
        ident.name,
        settings.max_slots,
        ",".join(settings.queues),
        settings.server_url,
    )
    try:
        await agent.run()
    except AuthError as exc:
        sys.exit(f"stopped: {exc}")
    finally:
        await client.aclose()
    logger.info("agent stopped")


async def status(settings: AgentSettings) -> int:
    ident = identity.load(settings.state_dir)
    if ident is None:
        print(f"not registered (state dir {settings.state_dir})")
        return 1
    print(f"name:      {ident.name}\nagent id:  {ident.agent_id}\nserver:    {ident.server_url}")
    client = ControlPlane(ident.server_url, ident.credential, retries=0, ca_file=_ca(settings))
    try:
        hb = await client.heartbeat(HeartbeatRequest(running=[], free_slots=0))
    except Exception as exc:  # report any failure plainly; this is a diagnostic command
        print(f"control plane: UNREACHABLE or rejected ({exc})")
        return 1
    finally:
        await client.aclose()
    print(f"control plane: OK (server time {hb.server_time.isoformat()})")
    return 0


def _ca(settings: AgentSettings) -> str | None:
    return str(settings.ca_file) if settings.ca_file else None


async def _with_identity(settings: AgentSettings, action: str) -> int:
    ident = identity.load(settings.state_dir)
    if ident is None:
        print(f"not registered (state dir {settings.state_dir})")
        return 1
    client = ControlPlane(ident.server_url, ident.credential, retries=2, ca_file=_ca(settings))
    try:
        if action == "drain":
            await client.drain()
            print("draining: no new jobs will be sent to this agent; running jobs finish normally")
        else:
            fresh = await client.rotate_credential()
            identity.save(
                settings.state_dir,
                replace(
                    ident, credential=fresh.credential, issued_at=datetime.now(UTC).isoformat()
                ),
            )
            print("credential rotated (restart the agent if it is running to use it immediately)")
    except (ControlPlaneError, OSError) as exc:
        print(f"failed: {exc}")
        return 1
    finally:
        await client.aclose()
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="torq-agent", description="Torqrun agent")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="enroll with the control plane and save the identity")
    sub.add_parser("start", help="run the agent (registers first if needed)")
    sub.add_parser("status", help="show identity and check the control plane connection")
    sub.add_parser(
        "drain", help="stop receiving new jobs (running jobs finish); resume from the UI"
    )
    sub.add_parser("rotate-credential", help="replace this agent's credential now")
    args = parser.parse_args(argv)

    settings = _settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "register":
        asyncio.run(register(settings))
    elif args.command == "start":
        asyncio.run(start(settings))
    elif args.command == "status":
        sys.exit(asyncio.run(status(settings)))
    else:
        sys.exit(asyncio.run(_with_identity(settings, args.command)))


if __name__ == "__main__":
    main()
