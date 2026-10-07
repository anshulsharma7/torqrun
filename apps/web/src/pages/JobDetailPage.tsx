import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, FileCode2, Pencil, Play, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";

import { RunDots } from "../components/RunHistory";
import { RunsTable } from "../components/RunsTable";
import { ScheduleDialog } from "../components/ScheduleDialog";
import { ScheduleTable } from "./SchedulesPage";
import { Badge, Button, ButtonLink, Card, CodeBlock, EmptyState, ErrorState, Loading, PageHeader, Pager, Tabs } from "../components/ui";
import { Can } from "../lib/auth";
import { api, TERMINAL_STATUSES } from "../lib/api";
import { formatDuration, formatTimestamp } from "../lib/format";

const LIMIT = 25;
type Tab = "runs" | "schedules" | "script" | "config";

export function JobDetailPage() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("runs");
  const [offset, setOffset] = useState(0);
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api.getJob(jobId), refetchInterval: 4000 });
  const runs = useQuery({
    queryKey: ["runs", { jobId, offset }],
    queryFn: () => api.listRuns({ job_id: jobId, limit: LIMIT, offset }),
    refetchInterval: 3000,
  });
  const [scheduling, setScheduling] = useState(false);
  const schedules = useQuery({ queryKey: ["schedules", jobId], queryFn: () => api.listSchedules({ job_id: jobId }), refetchInterval: 5000 });
  const trigger = useMutation({
    mutationFn: () => api.triggerRun(jobId),
    onSuccess: async (run) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${run.id}`);
    },
  });

  if (job.isPending) return <Card><Loading /></Card>;
  if (job.isError) return <Card><ErrorState error={job.error} /></Card>;
  const { spec } = job.data;
  const recent = job.data.recent_runs;
  const finished = recent.filter((r) => TERMINAL_STATUSES.has(r.status));
  const rate = finished.length ? Math.round((finished.filter((r) => r.status === "SUCCEEDED").length / finished.length) * 100) : null;
  const durations = (runs.data?.items ?? []).map((r) => r.duration_seconds).filter((d): d is number => d !== null);
  const avg = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

  return (
    <>
      <PageHeader
        crumbs={[{ to: "/jobs", label: "Jobs" }]}
        icon={spec.runtime === "python" ? FileCode2 : TerminalSquare}
        title={job.data.name}
        subtitle={job.data.description || `${spec.runtime === "python" ? "Python 3" : "Bash"} job`}
        actions={
          <Can role="operator">
            <ButtonLink to={`/jobs/${jobId}/edit`} icon={Pencil}>
              Edit
            </ButtonLink>
            <Button variant="primary" icon={Play} onClick={() => trigger.mutate()} disabled={trigger.isPending}>
              {trigger.isPending ? "Starting…" : "Run now"}
            </Button>
          </Can>
        }
      />
      {trigger.isError && <ErrorState error={trigger.error} />}
      {scheduling && <ScheduleDialog open onClose={() => setScheduling(false)} jobId={jobId} />}

      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="card p-4">
          <p className="text-xs text-fg-muted">Recent runs</p>
          <div className="mt-2 flex h-8 items-center"><RunDots runs={recent} /></div>
        </div>
        <div className="card p-4">
          <p className="text-xs text-fg-muted">Success rate</p>
          <p className="mt-2 flex h-8 items-center text-2xl font-semibold tabular-nums">{rate === null ? "—" : `${rate}%`}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-fg-muted">Avg duration</p>
          <p className="mt-2 flex h-8 items-center text-2xl font-semibold tabular-nums">{formatDuration(avg)}</p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-fg-muted">Version</p>
          <p className="mt-2 flex h-8 items-center text-2xl font-semibold tabular-nums">v{job.data.current_version}</p>
        </div>
      </div>

      <Card>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "runs", label: "Runs", count: runs.data?.total },
            { value: "schedules", label: "Schedules", count: schedules.data?.length },
            { value: "script", label: "Script" },
            { value: "config", label: "Configuration" },
          ]}
        />
        {tab === "runs" &&
          (runs.isPending ? (
            <Loading />
          ) : runs.isError ? (
            <ErrorState error={runs.error} />
          ) : runs.data.items.length === 0 ? (
            <EmptyState icon={Play} title="This job hasn't run yet" action={<Button variant="primary" icon={Play} onClick={() => trigger.mutate()}>Run now</Button>} />
          ) : (
            <>
              <RunsTable runs={runs.data.items} showJob={false} />
              <Pager total={runs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          ))}
        {tab === "schedules" && (
          <>
            {schedules.data && schedules.data.length > 0 && <ScheduleTable schedules={schedules.data} showJob={false} />}
            <div className="p-5">
              <Button icon={CalendarClock} onClick={() => setScheduling(true)}>Add schedule</Button>
            </div>
          </>
        )}
        {tab === "script" && (
          <div className="p-5">
            <CodeBlock code={spec.script} label={spec.runtime === "python" ? "main.py" : "main.sh"} />
          </div>
        )}
        {tab === "config" && (
          <dl className="grid grid-cols-1 gap-x-8 px-5 py-2 md:grid-cols-2">
            {[
              ["Runtime", spec.runtime === "python" ? "Python 3" : "Bash"],
              [
                "Runs in",
                spec.executor === "docker" && spec.container ? (
                  <span key="c" className="font-mono text-xs">
                    container {spec.container.image}
                    {spec.container.network === "none" ? " · no network" : ""}
                    {spec.container.memory_mb ? ` · ${spec.container.memory_mb} MB` : ""}
                    {spec.container.cpus ? ` · ${spec.container.cpus} CPU` : ""}
                  </span>
                ) : (
                  "process on the agent host"
                ),
              ],
              ["Timeout", `${spec.timeout_seconds}s`],
              ["Queue", <Badge key="q">{spec.queue}</Badge>],
              ["Priority", String(spec.priority)],
              ["Attempts", spec.retry?.max_attempts > 1 ? `${spec.retry.max_attempts} (first retry after ${spec.retry.backoff_seconds}s, doubling${spec.retry.retry_on_timeout ? "; also on timeout" : ""})` : "1 (no retries)"],
              ["If agent dies mid-run", spec.interrupt_policy === "retry" ? "retry" : "mark lost"],
              ["Max concurrent", spec.max_concurrent ? String(spec.max_concurrent) : "unlimited"],
              ["Arguments", spec.args.length ? <span className="font-mono text-xs">{spec.args.join(" ")}</span> : "—"],
              ["Environment", Object.keys(spec.env).length ? <span className="font-mono text-xs">{Object.keys(spec.env).join(", ")}</span> : "—"],
              [
                "Secrets",
                Object.keys(spec.secrets ?? {}).length ? (
                  <span key="s" className="font-mono text-xs">
                    {Object.entries(spec.secrets).map(([k, v]) => `${k} ← ${v}`).join(", ")}
                  </span>
                ) : (
                  "—"
                ),
              ],
              ["Created", formatTimestamp(job.data.created_at)],
              ["Updated", formatTimestamp(job.data.updated_at)],
            ].map(([k, v]) => (
              <div key={k as string} className="flex items-center justify-between gap-4 border-b border-line py-3 text-sm">
                <dt className="text-fg-muted">{k}</dt>
                <dd className="truncate text-right">{v}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </>
  );
}
