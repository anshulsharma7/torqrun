import { useMutation, useQuery } from "@tanstack/react-query";
import { Play, Plus, Workflow as WorkflowIcon } from "lucide-react";
import { Link, useNavigate } from "react-router";

import { Button, ButtonLink, Card, EmptyState, ErrorState, Loading, PageHeader, Table } from "../components/ui";
import { Can } from "../lib/auth";
import { WorkflowRunBadge } from "../components/WorkflowRunStatus";
import { api } from "../lib/api";
import { formatRelative } from "../lib/format";

export function WorkflowsPage() {
  const navigate = useNavigate();
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows, refetchInterval: 5000 });
  const run = useMutation({ mutationFn: (id: string) => api.runWorkflow(id), onSuccess: (wr) => navigate(`/workflow-runs/${wr.id}`) });

  return (
    <>
      <PageHeader
        icon={WorkflowIcon}
        title="Workflows"
        subtitle="Chain jobs into pipelines: tasks run when their dependencies finish, in parallel where possible."
        actions={<Can role="operator"><ButtonLink to="/workflows/new" variant="primary" icon={Plus}>New workflow</ButtonLink></Can>}
      />
      <Card>
        {workflows.isPending ? (
          <Loading />
        ) : workflows.isError ? (
          <ErrorState error={workflows.error} />
        ) : workflows.data.length === 0 ? (
          <EmptyState icon={WorkflowIcon} title="No workflows yet" action={<Can role="operator"><ButtonLink to="/workflows/new" variant="primary" icon={Plus}>New workflow</ButtonLink></Can>}>
            A workflow runs several jobs in order — for example extract → transform → report.
          </EmptyState>
        ) : (
          <Table head={["Workflow", "Tasks", "Version", "Last run", ""]}>
            {workflows.data.map((w) => (
              <tr key={w.id} className="transition hover:bg-surface-2/50">
                <td className="px-5 py-3">
                  <Link to={`/workflows/${w.id}`} className="font-medium hover:text-brand">{w.name}</Link>
                  {w.description && <p className="max-w-md truncate text-xs text-fg-muted">{w.description}</p>}
                </td>
                <td className="px-5 py-3 tabular-nums text-fg-muted">
                  {w.tasks.length} task{w.tasks.length === 1 ? "" : "s"} · {w.layers.length} stage{w.layers.length === 1 ? "" : "s"}
                </td>
                <td className="px-5 py-3 font-mono text-xs text-fg-muted">v{w.current_version}</td>
                <td className="px-5 py-3 whitespace-nowrap">
                  {w.last_run ? (
                    <Link to={`/workflow-runs/${w.last_run.id}`} className="flex items-center gap-2">
                      <WorkflowRunBadge status={w.last_run.status} />
                      <span className="text-xs text-fg-subtle">{formatRelative(w.last_run.created_at)}</span>
                    </Link>
                  ) : (
                    <span className="text-xs text-fg-subtle">never run</span>
                  )}
                </td>
                <td className="px-5 py-3 text-right">
                  <Button size="sm" icon={Play} onClick={() => run.mutate(w.id)} disabled={run.isPending} aria-label={`Run ${w.name}`}>
                    Run
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
