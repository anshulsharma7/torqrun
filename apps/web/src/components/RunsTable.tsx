import { Link } from "react-router";

import type { Run } from "../lib/api";
import { formatDuration, formatRelative, shortId } from "../lib/format";
import { Badge, RunStatusBadge, Table } from "./ui";

export function RunsTable({ runs, showJob = true }: { runs: Run[]; showJob?: boolean }) {
  const head = ["Status", ...(showJob ? ["Job"] : []), "Run", "Exit", "Duration", "Trigger", "Queued"];
  return (
    <Table head={head}>
      {runs.map((run) => (
        <tr key={run.id} className="group transition hover:bg-surface-2/50">
          <td className="px-5 py-3 whitespace-nowrap">
            <RunStatusBadge status={run.status} />
            {run.current_attempt > 1 && (
              <span className="ml-1.5 text-[11px] text-fg-subtle tabular-nums" title="attempt">
                {run.current_attempt}/{run.max_attempts}
              </span>
            )}
          </td>
          {showJob && (
            <td className="px-5 py-3 whitespace-nowrap">
              <Link to={`/jobs/${run.job_id}`} className="font-medium hover:text-brand">
                {run.job_name}
              </Link>
              <span className="ml-1.5 text-xs text-fg-subtle">v{run.job_version}</span>
            </td>
          )}
          <td className="px-5 py-3">
            <Link to={`/runs/${run.id}`} className="font-mono text-xs text-fg-muted hover:text-brand">
              #{shortId(run.id)}
            </Link>
          </td>
          <td className="px-5 py-3 font-mono text-xs tabular-nums">
            {run.exit_code === null ? <span className="text-fg-subtle">—</span> : run.exit_code}
          </td>
          <td className="px-5 py-3 tabular-nums text-fg-muted">{formatDuration(run.duration_seconds)}</td>
          <td className="px-5 py-3">
            <Badge>{run.rerun_of ? "rerun" : run.trigger}</Badge>
          </td>
          <td className="px-5 py-3 text-xs whitespace-nowrap text-fg-muted" title={run.queued_at}>
            {formatRelative(run.queued_at)}
          </td>
        </tr>
      ))}
    </Table>
  );
}
