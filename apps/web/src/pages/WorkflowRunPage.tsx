import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Redo2, RotateCw, Workflow as WorkflowIcon } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";

import { DagGraph } from "../components/DagGraph";
import { Badge, Button, Card, ErrorState, Loading, PageHeader, RunStatusBadge, StatusPill, Table } from "../components/ui";
import { Can } from "../lib/auth";
import { WorkflowRunBadge } from "../components/WorkflowRunStatus";
import { api, type TaskState } from "../lib/api";
import { formatDuration, formatTimestamp } from "../lib/format";

const taskTone: Record<TaskState, "ok" | "bad" | "info" | "neutral"> = {
  PENDING: "neutral",
  ACTIVE: "info",
  SUCCEEDED: "ok",
  FAILED: "bad",
  SKIPPED: "neutral",
};

export function WorkflowRunPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const wr = useQuery({
    queryKey: ["workflow-run", runId],
    queryFn: () => api.getWorkflowRun(runId),
    refetchInterval: (q) => (q.state.data?.status === "RUNNING" ? 1500 : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["workflow-run", runId] });
  const cancel = useMutation({ mutationFn: () => api.cancelWorkflowRun(runId), onSuccess: refresh });
  const rerun = useMutation({ mutationFn: (mode: "failed" | "all") => api.rerunWorkflowRun(runId, mode), onSuccess: (r) => navigate(`/workflow-runs/${r.id}`) });

  if (wr.isPending) return <Card><Loading /></Card>;
  if (wr.isError) return <Card><ErrorState error={wr.error} /></Card>;
  const r = wr.data;
  const error = cancel.error ?? rerun.error;

  return (
    <>
      <PageHeader
        crumbs={[{ to: "/workflows", label: "Workflows" }, { to: `/workflows/${r.workflow_id}`, label: r.workflow_name }]}
        icon={WorkflowIcon}
        title={<span className="flex items-center gap-3">{r.workflow_name} <span className="font-mono text-base text-fg-subtle">#{r.id.slice(-8)}</span> <WorkflowRunBadge status={r.status} /></span>}
        subtitle={`v${r.version} · ${r.trigger}${r.rerun_of ? " (rerun)" : ""} · started ${formatTimestamp(r.started_at)}${r.duration_seconds !== null ? ` · took ${formatDuration(r.duration_seconds)}` : ""}`}
        actions={
          <Can role="operator">
            {r.status === "RUNNING" && <Button icon={Ban} onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel</Button>}
            {(r.status === "FAILED" || r.status === "CANCELLED") && (
              <Button icon={Redo2} onClick={() => rerun.mutate("failed")} disabled={rerun.isPending}>Rerun failed</Button>
            )}
            {r.status !== "RUNNING" && (
              <Button variant="primary" icon={RotateCw} onClick={() => rerun.mutate("all")} disabled={rerun.isPending}>Run again</Button>
            )}
          </Can>
        }
      />
      {error && <ErrorState error={error} />}
      <Card title="Graph" className="mb-6" bodyClassName="p-4">
        <DagGraph tasks={r.tasks} layers={r.layers} label="Workflow run graph" />
      </Card>
      <Card title="Tasks">
        <Table head={["Task", "Job", "State", "Run", "Duration", "Notes"]}>
          {r.tasks.map((t) => (
            <tr key={t.key}>
              <td className="px-5 py-3 font-medium">{t.key}</td>
              <td className="px-5 py-3"><Badge>{t.job}</Badge></td>
              <td className="px-5 py-3"><StatusPill tone={taskTone[t.state]} label={t.reused ? "Reused" : t.state.charAt(0) + t.state.slice(1).toLowerCase()} pulse={t.state === "ACTIVE"} /></td>
              <td className="px-5 py-3">
                {t.run_id && t.run_status ? (
                  <Link to={`/runs/${t.run_id}`} className="flex items-center gap-2">
                    <RunStatusBadge status={t.run_status} />
                    <span className="font-mono text-xs text-fg-subtle">#{t.run_id.slice(-8)}</span>
                  </Link>
                ) : (
                  <span className="text-xs text-fg-subtle">—</span>
                )}
              </td>
              <td className="px-5 py-3 tabular-nums text-fg-muted">{formatDuration(t.duration_seconds)}</td>
              <td className="px-5 py-3 text-xs text-fg-muted">{t.note ?? ""}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}
