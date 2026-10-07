import { Ban, Check, CircleDashed, Loader2, Recycle, SkipForward, X } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";

import type { TaskState, WorkflowTask, WorkflowTaskRun } from "../lib/api";
import { cn } from "./ui";

const NODE_W = 200;
const NODE_H = 58;
const GAP_X = 72;
const GAP_Y = 18;
const PAD = 12;

const stateStyle: Record<TaskState, { ring: string; icon: typeof Check; iconClass: string; label: string }> = {
  PENDING: { ring: "ring-line-strong", icon: CircleDashed, iconClass: "text-fg-subtle", label: "Waiting" },
  ACTIVE: { ring: "ring-info/60", icon: Loader2, iconClass: "text-info animate-spin", label: "Running" },
  SUCCEEDED: { ring: "ring-ok/50", icon: Check, iconClass: "text-ok", label: "Succeeded" },
  FAILED: { ring: "ring-bad/60", icon: X, iconClass: "text-bad", label: "Failed" },
  SKIPPED: { ring: "ring-line", icon: SkipForward, iconClass: "text-fg-subtle", label: "Skipped" },
};

const ruleLabel = { all_success: "", all_done: "always", one_failed: "on failure" } as const;

/**
 * Layered DAG drawing: one column per layer (longest path from a root), tasks stacked within it,
 * curved edges from each dependency to its dependants. Pass run state to colour the nodes.
 */
export function DagGraph({
  tasks,
  layers,
  label = "Workflow graph",
}: {
  tasks: (WorkflowTask | WorkflowTaskRun)[];
  layers: string[][];
  label?: string;
}) {
  const layout = useMemo(() => {
    const pos = new Map<string, { x: number; y: number }>();
    const tallest = Math.max(...layers.map((l) => l.length), 1);
    const height = tallest * NODE_H + (tallest - 1) * GAP_Y;
    layers.forEach((layer, col) => {
      const layerH = layer.length * NODE_H + (layer.length - 1) * GAP_Y;
      const top = (height - layerH) / 2;
      layer.forEach((key, row) => {
        pos.set(key, { x: PAD + col * (NODE_W + GAP_X), y: PAD + top + row * (NODE_H + GAP_Y) });
      });
    });
    return { pos, width: PAD * 2 + layers.length * NODE_W + Math.max(0, layers.length - 1) * GAP_X, height: PAD * 2 + height };
  }, [layers]);

  const byKey = new Map(tasks.map((t) => [t.key, t]));

  return (
    <div className="overflow-x-auto" role="img" aria-label={label}>
      <div className="relative" style={{ width: layout.width, height: layout.height }}>
        <svg className="absolute inset-0" width={layout.width} height={layout.height} aria-hidden="true">
          {tasks.flatMap((t) =>
            t.depends_on.map((dep) => {
              const a = layout.pos.get(dep);
              const b = layout.pos.get(t.key);
              if (!a || !b) return null;
              const x1 = a.x + NODE_W;
              const y1 = a.y + NODE_H / 2;
              const x2 = b.x;
              const y2 = b.y + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              const from = byKey.get(dep) as WorkflowTaskRun | undefined;
              const done = from?.state === "SUCCEEDED";
              return (
                <path
                  key={`${dep}->${t.key}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2 - 4} ${y2}`}
                  fill="none"
                  strokeWidth={1.5}
                  className={done ? "stroke-ok/60" : "stroke-line-strong"}
                  strokeDasharray={t.trigger_rule !== "all_success" ? "4 4" : undefined}
                  markerEnd="url(#arrow)"
                />
              );
            }),
          )}
          <defs>
            <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="fill-fg-subtle" />
            </marker>
          </defs>
        </svg>
        {tasks.map((t) => {
          const p = layout.pos.get(t.key);
          if (!p) return null;
          const run = "state" in t ? (t as WorkflowTaskRun) : null;
          const style = run ? stateStyle[run.state] : null;
          const Icon = run?.reused ? Recycle : run?.run_status === "CANCELLED" ? Ban : style?.icon;
          const body = (
            <div
              className={cn(
                "flex h-full items-center gap-2.5 rounded-xl bg-surface-2 px-3 ring-1 transition",
                style?.ring ?? "ring-line-strong",
                run?.state === "SKIPPED" && "opacity-55",
                run?.run_id && "hover:bg-surface-3",
              )}
            >
              {Icon && <Icon className={cn("size-4 shrink-0", style?.iconClass)} aria-hidden="true" />}
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{t.key}</p>
                <p className="truncate font-mono text-[11px] text-fg-subtle">
                  {t.job}
                  {ruleLabel[t.trigger_rule] && <span className="text-warn"> · {ruleLabel[t.trigger_rule]}</span>}
                </p>
              </div>
            </div>
          );
          return (
            <div
              key={t.key}
              className="absolute"
              style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
              title={run ? `${t.key}: ${run.reused ? "reused" : style?.label}${run.note ? ` — ${run.note}` : ""}` : t.key}
              data-task={t.key}
              data-state={run?.state}
            >
              {run?.run_id && !run.reused ? (
                <Link to={`/runs/${run.run_id}`} className="block h-full" aria-label={`${t.key}: ${style?.label}`}>
                  {body}
                </Link>
              ) : (
                body
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
