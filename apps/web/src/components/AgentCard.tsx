import { Cpu, Monitor, Server } from "lucide-react";
import { Link } from "react-router";

import type { Agent } from "../lib/api";
import { formatBytes, formatRelative, formatUptime } from "../lib/format";
import { Badge, cn, Dot, Meter, StatusPill } from "./ui";

export function agentState(agent: Agent): { tone: "ok" | "warn" | "bad" | "info" | "neutral"; label: string } {
  if (agent.status === "REVOKED") return { tone: "bad", label: "Revoked" };
  if (agent.status === "DRAINING") return { tone: "warn", label: "Draining" };
  if (!agent.connected) return { tone: "neutral", label: "Offline" };
  if (agent.running >= agent.max_slots) return { tone: "info", label: "Busy" };
  return { tone: "ok", label: "Online" };
}

export function AgentStateBadge({ agent }: { agent: Agent }) {
  const s = agentState(agent);
  return <StatusPill tone={s.tone} label={s.label} pulse={s.tone === "info"} />;
}

export function AgentCard({ agent, compact = false }: { agent: Agent; compact?: boolean }) {
  const s = agent.system;
  const memUsed = s?.mem_total_bytes && s.mem_available_bytes !== undefined ? 1 - s.mem_available_bytes / s.mem_total_bytes : null;
  const diskUsed = s?.disk_total_bytes && s.disk_free_bytes !== undefined ? 1 - s.disk_free_bytes / s.disk_total_bytes : null;
  const cpuLoad = s?.load_1m !== undefined && s?.cpu_count ? s.load_1m / s.cpu_count : null;
  const state = agentState(agent);
  const OsIcon = agent.os === "linux" ? Server : Monitor;

  return (
    <Link
      to={`/fleet/${agent.id}`}
      className={cn(
        "card group block p-4 transition hover:-translate-y-px hover:ring-1 hover:ring-line-strong",
        !agent.connected && "opacity-70",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="relative grid size-10 shrink-0 place-items-center rounded-xl bg-surface-2 ring-1 ring-line-strong">
            <OsIcon className="size-5 text-fg-muted" aria-hidden="true" />
            <Dot tone={state.tone} pulse={agent.connected} className="absolute -right-0.5 -bottom-0.5 ring-2 ring-surface rounded-full" />
          </div>
          <div className="min-w-0">
            <p className="truncate font-medium">{agent.name}</p>
            <p className="truncate text-xs text-fg-muted">{s?.os_pretty ?? `${agent.os}/${agent.arch}`}</p>
          </div>
        </div>
        <span className="shrink-0">
          <AgentStateBadge agent={agent} />
        </span>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        <Meter
          label="Slots"
          value={agent.running / agent.max_slots}
          detail={`${agent.running}/${agent.max_slots}`}
        />
        <Meter label="CPU load" value={cpuLoad} detail={cpuLoad === null ? "—" : `${s!.load_1m!.toFixed(2)} / ${s!.cpu_count}`} />
        {!compact && (
          <>
            <Meter label="Memory" value={memUsed} detail={memUsed === null ? "—" : `${formatBytes(s!.mem_total_bytes! - s!.mem_available_bytes!)} / ${formatBytes(s!.mem_total_bytes!)}`} />
            <Meter label="Disk" value={diskUsed} detail={diskUsed === null ? "—" : `${formatBytes(s!.disk_free_bytes!)} free`} />
          </>
        )}
      </div>

      {agent.active_runs.length > 0 && (
        <div className="mt-4 space-y-1.5 border-t border-line pt-3">
          {agent.active_runs.slice(0, 3).map((r) => (
            <div key={r.run_id} className="flex items-center gap-2 text-xs">
              <Dot tone="info" pulse className="size-1.5 [&>span]:size-1.5" />
              <span className="truncate font-mono text-fg">{r.job_name}</span>
              <span className="ml-auto text-fg-subtle">{formatRelative(r.started_at)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-1.5 text-[11px] text-fg-subtle">
        <Cpu className="size-3" aria-hidden="true" />
        <span>{s?.cpu_count ?? "?"} cores</span>
        <span>·</span>
        <span>up {formatUptime(s?.uptime_seconds)}</span>
        <span>·</span>
        <span>seen {formatRelative(agent.last_seen_at)}</span>
        <span className="ml-auto flex gap-1">
          {agent.capabilities?.includes("docker") && (
            <Badge className="text-info">docker</Badge>
          )}
          {agent.queues.map((q) => (
            <Badge key={q}>{q}</Badge>
          ))}
        </span>
      </div>
    </Link>
  );
}
