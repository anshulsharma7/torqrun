import { useMutation, useQuery } from "@tanstack/react-query";
import { CalendarClock, Pencil, Play, Workflow as WorkflowIcon } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { DagGraph } from "../components/DagGraph";
import { ScheduleDialog } from "../components/ScheduleDialog";
import { Button, ButtonLink, Card, CodeBlock, EmptyState, ErrorState, Loading, PageHeader, Table, Tabs } from "../components/ui";
import { Can } from "../lib/auth";
import { WorkflowRunBadge } from "../components/WorkflowRunStatus";
import { api } from "../lib/api";
import { formatDuration, formatRelative } from "../lib/format";
import { ScheduleTable } from "./SchedulesPage";

type Tab = "runs" | "schedules" | "definition";

export function WorkflowDetailPage() {
  const { workflowId = "" } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("runs");
  const [scheduling, setScheduling] = useState(false);
  const wf = useQuery({ queryKey: ["workflow", workflowId], queryFn: () => api.getWorkflow(workflowId) });
  const runs = useQuery({ queryKey: ["workflow-runs", workflowId], queryFn: () => api.listWorkflowRuns({ workflow_id: workflowId }), refetchInterval: 3000 });
  const schedules = useQuery({ queryKey: ["schedules", "wf", workflowId], queryFn: () => api.listSchedules({ workflow_id: workflowId }), refetchInterval: 5000 });
  const run = useMutation({ mutationFn: () => api.runWorkflow(workflowId), onSuccess: (wr) => navigate(`/workflow-runs/${wr.id}`) });

  if (wf.isPending) return <Card><Loading /></Card>;
  if (wf.isError) return <Card><ErrorState error={wf.error} /></Card>;
  const w = wf.data;

  return (
    <>
      <PageHeader
        crumbs={[{ to: "/workflows", label: "Workflows" }]}
        icon={WorkflowIcon}
        title={w.name}
        subtitle={w.description || `${w.tasks.length} tasks · v${w.current_version}`}
        actions={
          <Can role="operator">
            <ButtonLink to={`/workflows/${w.id}/edit`} icon={Pencil}>Edit</ButtonLink>
            <Button variant="primary" icon={Play} onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? "Starting…" : "Run workflow"}
            </Button>
          </Can>
        }
      />
      {run.isError && <ErrorState error={run.error} />}
      <Card title="Graph" className="mb-6" bodyClassName="p-4">
        <DagGraph tasks={w.tasks} layers={w.layers} />
      </Card>
      <Card>
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "runs", label: "Runs", count: runs.data?.total },
            { value: "schedules", label: "Schedules", count: schedules.data?.length },
            { value: "definition", label: "Definition" },
          ]}
        />
        {tab === "runs" &&
          (runs.isPending ? (
            <Loading />
          ) : !runs.data?.items.length ? (
            <EmptyState icon={Play} title="Not run yet" action={<Button variant="primary" icon={Play} onClick={() => run.mutate()}>Run workflow</Button>} />
          ) : (
            <Table head={["Status", "Run", "Version", "Tasks", "Duration", "Trigger", "Started"]}>
              {runs.data.items.map((r) => (
                <tr key={r.id} className="transition hover:bg-surface-2/50">
                  <td className="px-5 py-3"><WorkflowRunBadge status={r.status} /></td>
                  <td className="px-5 py-3"><Link to={`/workflow-runs/${r.id}`} className="font-mono text-xs text-fg-muted hover:text-brand">#{r.id.slice(-8)}</Link></td>
                  <td className="px-5 py-3 font-mono text-xs text-fg-muted">v{r.version}</td>
                  <td className="px-5 py-3 text-xs tabular-nums text-fg-muted">
                    {r.counts.SUCCEEDED ?? 0} ok · {r.counts.FAILED ?? 0} failed · {r.counts.SKIPPED ?? 0} skipped
                  </td>
                  <td className="px-5 py-3 tabular-nums text-fg-muted">{formatDuration(r.duration_seconds)}</td>
                  <td className="px-5 py-3 text-xs text-fg-muted">{r.trigger}</td>
                  <td className="px-5 py-3 text-xs text-fg-muted">{formatRelative(r.started_at)}</td>
                </tr>
              ))}
            </Table>
          ))}
        {tab === "schedules" && (
          <>
            {schedules.data && schedules.data.length > 0 && <ScheduleTable schedules={schedules.data} showJob={false} />}
            <div className="p-5"><Button icon={CalendarClock} onClick={() => setScheduling(true)}>Add schedule</Button></div>
          </>
        )}
        {tab === "definition" && <div className="p-5"><CodeBlock code={w.source} label="workflow.yaml" /></div>}
      </Card>
      {scheduling && <ScheduleDialog open onClose={() => setScheduling(false)} workflowId={w.id} />}
    </>
  );
}
