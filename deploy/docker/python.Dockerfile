# syntax=docker/dockerfile:1.7
# Python services. Build from the repo root:
#   docker build -f deploy/docker/python.Dockerfile --target api .
#   docker build -f deploy/docker/python.Dockerfile --target scheduler .
#   docker build -f deploy/docker/python.Dockerfile --target agent .

FROM ghcr.io/astral-sh/uv:0.12 AS uv

FROM python:3.12-slim-bookworm AS build-base
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# Workspace metadata and sources. The private repository also contains packages/torqrun-ee
# (Torqrun Enterprise); when present it is installed into the API and scheduler images.
COPY pyproject.toml uv.lock ./
COPY packages packages
COPY apps/api apps/api
COPY apps/agent apps/agent
COPY apps/scheduler apps/scheduler

# --- API ---------------------------------------------------------------------------------
FROM build-base AS build-api
RUN --mount=type=cache,target=/root/.cache/uv \
    pkg=torqrun-api; [ -f packages/torqrun-ee/pyproject.toml ] && pkg=torqrun-ee; \
    uv sync --frozen --no-dev --no-editable --package "$pkg"
# Agent wheels, served by the API at /agent/dist/ for the one-line installer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --quiet --wheel --package torqrun-agent --out-dir /opt/agent-dist \
 && uv build --quiet --wheel --package torqrun-protocol --out-dir /opt/agent-dist

# --- Scheduler -------------------------------------------------------------------------
FROM build-base AS build-scheduler
RUN --mount=type=cache,target=/root/.cache/uv \
    pkg=torqrun-scheduler; [ -f packages/torqrun-ee/pyproject.toml ] && pkg=torqrun-ee; \
    uv sync --frozen --no-dev --no-editable --package "$pkg"

# --- Agent: only the agent and the protocol package (no DB drivers, no web framework) ------
FROM build-base AS build-agent
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package torqrun-agent

FROM python:3.12-slim-bookworm AS runtime-base
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin torqrun
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

FROM runtime-base AS api
COPY --from=build-api /opt/venv /opt/venv
COPY --from=build-api /opt/agent-dist /opt/torqrun/agent/dist
COPY deploy/install/install-agent.sh /opt/torqrun/agent/install-agent.sh
# Artifact store; created here so a fresh named volume inherits the owner.
RUN install -d -o torqrun -g torqrun -m 0750 /var/lib/torqrun/artifacts
ENV PATH=/opt/venv/bin:$PATH \
    TORQRUN_API_HOST=0.0.0.0 \
    TORQRUN_API_PORT=8000
USER torqrun
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=4s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3)"]
CMD ["torqrun-api"]

FROM runtime-base AS scheduler
COPY --from=build-scheduler /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    TORQRUN_SCHEDULER_HEARTBEAT_FILE=/tmp/torqrun-scheduler.alive
USER torqrun
# Healthy while the loop keeps touching its heartbeat file.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import os, sys, time; sys.exit(time.time() - os.path.getmtime('/tmp/torqrun-scheduler.alive') > 30)"]
CMD ["torqrun-scheduler"]

# The agent runs jobs inside this container as the unprivileged `torqrun` user. Jobs get
# /usr/local/bin/python3 (plain CPython, not the agent's virtualenv) and bash.
FROM runtime-base AS agent
# tini runs as PID 1 and reaps orphaned job processes (e.g. `cmd &` killed on timeout).
# Without it the agent would be PID 1 and those processes would linger as zombies.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build-agent /opt/venv /opt/venv
RUN install -d -o torqrun -g torqrun -m 0700 /var/lib/torqrun-agent
ENV PATH=/opt/venv/bin:$PATH \
    TORQRUN_AGENT_STATE_DIR=/var/lib/torqrun-agent
USER torqrun
VOLUME ["/var/lib/torqrun-agent"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["torq-agent", "start"]
