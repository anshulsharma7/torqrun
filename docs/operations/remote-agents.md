# Running agents on other machines

Agents connect **out** to the control plane over HTTPS. Nothing connects in to them, so they
work behind NAT and firewalls. Each agent has its own revocable credential.

## 1. Make the control plane reachable over HTTPS

Remote machines need a URL they can reach. The Compose stack binds to `127.0.0.1` only; the
TLS overlay adds [Caddy](https://caddyserver.com) on ports 80/443 with automatic certificates:

```bash
# DNS for torqrun.example.com must point at this host, ports 80 and 443 reachable.
TORQRUN_DOMAIN=torqrun.example.com make up-tls
```

No public name (lab, private network)? Use Caddy's own CA and give agents its root certificate:

```bash
TORQRUN_DOMAIN=torqrun.internal TORQRUN_CADDY_TLS="tls internal" make up-tls
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.tls.yml \
  --project-directory . cp caddy:/data/caddy/pki/authorities/local/root.crt ./torqrun-ca.crt
# copy torqrun-ca.crt to each server and pass --ca-file /path/torqrun-ca.crt to the installer
```

> ⚠️ Exposing the control plane means anyone who can reach it can use it **until
> authentication is enabled** (milestone M6). Restrict access (firewall / VPN / security group)
> until then.

## 2. Create an enrollment token

UI: **Fleet → Connect agent → Create token.** Tokens are single-use, expire (15 min – 7 days)
and can preset the queues the agent serves. The secret is shown once; only a hash is stored.

API: `POST /api/v1/enrollment-tokens {"description": "web-01", "ttl_minutes": 60, "queues": ["default"]}`

## 3. Install the agent

On the server, as root — the UI shows this exact line:

```bash
curl -fsSL https://torqrun.example.com/agent/install.sh | sudo bash -s -- --token tqe_…
```

The installer, served by the control plane itself:

* installs a private Python 3.12 via `uv` under `/opt/torqrun-agent` (the host Python version
  doesn't matter: tested on Ubuntu 22.04 with no Python installed),
* downloads the agent packages from `https://<control-plane>/agent/dist/`,
* creates the unprivileged system user `torqrun` and `/var/lib/torqrun-agent` (mode 0700),
* writes `/etc/torqrun/agent.env` (mode 0640, `root:torqrun`),
* installs and starts the `torq-agent` systemd service (`Restart=always`, `NoNewPrivileges`).

Options: `--name`, `--queues a,b`, `--tags a,b`, `--slots N`, `--ca-file PATH`,
`--insecure-http` (private networks only), `--no-systemd`. Re-running upgrades in place and
keeps the agent's identity.

On AWS EC2, the same line works as **user data** (it runs as root on first boot); allow the
instance outbound HTTPS to the control plane — no inbound rules are needed.

```bash
systemctl status torq-agent       # service state
journalctl -u torq-agent -f       # logs
sudo -u torqrun env $(cat /etc/torqrun/agent.env | xargs) torq-agent status
```

## 4. Lifecycle

| Action | How | Effect |
|---|---|---|
| Drain | UI agent page → **Drain**, or `torq-agent drain` on the machine | No new jobs; running jobs finish. **Resume** undoes it. |
| Revoke | UI agent page → **Revoke** | Credentials stop working immediately; running jobs are marked *Lost* (retried if the job allows); unacknowledged work is requeued. Re-enroll with a new token to bring the machine back. |
| Rotate credential | Automatic every 7 days (`TORQRUN_AGENT_CREDENTIAL_ROTATE_DAYS`), or `torq-agent rotate-credential` | New credential saved locally first; the old one keeps working for 5 minutes. |
| Uninstall | `systemctl disable --now torq-agent && rm -rf /opt/torqrun-agent /etc/torqrun /var/lib/torqrun-agent /etc/systemd/system/torq-agent.service && userdel torqrun`, then revoke it in the UI | |

## Security notes

* Enrollment tokens and agent credentials are 256-bit random values stored only as SHA-256
  hashes. Credentials are checked against the database on every request, so revocation is
  immediate (no cached tokens to wait out).
* The agent refuses plain `http://` to anything but localhost unless `--insecure-http` /
  `TORQRUN_AGENT_ALLOW_INSECURE_HTTP=true` is set.
* Jobs run as the `torqrun` user. That is **not a sandbox**: a job can read anything that
  user can, including the agent's credential file. Use separate machines for untrusted work,
  and the container executor (M7) when available.
