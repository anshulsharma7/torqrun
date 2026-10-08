# Editions and plans

Torqrun is **open core**, like Dagster. The orchestration engine is free and open source.
Collaboration, security and alerting features, which teams need once more than one person
depends on Torqrun, are part of the paid **Team** and **Enterprise** plans. Paid plans can be
**self-hosted with a license key** or **managed by us**.

| | **Community** | **Team** | **Enterprise** |
|---|---|---|---|
| Price | **Free**, Apache-2.0 | Contact us | Contact us |
| For | One operator, side projects, evaluation | Teams running production jobs | Organisations with security and compliance needs |
| Hosting | Self-hosted (Docker Compose) | Self-hosted with a license key, or managed by us | Self-hosted, on-prem or air-gapped, or managed |
| Support | GitHub issues | Priority email, next business day | SLA, dedicated contact |
| Contact | [GitHub](https://github.com/anshulsharma7/torqrun) | [anshulshrm12@gmail.com](mailto:anshulshrm12@gmail.com?subject=Torqrun%20Team%20plan) | [anshulshrm12@gmail.com](mailto:anshulshrm12@gmail.com?subject=Torqrun%20Enterprise%20plan) |

## Feature comparison

✅ included · 🗺 roadmap, built for Enterprise on request · — not included

| Feature | Community | Team | Enterprise |
|---|---|---|---|
| **Orchestration** | | | |
| Python and shell jobs, immutable versions, parameters, environment | ✅ | ✅ | ✅ |
| Cron and interval schedules with time zones, missed-run and overlap policies | ✅ | ✅ | ✅ |
| Workflows (DAGs) with trigger rules, rerun of failed tasks | ✅ | ✅ | ✅ |
| Retries, timeouts, cancel, per-queue and per-job concurrency limits | ✅ | ✅ | ✅ |
| Remote agents: one-line Linux installer, drain/revoke, credential rotation | ✅ | ✅ | ✅ |
| Container executor (Docker), artifacts, live logs | ✅ | ✅ | ✅ |
| Unlimited jobs, runs and agents | ✅ | ✅ | ✅ |
| **Access and security** | | | |
| Sign-in and personal API tokens (one admin account) | ✅ | ✅ | ✅ |
| Multiple users with viewer / operator / admin roles | — | ✅ | ✅ |
| Secrets manager: encrypted, injected at run time, masked in logs | — | ✅ | ✅ |
| Audit log of every change and sign-in | — | ✅ | ✅ |
| SSO (SAML / OIDC), SCIM provisioning | — | — | 🗺 |
| Per-project permissions, audit export to a SIEM | — | — | 🗺 |
| **Operations** | | | |
| Prometheus metrics, alert rules, backup and restore tooling | ✅ | ✅ | ✅ |
| Notifications: Slack, Teams, email, signed webhooks; agent-offline alerts | — | ✅ | ✅ |
| Managed control plane, upgrades and backups done for you | — | optional | optional |
| SLA, security review, custom terms (DPA) | — | — | ✅ |

## How the editions relate

- **Same core, same database.** Team and Enterprise are the Community Edition plus a
  proprietary extension package. Upgrading keeps all your jobs, runs and agents: add the
  license key, and the paid features appear.
- **License keys** are signed and checked offline. Nothing phones home. A key states the
  plan, the number of users and an end date. After it expires, paid features keep working for
  14 days, then switch off without deleting data; renewing turns them back on.
- **What stays free:** everything you need to orchestrate jobs reliably on any number of
  machines. We won't move existing Community features behind a paywall.
- **Roadmap items** are labeled as such. We don't sell features that don't exist yet;
  Enterprise customers get them built and prioritised with them.

## How to upgrade

1. **Buy a plan:** email [anshulshrm12@gmail.com](mailto:anshulshrm12@gmail.com) with your
   company, how many users and agents you expect, and whether you want it self-hosted or
   managed (or use **Plans → Contact sales** in the Torqrun UI). You receive a **license key**
   and a **registry token** for the licensed images.
2. **Upgrade in place:** in the directory where you run Torqrun:

   ```bash
   make upgrade LICENSE=<license key> TOKEN=<registry token>
   ```

   This:
   - backs up the database and artifacts (`backups/`);
   - saves the license in `.env`;
   - downloads the licensed images **of the version you're running**, so the database
     schema is identical, and restarts on them;
   - checks that the paid features are active.

   It takes well under a minute (14 s in our drill). **Everything stays as it was**: jobs,
   runs and logs, schedules, workflows, artifacts, agents (no re-enrollment) and your admin
   account, which becomes the first administrator of the paid edition. If any step fails,
   the previous settings and images are restored automatically.
3. **Start using it:** Users, Secrets, Audit log and Notifications appear under *Admin*.
   Invite your team from **Users**.

To go back, for example at the end of a subscription, run `make downgrade`. All data is kept;
the paid features become inactive and come back if you upgrade again.

**Managed plan instead?** We restore your [backup](operations/backup-restore.md) into your
managed instance. Your agents keep running and only need the new server URL.
