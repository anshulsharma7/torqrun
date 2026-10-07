# Notifications

> **Team and Enterprise feature.** Notifications are part of the paid plans
> ([plans](../editions.md)); the Community Edition shows them as locked.

Admins set up channels under **Admin → Notifications**. Torqrun then reports:

| Event | When |
|---|---|
| `run.failed` | A run ended `FAILED`, `TIMED_OUT` or `LOST` after all its retries. A failed attempt that will be retried is not reported. |
| `run.succeeded` | A run succeeded. Off by default, because this can be noisy. |
| `workflow.failed` / `workflow.succeeded` | A workflow run finished. |
| `agent.offline` | An agent hasn't checked in for `TORQRUN_NOTIFY_AGENT_OFFLINE_AFTER_SECONDS` (default 120). Sent once per outage; it re-arms when the agent comes back. |

Run events can be limited to certain queues per channel, for example only `prod`.

## Channel types

**Slack.** Create a Slack app with *Incoming Webhooks*, add it to a channel, and paste the
`https://hooks.slack.com/services/…` URL.

**Microsoft Teams.** In the Teams channel, add the *Workflows* template "Post to a channel when
a webhook request is received" and paste its URL. Messages are Adaptive Cards.

**Email.** Enter one or more recipients. Configure SMTP on the scheduler:
`TORQRUN_SMTP_HOST`, `_PORT` (587), `_USERNAME`, `_PASSWORD`, `_FROM` and `_STARTTLS` (true).

**Webhook.** Torqrun POSTs JSON to your endpoint:

```json
{
  "id": "6f1c…",
  "event": "run.failed",
  "occurred_at": "2026-10-07T09:12:44.120Z",
  "data": {
    "run_id": "…", "job_id": "…", "job_name": "nightly-backup", "status": "FAILED",
    "attempts": 3, "exit_code": 2, "error_summary": "exited with code 2\n…",
    "queue": "default", "trigger": "schedule", "workflow_run_id": null, "finished_at": "…"
  }
}
```

The request carries these headers:
- `X-Torqrun-Event`
- `X-Torqrun-Delivery`: the delivery ID; use it to drop duplicates.
- `X-Torqrun-Signature: t=<unix time>,v1=<hex>`, where `v1` is HMAC-SHA256 over
  `"<t>." + raw body`, keyed with the channel's signing secret.

The secret is shown once, when the channel is created or the secret is rotated
(**New secret**). Verify it like this:

```python
import hashlib, hmac, time


def verify(secret: str, header: str, raw_body: bytes, tolerance: int = 300) -> bool:
    t, v1 = (p.split("=", 1)[1] for p in header.split(","))
    expected = hmac.new(secret.encode(), f"{t}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(v1, expected) and abs(time.time() - int(t)) <= tolerance
```

Respond with any 2xx status. Anything else counts as a failure.

## Delivery guarantees

- **Transactional outbox.** A notification is queued in the same database transaction as the
  change it reports. You're never told about a run that didn't really fail, and a failure that
  committed is never forgotten, even if the scheduler is down at that moment.
- **Retries.** The scheduler sends due notifications every second. Failures are retried with
  backoff (30 s, 1 min, 2 min…), up to 8 attempts over about an hour, and then marked `failed`.
  Failed deliveries can be retried from the channel's **Recent deliveries** list.
- **At least once.** A crash between sending and recording the result can repeat a message.
  Webhook receivers should deduplicate on `X-Torqrun-Delivery`.
- **Retention.** Delivery history is kept for `TORQRUN_NOTIFICATION_RETENTION_DAYS` (30).

## Security

- Destinations (webhook URLs, which are credentials themselves; signing secrets; recipients)
  are encrypted with `TORQRUN_SECRET_KEY`, like job secrets. The API shows only a masked hint.
  **The scheduler needs the same `TORQRUN_SECRET_KEY` as the API.**
- The scheduler refuses link-local and metadata addresses such as `169.254.169.254`. Private
  network addresses are allowed, because internal webhooks are a normal use. Only admins can
  create channels.
- Messages include the job name, status, exit code and error summary, never logs or secret
  values. Error summaries come from the agent, which has already masked secrets in them.
- Links in messages use `TORQRUN_PUBLIC_URL` (set it on the scheduler). Without it, messages
  have no links.
