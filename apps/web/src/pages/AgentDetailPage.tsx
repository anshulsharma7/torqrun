import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Ban, Info, Monitor, PauseCircle, PlayCircle, Server } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router";

import { AgentStateBadge } from "../components/AgentCard";
import { RunsTable } from "../components/RunsTable";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, Meter, PageHeader, Pager } from "../components/ui";
import { Can } from "../lib/auth";
import { api } from "../lib/api";
import { formatBytes, formatRelative, formatTimestamp, formatUptime } from "../lib/format";

const LIMIT = 25;

export function AgentDetailPage() {
  const { agentId = "" } = useParams();
  const [offset, setOffset] = useState(0);
  const queryClient = useQueryClient();
  const action = useMutation({
    mutationFn: (a: "drain" | "resume" | "revoke") => api.agentAction(agentId, a),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["agent", agentId] });
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
  const agent = useQuery({ queryKey: ["agent", agentId], queryFn: () => api.getAgent(agentId), refetchInterval: 4000 });
  const runs = useQuery({
    queryKey: ["runs", { agentId, offset }],
    queryFn: () => api.listRuns({ agent_id: agentId, limit: LIMIT, offset }),
    refetchInterval: 4000,
  });

  if (agent.isPending) return <Card><Loading /></Card>;
  if (agent.isError) return <Card><ErrorState error={agent.error} /></Card>;
  const a = agent.data;
  const s = a.system;
  const mem = s?.mem_total_bytes && s.mem_available_bytes !== undefined ? 1 - s.mem_available_bytes / s.mem_total_bytes : null;
  const disk = s?.disk_total_bytes && s.disk_free_bytes !== undefined ? 1 - s.disk_free_bytes / s.disk_total_bytes : null;
  const cpu = s?.load_1m !== undefined && s?.cpu_count ? s.load_1m / s.cpu_count : null;

  const facts: [string, React.ReactNode][] = [
    ["Hostname", <span className="font-mono text-xs">{a.hostname}</span>],
    ["Operating system", s?.os_pretty ?? a.os],
    ["Kernel / arch", <span className="font-mono text-xs">{s?.kernel ?? "—"} · {a.arch}</span>],
    ["Agent version", <span className="font-mono text-xs">{a.agent_version}</span>],
    ["Python", <span className="font-mono text-xs">{s?.python_version ?? "—"}</span>],
    ["Queues", <span className="flex justify-end gap-1">{a.queues.map((q) => <Badge key={q}>{q}</Badge>)}</span>],
    ["Uptime", formatUptime(s?.uptime_seconds)],
    ["Last heartbeat", formatRelative(a.last_seen_at)],
    ["Enrolled", formatTimestamp(a.created_at)],
  ];

  return (
    <>
      <PageHeader
        crumbs={[{ to: "/fleet", label: "Fleet" }]}
        icon={a.os === "linux" ? Server : Monitor}
        title={<span className="flex items-center gap-3">{a.name} <AgentStateBadge agent={a} /></span>}
        subtitle={
          a.status === "REVOKED"
            ? "Revoked: its credentials no longer work. Re-enroll the machine with a new token to bring it back."
            : a.status === "DRAINING"
              ? `Draining: no new jobs are sent; ${a.running} still running`
              : `${a.running} of ${a.max_slots} job slots in use`
        }
        actions={
          a.status !== "REVOKED" && (
            <Can role="admin">
              {a.status === "DRAINING" ? (
                <Button icon={PlayCircle} onClick={() => action.mutate("resume")} disabled={action.isPending}>
                  Resume
                </Button>
              ) : (
                <Button icon={PauseCircle} onClick={() => action.mutate("drain")} disabled={action.isPending}>
                  Drain
                </Button>
              )}
              <Button
                icon={Ban}
                onClick={() => {
                  if (window.confirm(`Revoke ${a.name}? Its credentials stop working immediately and running jobs are marked lost.`)) {
                    action.mutate("revoke");
                  }
                }}
                disabled={action.isPending}
              >
                Revoke
              </Button>
            </Can>
          )
        }
      />
      {action.isError && <ErrorState error={action.error} />}
      <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { label: "Job slots", value: a.running / a.max_slots, detail: `${a.running}/${a.max_slots}` },
          { label: "CPU load (1m)", value: cpu, detail: cpu === null ? "—" : `${s!.load_1m!.toFixed(2)} on ${s!.cpu_count} cores` },
          { label: "Memory", value: mem, detail: mem === null ? "—" : `${formatBytes(s!.mem_total_bytes! - s!.mem_available_bytes!)} of ${formatBytes(s!.mem_total_bytes!)}` },
          { label: "Disk", value: disk, detail: disk === null ? "—" : `${formatBytes(s!.disk_free_bytes!)} free` },
        ].map((m) => (
          <div key={m.label} className="card p-4">
            <Meter label={m.label} value={m.value} detail={m.detail} />
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_340px]">
        <Card title="Runs on this agent" icon={Activity}>
          {runs.isPending ? (
            <Loading />
          ) : runs.isError ? (
            <ErrorState error={runs.error} />
          ) : runs.data.items.length === 0 ? (
            <EmptyState icon={Activity} title="No runs yet">Jobs in queue {a.queues.join(", ")} will run here.</EmptyState>
          ) : (
            <>
              <RunsTable runs={runs.data.items} />
              <Pager total={runs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Card>
        <Card title="Details" icon={Info}>
          <dl className="divide-y divide-line px-5">
            {facts.map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-4 py-2.5 text-sm">
                <dt className="text-fg-muted">{k}</dt>
                <dd className="min-w-0 truncate text-right">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
    </>
  );
}
