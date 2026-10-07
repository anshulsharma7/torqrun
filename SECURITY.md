# Security policy

Torqrun executes user-provided code on remote machines, so we treat security reports as a
priority.

## Reporting a vulnerability

Please report vulnerabilities **privately** through GitHub's *Report a vulnerability*
(Security → Advisories) on this repository. Don't open public issues. Include steps to
reproduce, the affected version or commit, and the impact you expect. We aim to acknowledge
within 5 working days. Fixes are released as soon as they're ready and credited
in the changelog unless you prefer otherwise.

Torqrun is pre-1.0: only the latest commit on `main` receives security fixes.

## Security model in brief

- **Agents connect out** to the control plane over HTTPS. There are no inbound ports, SSH keys
  or shared secrets in the agent package. Each agent has its own credential, stored hashed,
  rotated, and revocable. Enrollment tokens are single-use by default and expire.
- **Users:**
  - Passwords are hashed with Argon2id.
  - Session cookies are `HttpOnly` and `SameSite=Strict`, and `Secure` in production. Requests
    authenticated by cookie that change state also need a custom header (CSRF protection).
  - Failed logins are throttled.
  - API tokens are stored hashed and can expire.
  - Roles are viewer, operator and admin, enforced on every endpoint.
- **Secrets:**
  - Encrypted with AES-256-GCM using `TORQRUN_SECRET_KEY`. Values are write-only in the API.
  - They're decrypted only into the assignment sent to the agent running the job, and passed
    to containers without appearing on a command line.
  - The agent masks them (and their base64 form) in output.
- **Isolation:**
  - The default process executor runs jobs as the agent's unprivileged OS user, with a
    minimal environment. It is **not a sandbox**.
  - The container executor runs each job in a fresh container: no capabilities,
    `no-new-privileges`, resource limits, optional no network.
  - Access to the Docker socket is root-equivalent on that host, so use dedicated hosts.
- **Trust in the control plane:** a compromised control plane can run code on every enrolled
  agent, by design. Protect it like any deployment system: HTTPS, strong admin passwords,
  few admins, backups, and an audit log you actually read.
- **Audit:** every change and every sign-in attempt is recorded with actor, target, result,
  IP and request ID. Request bodies are never stored.
- **Defaults:**
  - Published ports bind to `127.0.0.1`.
  - Production mode refuses insecure development shortcuts.
  - Notification delivery refuses link-local and metadata addresses.
  - Artifact downloads are always served as attachments.

The full threat model is in [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)
(§9), and the operational guide is in [docs/operations/security.md](docs/operations/security.md).

## Automated checks

Every push runs:
- `ruff`, including the bandit-derived `S` rules, and `mypy --strict`.
- `pip-audit` (Python dependencies) and `pnpm audit` (web dependencies). Any known
  vulnerability fails the build.
- **Trivy** on all four container images. Fixable HIGH/CRITICAL findings fail the build.
- Security integration tests:
  - role enforcement;
  - CSRF;
  - secret values never appearing in responses, logs or the database;
  - masking with a real agent;
  - a symlink in a job's artifacts never leaking an agent's own files.

Run the same scans locally with `make security`.
