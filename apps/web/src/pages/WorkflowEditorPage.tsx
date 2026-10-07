import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Workflow as WorkflowIcon } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";

import { CodeEditor } from "../components/CodeEditor";
import { DagGraph } from "../components/DagGraph";
import { Button, Card, ErrorState, Loading, PageHeader } from "../components/ui";
import { api, type Workflow } from "../lib/api";

const EXAMPLE = `# Each task runs a job. depends_on lists tasks that must finish first.
# trigger_rule: all_success (default) | all_done (always, e.g. cleanup) | one_failed (alerts)
tasks:
  extract:
    job: extract-orders
  transform:
    job: transform-orders
    depends_on: [extract]
  report:
    job: send-report
    depends_on: [transform]
  cleanup:
    job: cleanup-tmp
    depends_on: [transform]
    trigger_rule: all_done
`;

const inputClass =
  "w-full rounded-lg bg-bg px-3 py-2 text-sm ring-1 ring-inset ring-line outline-none placeholder:text-fg-subtle focus:ring-brand/50 disabled:opacity-60";

export function WorkflowEditorPage() {
  const { workflowId } = useParams();
  const existing = useQuery({ queryKey: ["workflow", workflowId], queryFn: () => api.getWorkflow(workflowId!), enabled: !!workflowId });
  if (workflowId && existing.isPending) return <Card><Loading /></Card>;
  if (workflowId && existing.isError) return <Card><ErrorState error={existing.error} /></Card>;
  return <Editor key={workflowId ?? "new"} workflow={existing.data} />;
}

function Editor({ workflow }: { workflow?: Workflow }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api.listJobs({ limit: 200 }) });
  const [name, setName] = useState(workflow?.name ?? "");
  const [description, setDescription] = useState(workflow?.description ?? "");
  const [source, setSource] = useState(workflow?.source ?? "");
  const [debounced, setDebounced] = useState(source);

  useEffect(() => {
    if (workflow || source || !jobs.data) return;
    // Start new workflows from an example that uses real job names when there are some.
    const names = jobs.data.items.map((j) => j.name);
    if (names.length >= 2) {
      setSource(`tasks:\n  first:\n    job: ${names[0]}\n  second:\n    job: ${names[1]}\n    depends_on: [first]\n`);
    } else {
      setSource(EXAMPLE);
    }
  }, [jobs.data, workflow, source]);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(source), 300);
    return () => clearTimeout(t);
  }, [source]);

  const check = useQuery({
    queryKey: ["workflow-validate", debounced],
    queryFn: () => api.validateWorkflow(debounced),
    enabled: debounced.trim().length > 0,
  });

  const save = useMutation({
    mutationFn: () =>
      workflow ? api.updateWorkflow(workflow.id, { description, source }) : api.createWorkflow({ name, description, source }),
    onSuccess: async (w) => {
      await queryClient.invalidateQueries({ queryKey: ["workflows"] });
      await queryClient.invalidateQueries({ queryKey: ["workflow", w.id] });
      navigate(`/workflows/${w.id}`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  return (
    <>
      <PageHeader
        icon={WorkflowIcon}
        title={workflow ? `Edit ${workflow.name}` : "New workflow"}
        subtitle={workflow ? `Saving a change creates v${workflow.current_version + 1}. Past runs keep their version.` : "Describe tasks in YAML; the graph updates as you type."}
        crumbs={[{ to: "/workflows", label: "Workflows" }, ...(workflow ? [{ to: `/workflows/${workflow.id}`, label: workflow.name }] : [])]}
      />
      <form onSubmit={onSubmit} className="grid grid-cols-1 gap-6">
        <div className="min-w-0 space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-muted">
              Name
              <input required disabled={!!workflow} pattern="[a-z0-9][a-z0-9_.\-]{0,62}" value={name} onChange={(e) => setName(e.target.value)} placeholder="nightly-etl" className={`${inputClass} mt-1.5 font-mono`} />
            </label>
            <label className="text-xs text-fg-muted">
              Description
              <input value={description} onChange={(e) => setDescription(e.target.value)} className={`${inputClass} mt-1.5`} />
            </label>
          </div>
          <CodeEditor id="workflow-source" label="Workflow definition (YAML)" value={source} onChange={setSource} rows={14} />
          {jobs.data && (
            <details className="text-xs text-fg-subtle">
              <summary className="cursor-pointer select-none hover:text-fg-muted">
                {jobs.data.total ? `${jobs.data.total} jobs available to use as tasks` : "No jobs yet — create jobs first"}
              </summary>
              <div className="mt-2 flex max-h-32 flex-wrap gap-1 overflow-y-auto">
                {jobs.data.items.map((j) => (
                  <button key={j.id} type="button" onClick={() => navigator.clipboard?.writeText(j.name)} title="Copy name" className="rounded-md bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted hover:text-fg">
                    {j.name}
                  </button>
                ))}
              </div>
            </details>
          )}
        </div>
        <div className="min-w-0 space-y-4">
          <Card title="Preview" bodyClassName="p-4">
            {check.data?.ok ? (
              <>
                <p className="mb-3 flex items-center gap-2 text-sm text-ok">
                  <CheckCircle2 className="size-4" aria-hidden="true" /> Valid · {check.data.tasks.length} tasks in {check.data.layers.length} stages
                </p>
                <DagGraph tasks={check.data.tasks} layers={check.data.layers} label="Workflow preview" />
              </>
            ) : check.data?.error ? (
              <p role="alert" className="flex items-start gap-2 text-sm text-bad">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> {check.data.error}
              </p>
            ) : (
              <Loading rows={2} />
            )}
          </Card>
          {save.isError && <ErrorState error={save.error} />}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={!check.data?.ok || save.isPending}>
              {save.isPending ? "Saving…" : workflow ? "Save workflow" : "Create workflow"}
            </Button>
            <Button onClick={() => navigate(workflow ? `/workflows/${workflow.id}` : "/workflows")}>Cancel</Button>
          </div>
        </div>
      </form>
    </>
  );
}
