import { Check, Loader2, X } from "lucide-react";

import { TERMINAL_STATUSES, type RunDetail, type RunEvent, type RunStatus } from "../lib/api";
import { formatDuration } from "../lib/format";
import { cn } from "./ui";

const STEPS: { key: string; label: string; reached: RunStatus[] }[] = [
  { key: "queued", label: "Queued", reached: ["QUEUED"] },
  { key: "dispatched", label: "Picked up", reached: ["DISPATCHED", "STARTING"] },
  { key: "running", label: "Running", reached: ["RUNNING"] },
  { key: "done", label: "Finished", reached: ["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"] },
];

function firstAt(events: RunEvent[], statuses: RunStatus[]): number | null {
  const e = events.find((ev) => statuses.includes(ev.to_status));
  return e ? new Date(e.at).getTime() : null;
}

/**
 * Horizontal lifecycle of one attempt: queued → picked up → running → finished, with the time
 * spent in each. Retries are separate attempts, so only that attempt's events count.
 */
export function RunStepper({ run, attemptNo, now }: { run: RunDetail; attemptNo: number; now: number }) {
  const events = run.events.filter((e) => e.attempt_no === attemptNo && e.to_status !== "RETRY_WAIT");
  const attempt = run.attempts.find((a) => a.attempt_no === attemptNo);
  const status = attempt?.status ?? run.status;
  const times = STEPS.map((s) => firstAt(events, s.reached));
  const finished = TERMINAL_STATUSES.has(status);
  const failed = finished && status !== "SUCCEEDED";
  const finalLabel = { FAILED: "Failed", TIMED_OUT: "Timed out", CANCELLED: "Cancelled", LOST: "Lost" }[status as string] ?? "Failed";
  const current = finished ? STEPS.length - 1 : times.reduce<number>((acc, t, i) => (t !== null ? i : acc), 0);

  return (
    <ol className="grid grid-cols-4 gap-2" aria-label="Run progress">
      {STEPS.map((step, i) => {
        const reached = times[i] !== null;
        const next = times[i + 1] ?? (i === current && !finished ? now : null);
        const spent = reached && next !== null && i < STEPS.length - 1 ? (next - times[i]!) / 1000 : null;
        const isCurrent = i === current && !finished;
        const isLast = i === STEPS.length - 1;
        return (
          <li key={step.key} className="min-w-0">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "grid size-6 shrink-0 place-items-center rounded-full text-[11px] ring-1 ring-inset",
                  isLast && finished && failed && "bg-bad/15 text-bad ring-bad/40",
                  isLast && finished && !failed && "bg-ok/15 text-ok ring-ok/40",
                  !isLast && reached && "bg-ok/15 text-ok ring-ok/40",
                  isCurrent && "bg-info/15 text-info ring-info/40",
                  !reached && "bg-surface-2 text-fg-subtle ring-line-strong",
                )}
              >
                {isCurrent ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : isLast && failed ? (
                  <X className="size-3.5" />
                ) : reached ? (
                  <Check className="size-3.5" />
                ) : (
                  i + 1
                )}
              </span>
              {!isLast && (
                <span className={cn("h-px flex-1 rounded-full", times[i + 1] !== null ? "bg-ok/50" : "bg-line-strong")} />
              )}
            </div>
            <p className={cn("mt-2 text-xs font-medium", reached ? "text-fg" : "text-fg-subtle")}>
              {isLast && finished ? (failed ? finalLabel : "Succeeded") : step.label}
            </p>
            <p className="text-[11px] tabular-nums text-fg-subtle">{spent !== null ? formatDuration(spent) : " "}</p>
          </li>
        );
      })}
    </ol>
  );
}
