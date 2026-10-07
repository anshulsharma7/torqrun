import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Pause, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ScheduleDialog } from "../components/ScheduleDialog";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, RunStatusBadge, StatusPill, Table } from "../components/ui";
import { Can } from "../lib/auth";
import { api, type Schedule } from "../lib/api";
import { describeCron, describeInterval } from "../lib/cron";
import { formatRelative, formatTimestamp } from "../lib/format";

export function ScheduleTable({ schedules, showJob = true }: { schedules: Schedule[]; showJob?: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Schedule | undefined>();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["schedules"] });
  const toggle = useMutation({ mutationFn: (s: Schedule) => api.scheduleAction(s.id, s.enabled ? "pause" : "resume"), onSuccess: refresh });
  const remove = useMutation({ mutationFn: api.deleteSchedule, onSuccess: refresh });

  return (
    <>
      <Table head={["Schedule", ...(showJob ? ["Runs"] : []), "When", "Next run", "Last run", ""]}>
        {schedules.map((s) => (
          <tr key={s.id} className="transition hover:bg-surface-2/50">
            <td className="px-5 py-3">
              <p className="font-medium">{s.name}</p>
              {!s.enabled ? <StatusPill tone="warn" label="Paused" /> : s.skipped_count > 0 && (
                <p className="text-[11px] text-fg-subtle" title={s.last_skip_reason ?? ""}>{s.skipped_count} skipped</p>
              )}
            </td>
            {showJob && (
              <td className="px-5 py-3 whitespace-nowrap">
                <Link to={s.workflow_id ? `/workflows/${s.workflow_id}` : `/jobs/${s.job_id}`} className="hover:text-brand">
                  {s.job_name}
                </Link>
                {s.workflow_id && <span className="ml-1.5 text-[11px] text-fg-subtle">workflow</span>}
              </td>
            )}
            <td className="px-5 py-3">
              <p>{s.kind === "cron" ? describeCron(s.cron ?? "") : describeInterval(s.interval_seconds ?? 0)}</p>
              <p className="flex gap-1.5 text-[11px] text-fg-subtle">
                {s.kind === "cron" && <Badge>{s.cron}</Badge>}
                <span>{s.timezone}</span>
              </p>
            </td>
            <td className="px-5 py-3 whitespace-nowrap" title={s.next_fire_at ? formatTimestamp(s.next_fire_at) : ""}>
              {s.next_fire_at ? (
                <>
                  <p className="tabular-nums">{formatRelative(s.next_fire_at)}</p>
                  <p className="text-[11px] text-fg-subtle tabular-nums">{formatTimestamp(s.next_fire_at)}</p>
                </>
              ) : (
                <span className="text-fg-subtle">—</span>
              )}
            </td>
            <td className="px-5 py-3 whitespace-nowrap">
              {s.last_run_id && s.last_run_status ? (
                <Link to={`/runs/${s.last_run_id}`} className="flex items-center gap-2">
                  <RunStatusBadge status={s.last_run_status} />
                  <span className="text-xs text-fg-subtle">{formatRelative(s.last_fired_at)}</span>
                </Link>
              ) : (
                <span className="text-xs text-fg-subtle">never</span>
              )}
            </td>
            <td className="px-5 py-3">
              <div className="flex justify-end gap-1">
                <Button size="sm" icon={s.enabled ? Pause : Play} onClick={() => toggle.mutate(s)} aria-label={s.enabled ? `Pause ${s.name}` : `Resume ${s.name}`}>
                  {s.enabled ? "Pause" : "Resume"}
                </Button>
                <Button size="sm" variant="ghost" icon={Pencil} onClick={() => setEditing(s)} aria-label={`Edit ${s.name}`} />
                <Button
                  size="sm"
                  variant="ghost"
                  icon={Trash2}
                  aria-label={`Delete ${s.name}`}
                  onClick={() => window.confirm(`Delete schedule ${s.name}? Past runs are kept.`) && remove.mutate(s.id)}
                />
              </div>
            </td>
          </tr>
        ))}
      </Table>
      {editing && (
        <ScheduleDialog open onClose={() => setEditing(undefined)} schedule={editing} jobId={editing.job_id ?? undefined} workflowId={editing.workflow_id ?? undefined} />
      )}
    </>
  );
}

export function SchedulesPage() {
  const [creating, setCreating] = useState(false);
  const schedules = useQuery({ queryKey: ["schedules"], queryFn: () => api.listSchedules(), refetchInterval: 5000 });
  return (
    <>
      <PageHeader
        icon={CalendarClock}
        title="Schedules"
        subtitle="Run jobs on a cron expression or a fixed interval, in any time zone."
        actions={<Can role="operator"><Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>New schedule</Button></Can>}
      />
      <Card>
        {schedules.isPending ? (
          <Loading />
        ) : schedules.isError ? (
          <ErrorState error={schedules.error} />
        ) : schedules.data.length === 0 ? (
          <EmptyState icon={CalendarClock} title="No schedules yet" action={<Can role="operator"><Button variant="primary" icon={Plus} onClick={() => setCreating(true)}>New schedule</Button></Can>}>
            Schedules start runs automatically, e.g. a backup every night at 02:00.
          </EmptyState>
        ) : (
          <ScheduleTable schedules={schedules.data} />
        )}
      </Card>
      {creating && <ScheduleDialog open onClose={() => setCreating(false)} />}
    </>
  );
}
