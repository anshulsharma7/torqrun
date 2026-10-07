#!/usr/bin/env bash
# Torqrun agent installer for Linux (systemd).
#
#   curl -fsSL https://<control-plane>/agent/install.sh | sudo bash -s -- --token tqe_...
#
# Installs into /opt/torqrun-agent (a private Python 3.12 managed by uv, so the host's Python
# version doesn't matter), creates the unprivileged system user `torqrun`, writes
# /etc/torqrun/agent.env and enables the `torq-agent` systemd service. Re-running upgrades the
# agent in place and keeps its identity.
set -euo pipefail

SERVER_URL="${TORQRUN_SERVER_URL:-__TORQRUN_SERVER_URL__}"
TOKEN="" NAME="$(hostname -s 2>/dev/null || hostname)" QUEUES="" TAGS="" SLOTS="" CA_FILE=""
INSECURE_HTTP=0 SYSTEMD=1 DOCKER=0
PREFIX=/opt/torqrun-agent STATE_DIR=/var/lib/torqrun-agent CONF_DIR=/etc/torqrun SERVICE_USER=torqrun

usage() {
  cat <<USAGE
Usage: install.sh --token TOKEN [options]
  --token TOKEN        enrollment token from Torqrun (Fleet -> Connect agent)
  --server URL         control plane URL (default: ${SERVER_URL})
  --name NAME          agent name (default: short hostname)
  --queues a,b         queues to serve (default: from the token, else "default")
  --tags a,b           extra tags
  --slots N            concurrent jobs (default 2)
  --ca-file PATH       trust this CA bundle for the control plane's TLS certificate
  --insecure-http      allow plain http:// (trusted private networks only)
  --docker             offer the container executor: adds the agent user to the `docker`
                       group (root-equivalent on this host; dedicate the machine to Torqrun)
  --no-systemd         install only; print how to start the agent
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    --server) SERVER_URL="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --queues) QUEUES="$2"; shift 2 ;;
    --tags) TAGS="$2"; shift 2 ;;
    --slots) SLOTS="$2"; shift 2 ;;
    --ca-file) CA_FILE="$2"; shift 2 ;;
    --insecure-http) INSECURE_HTTP=1; shift ;;
    --docker) DOCKER=1; shift ;;
    --no-systemd) SYSTEMD=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
[ "$(uname -s)" = Linux ] || die "this installer supports Linux only"
case "$SERVER_URL" in http://*|https://*) ;; *) die "--server must be an http(s) URL";; esac
[ -n "$TOKEN" ] || [ -f "$STATE_DIR/identity.json" ] || die "--token is required for a new installation"
command -v curl >/dev/null || die "curl is required"

say "Installing Torqrun agent for $SERVER_URL"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
if [ "$DOCKER" = 1 ]; then
  getent group docker >/dev/null || die "--docker: no 'docker' group; install Docker Engine first"
  say "Adding $SERVICE_USER to the docker group (container executor)"
  usermod -aG docker "$SERVICE_USER"
fi
install -d -m 0755 "$PREFIX" "$PREFIX/bin"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
install -d -m 0750 -g "$SERVICE_USER" "$CONF_DIR"

CURL=(curl -fsSL)
[ -n "$CA_FILE" ] && CURL+=(--cacert "$CA_FILE")

if [ ! -x "$PREFIX/bin/uv" ]; then
  say "Installing uv (Python toolchain) into $PREFIX/bin"
  curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$PREFIX/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
fi
export UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_CACHE_DIR="$PREFIX/cache"

say "Creating Python 3.12 environment"
"$PREFIX/bin/uv" venv --quiet --allow-existing --python 3.12 "$PREFIX/venv"

say "Downloading agent packages from the control plane"
WHEELS=$("${CURL[@]}" "$SERVER_URL/agent/dist/" | tr -d '[]" ' | tr ',' '\n' | grep '\.whl$') || die "could not list agent packages at $SERVER_URL/agent/dist/"
DL=$(mktemp -d); trap 'rm -rf "$DL"' EXIT
for w in $WHEELS; do "${CURL[@]}" -o "$DL/$w" "$SERVER_URL/agent/dist/$w"; done
"$PREFIX/bin/uv" pip install --quiet --python "$PREFIX/venv/bin/python" "$DL"/*.whl
ln -sf "$PREFIX/venv/bin/torq-agent" /usr/local/bin/torq-agent

CONF="$CONF_DIR/agent.env"
if [ ! -f "$CONF" ] || [ -n "$TOKEN" ]; then
  say "Writing $CONF"
  umask 027
  {
    echo "TORQRUN_AGENT_SERVER_URL=$SERVER_URL"
    echo "TORQRUN_AGENT_NAME=$NAME"
    echo "TORQRUN_AGENT_STATE_DIR=$STATE_DIR"
    [ -n "$TOKEN" ] && echo "TORQRUN_AGENT_ENROLLMENT_TOKEN=$TOKEN"
    [ -n "$QUEUES" ] && echo "TORQRUN_AGENT_QUEUES=$QUEUES"
    [ -n "$TAGS" ] && echo "TORQRUN_AGENT_TAGS=$TAGS"
    [ -n "$SLOTS" ] && echo "TORQRUN_AGENT_MAX_SLOTS=$SLOTS"
    [ -n "$CA_FILE" ] && echo "TORQRUN_AGENT_CA_FILE=$CA_FILE"
    [ "$INSECURE_HTTP" = 1 ] && echo "TORQRUN_AGENT_ALLOW_INSECURE_HTTP=true"
    echo "TORQRUN_AGENT_JOB_PATH=/usr/local/bin:/usr/bin:/bin"
    if [ "$DOCKER" = 1 ]; then echo "TORQRUN_AGENT_DOCKER=on"; else echo "TORQRUN_AGENT_DOCKER=off"; fi
  } > "$CONF"
  chgrp "$SERVICE_USER" "$CONF"
fi

if [ "$SYSTEMD" = 1 ] && command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
  say "Installing systemd service torq-agent"
  cat > /etc/systemd/system/torq-agent.service <<UNIT
[Unit]
Description=Torqrun agent
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
Group=$SERVICE_USER
EnvironmentFile=$CONF
ExecStart=$PREFIX/venv/bin/torq-agent start
Restart=always
RestartSec=5
KillMode=mixed
TimeoutStopSec=40
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now torq-agent >/dev/null
  systemctl restart torq-agent
  sleep 3
  if systemctl is-active --quiet torq-agent; then
    say "Agent running. Check: systemctl status torq-agent · journalctl -u torq-agent -f"
  else
    die "service failed to start: journalctl -u torq-agent -n 50"
  fi
else
  say "Installed. Start the agent with:"
  echo "  sudo -u $SERVICE_USER env \$(grep -v '^#' $CONF | xargs) $PREFIX/venv/bin/torq-agent start"
fi
