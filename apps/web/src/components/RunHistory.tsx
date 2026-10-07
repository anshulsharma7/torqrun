import { Link } from "react-router";

import type { Run, RunBrief, RunStatus } from "../lib/api";
import { formatRelative } from "../lib/format";
import { cn, RUN_LABEL, RUN_TONE, toneDot } from "./ui";

/** GitHub-Actions-style strip of recent run outcomes, oldest → newest. */
export function RunDots({ runs, slots = 12 }: { runs: RunBrief[]; slots?: number }) {
  const ordered = [...runs].reverse();
  const empty = Math.max(0, slots - ordered.length);
  return (
    <div className="flex items-center gap-[3px]" aria-label={`Last ${runs.length} runs`}>
      {Array.from({ length: empty }, (_, i) => (
        <span key={`e${i}`} className="h-4 w-1.5 rounded-sm bg-surface-3" />
      ))}
      {ordered.map((r) => (
        <Link
          key={r.id}
          to={`/runs/${r.id}`}
          title={`${RUN_LABEL[r.status]} · ${formatRelative(r.created_at)}`}
          className={cn("h-4 w-1.5 rounded-sm transition hover:scale-y-125", toneDot[RUN_TONE[r.status]])}
        />
      ))}
    </div>
  );
}

const BUCKET_STATUS: { key: string; statuses: RunStatus[]; className: string }[] = [
  { key: "ok", statuses: ["SUCCEEDED"], className: "bg-ok" },
  { key: "bad", statuses: ["FAILED", "TIMED_OUT", "LOST"], className: "bg-bad" },
  { key: "other", statuses: [], className: "bg-info" },
];

/** Stacked bars of runs per hour for the last `hours` hours. */
export function ActivityChart({ runs, hours = 24, now = Date.now() }: { runs: Run[]; hours?: number; now?: number }) {
  const hourMs = 3600_000;
  const end = Math.ceil(now / hourMs) * hourMs;
  const buckets = Array.from({ length: hours }, () => ({ ok: 0, bad: 0, other: 0 }));
  for (const r of runs) {
    const idx = hours - 1 - Math.floor((end - new Date(r.queued_at).getTime()) / hourMs);
    if (idx < 0 || idx >= hours) continue;
    const b = buckets[idx]!;
    if (r.status === "SUCCEEDED") b.ok++;
    else if (r.status === "FAILED" || r.status === "TIMED_OUT" || r.status === "LOST") b.bad++;
    else b.other++;
  }
  const max = Math.max(1, ...buckets.map((b) => b.ok + b.bad + b.other));
  return (
    <div>
      <div className="flex h-28 items-end gap-1" role="img" aria-label={`Runs per hour, last ${hours} hours`}>
        {buckets.map((b, i) => {
          const total = b.ok + b.bad + b.other;
          return (
            <div key={i} className="group relative flex h-full flex-1 flex-col justify-end" title={`${total} run${total === 1 ? "" : "s"}`}>
              <div className="flex flex-col-reverse overflow-hidden rounded-[3px]" style={{ height: `${(total / max) * 100}%` }}>
                {BUCKET_STATUS.map((s) =>
                  b[s.key as keyof typeof b] ? (
                    <div key={s.key} className={s.className} style={{ flexGrow: b[s.key as keyof typeof b] }} />
                  ) : null,
                )}
              </div>
              {total === 0 && <div className="h-[3px] rounded-full bg-surface-3" />}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-fg-subtle">
        <span>{hours}h ago</span>
        <span>now</span>
      </div>
    </div>
  );
}
