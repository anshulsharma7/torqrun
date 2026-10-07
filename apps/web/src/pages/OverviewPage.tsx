import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, Boxes, CheckCircle2, Plus, Rocket, Server, Terminal, Timer, XCircle, type LucideIcon } from "lucide-react";
import { Link } from "react-router";

import { AgentCard } from "../components/AgentCard";
import { ActivityChart } from "../components/RunHistory";
import { RunsTable } from "../components/RunsTable";
import { SystemStatus } from "../components/SystemStatus";
import { ButtonLink, Card, cn, EmptyState, ErrorState, Loading, PageHeader } from "../components/ui";
import { api, TERMINAL_STATUSES } from "../lib/api";
import { formatDuration } from "../lib/format";

function Stat({ label, value, icon: Icon, tone, sub, to }: { label: string; value: string | number | undefined; icon: LucideIcon; tone: string; sub?: string; to: string }) {
  return (
    <Link to={to} className="card group block p-4 transition hover:-translate-y-px hover:ring-1 hover:ring-line-strong">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-fg-muted">{label}</p>
        <div className={cn("grid size-7 place-items-center rounded-lg", tone)}>
          <Icon className="size-3.5" aria-hidden="true" />
        </div>
      </div>
      <p className="mt-3 text-[28px] leading-none font-semibold tracking-tight tabular-nums">{value ?? "—"}</p>
      {sub && <p className="mt-2 text-xs text-fg-subtle">{sub}</p>}
    </Link>
  );
}

function GettingStarted({ hasAgent, hasJob, hasRun }: { hasAgent: boolean; hasJob: boolean; hasRun: boolean }) {
  const steps = [
    { done: hasAgent, title: "Connect an agent", body: "A machine that runs your jobs. One is bundled with Docker Compose.", to: "/fleet?connect=1", icon: Terminal },
    { done: hasJob, title: "Create a job", body: "Paste a Python or Bash script and choose a timeout.", to: "/jobs/new", icon: Plus },
    { done: hasRun, title: "Run it", body: "Press Run now and watch the output stream in live.", to: "/jobs", icon: Rocket },
  ];
  return (
    <Card className="mb-6 overflow-hidden" bodyClassName="relative">
      <div className="pointer-events-none absolute -top-24 right-0 size-72 rounded-full bg-brand/10 blur-3xl" />
      <div className="relative p-6">
        <p className="text-sm font-medium text-gradient">Welcome to Torqrun</p>
        <h2 className="mt-1 text-lg font-semibold tracking-tight">Run your first job in three steps</h2>
        <ol className="mt-5 grid gap-3 md:grid-cols-3">
          {steps.map((s, i) => (
            <li key={s.title}>
              <Link to={s.to} className="flex h-full gap-3 rounded-xl bg-surface-2/70 p-4 ring-1 ring-inset ring-line transition hover:ring-line-strong">
                <span className={cn("grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold", s.done ? "bg-ok/15 text-ok" : "bg-surface-3 text-fg-muted")}>
                  {s.done ? <CheckCircle2 className="size-4" /> : i + 1}
                </span>
                <span>
                  <span className={cn("block text-sm font-medium", s.done && "text-fg-muted line-through")}>{s.title}</span>
                  <span className="mt-0.5 block text-xs text-fg-muted">{s.body}</span>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      </div>
    </Card>
  );
}

export function OverviewPage() {
  const jobs = useQuery({ queryKey: ["jobs", "count"], queryFn: () => api.listJobs({ limit: 1 }), select: (p) => p.total, refetchInterval: 10000 });
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.listAgents, refetchInterval: 5000 });
  const recent = useQuery({ queryKey: ["runs", "recent", 200], queryFn: () => api.listRuns({ limit: 200 }), refetchInterval: 3000 });

  const runs = recent.data?.items ?? [];
  const finished = runs.filter((r) => TERMINAL_STATUSES.has(r.status));
  const succeeded = finished.filter((r) => r.status === "SUCCEEDED").length;
  const rate = finished.length ? Math.round((succeeded / finished.length) * 100) : null;
  const active = runs.filter((r) => !TERMINAL_STATUSES.has(r.status)).length;
  const durations = finished.map((r) => r.duration_seconds).filter((d): d is number => d !== null).sort((a, b) => a - b);
  const p50 = durations.length ? durations[Math.floor(durations.length / 2)]! : null;
  const p95 = durations.length ? durations[Math.min(durations.length - 1, Math.floor(durations.length * 0.95))]! : null;
  const online = agents.data?.filter((a) => a.connected).length;
  const showOnboarding = jobs.data === 0 || (agents.data && agents.data.length === 0) || (recent.data && recent.data.total === 0);

  return (
    <>
      <PageHeader title="Overview" subtitle="What's running across your fleet right now." />
      {showOnboarding && (
        <GettingStarted hasAgent={!!agents.data?.length} hasJob={!!jobs.data} hasRun={!!recent.data?.total} />
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Jobs" value={jobs.data} icon={Boxes} tone="bg-surface-3 text-fg-muted" to="/jobs" />
        <Stat label="Active runs" value={active} icon={Activity} tone="bg-info/12 text-info" sub="queued or running" to="/runs" />
        <Stat label="Success rate" value={rate === null ? "—" : `${rate}%`} icon={CheckCircle2} tone="bg-ok/12 text-ok" sub={`${succeeded} of ${finished.length} recent runs`} to="/runs" />
        <Stat label="Failed" value={finished.length - succeeded} icon={XCircle} tone="bg-bad/12 text-bad" sub="recent runs" to="/runs" />
        <Stat label="Duration p50 / p95" value={formatDuration(p50)} icon={Timer} tone="bg-warn/12 text-warn" sub={`p95 ${formatDuration(p95)}`} to="/runs" />
      </div>

      {/* One grid (not two stacks) so each row's boxes share top and bottom edges. */}
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_380px]">
          <Card title="Activity" icon={Activity} actions={<span className="text-xs text-fg-subtle">runs per hour · last 24h</span>} bodyClassName="flex flex-col justify-center p-5">
            <ActivityChart runs={runs} />
          </Card>
          <Card
            title="Fleet"
            icon={Server}
            actions={
              <Link to="/fleet" className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg">
                {online ?? 0} online <ArrowRight className="size-3" />
              </Link>
            }
            bodyClassName="space-y-3 p-3"
          >
            {agents.isPending ? (
              <Loading rows={2} />
            ) : agents.data && agents.data.length > 0 ? (
              agents.data.slice(0, 3).map((a) => <AgentCard key={a.id} agent={a} compact />)
            ) : (
              <EmptyState icon={Server} title="No agents connected" action={<ButtonLink to="/fleet?connect=1" icon={Terminal}>Connect an agent</ButtonLink>} />
            )}
          </Card>
          <Card
            title="Recent runs"
            actions={
              <Link to="/runs" className="flex items-center gap-1 text-xs text-fg-muted hover:text-fg">
                View all <ArrowRight className="size-3" />
              </Link>
            }
          >
            {recent.isPending ? (
              <Loading />
            ) : recent.isError ? (
              <ErrorState error={recent.error} />
            ) : runs.length === 0 ? (
              <EmptyState icon={Rocket} title="Nothing has run yet" action={<ButtonLink to="/jobs/new" variant="primary" icon={Plus}>Create a job</ButtonLink>}>
                Create a job and press Run now.
              </EmptyState>
            ) : (
              <RunsTable runs={runs.slice(0, 8)} />
            )}
          </Card>
          <SystemStatus />
      </div>
    </>
  );
}
