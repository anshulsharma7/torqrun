import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Box, FileCode2, LifeBuoy, Settings2, Sparkles, TerminalSquare } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { CodeEditor } from "../components/CodeEditor";
import { Button, Card, cn, ErrorState, Field, inputClass, Loading, PageHeader } from "../components/ui";
import { useEdition } from "../lib/edition";
import { api, type Job, type JobSpec } from "../lib/api";

interface Template {
  id: string;
  title: string;
  runtime: JobSpec["runtime"];
  script: string;
  args?: string;
}

const TEMPLATES: Template[] = [
  {
    id: "hello-py",
    title: "Hello, Python",
    runtime: "python",
    script: 'import os\nimport time\n\nprint(f"hello from {os.environ[\'TORQRUN_JOB_NAME\']}")\nfor i in range(5):\n    print(f"step {i + 1}/5")\n    time.sleep(1)\nprint("done")\n',
  },
  {
    id: "hello-sh",
    title: "Hello, Bash",
    runtime: "shell",
    script: 'set -euo pipefail\necho "hello from $TORQRUN_JOB_NAME on $(hostname)"\nfor i in 1 2 3; do echo "step $i"; sleep 1; done\n',
  },
  {
    id: "http",
    title: "HTTP health check",
    runtime: "python",
    args: "https://example.com",
    script: 'import sys\nimport urllib.request\n\nurl = sys.argv[1]\nwith urllib.request.urlopen(url, timeout=10) as r:\n    print(f"{url} -> HTTP {r.status}")\n    if r.status >= 400:\n        sys.exit(1)\n',
  },
  {
    id: "disk",
    title: "Disk space report",
    runtime: "shell",
    script: 'set -euo pipefail\ndf -h /\nused=$(df --output=pcent / | tail -1 | tr -dc 0-9)\nif [ "$used" -gt 90 ]; then echo "disk ${used}% full" >&2; exit 1; fi\n',
  },
];

interface FormState {
  name: string;
  description: string;
  runtime: JobSpec["runtime"];
  script: string;
  args: string;
  env: string;
  secrets: string; // ENV=secret-name per line
  timeout_seconds: number;
  queue: string;
  priority: number;
  max_attempts: number;
  backoff_seconds: number;
  retry_on_timeout: boolean;
  interrupt_policy: JobSpec["interrupt_policy"];
  max_concurrent: string; // "" = unlimited
  executor: JobSpec["executor"];
  image: string;
  network: "bridge" | "none";
  memory_mb: string; // "" = no limit
  cpus: string; // "" = no limit
}

const RELIABILITY_DEFAULTS = {
  max_attempts: 1,
  backoff_seconds: 10,
  retry_on_timeout: false,
  interrupt_policy: "fail" as const,
  max_concurrent: "",
};

const ISOLATION_DEFAULTS = { executor: "process" as const, image: "", network: "bridge" as const, memory_mb: "", cpus: "" };

function fromJob(job?: Job): FormState {
  if (!job) {
    const t = TEMPLATES[0]!;
    return { name: "", description: "", runtime: t.runtime, script: t.script, args: "", env: "", secrets: "", timeout_seconds: 3600, queue: "default", priority: 0, ...RELIABILITY_DEFAULTS, ...ISOLATION_DEFAULTS };
  }
  return {
    name: job.name,
    description: job.description,
    runtime: job.spec.runtime,
    script: job.spec.script,
    args: job.spec.args.join("\n"),
    env: Object.entries(job.spec.env).map(([k, v]) => `${k}=${v}`).join("\n"),
    secrets: Object.entries(job.spec.secrets ?? {}).map(([k, v]) => `${k}=${v}`).join("\n"),
    timeout_seconds: job.spec.timeout_seconds,
    queue: job.spec.queue,
    priority: job.spec.priority,
    max_attempts: job.spec.retry?.max_attempts ?? 1,
    backoff_seconds: job.spec.retry?.backoff_seconds ?? 10,
    retry_on_timeout: job.spec.retry?.retry_on_timeout ?? false,
    interrupt_policy: job.spec.interrupt_policy ?? "fail",
    max_concurrent: job.spec.max_concurrent ? String(job.spec.max_concurrent) : "",
    executor: job.spec.executor ?? "process",
    image: job.spec.container?.image ?? "",
    network: job.spec.container?.network ?? "bridge",
    memory_mb: job.spec.container?.memory_mb ? String(job.spec.container.memory_mb) : "",
    cpus: job.spec.container?.cpus ? String(job.spec.container.cpus) : "",
  };
}

export function parseEnv(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) throw new Error(`Environment line "${line}" must look like NAME=value`);
    env[line.slice(0, eq).trim()] = line.slice(eq + 1);
  }
  return env;
}

function toSpec(f: FormState): Partial<JobSpec> {
  return {
    runtime: f.runtime,
    script: f.script,
    args: f.args.split("\n").filter((a) => a.length > 0),
    env: parseEnv(f.env),
    secrets: parseEnv(f.secrets),
    timeout_seconds: f.timeout_seconds,
    queue: f.queue,
    priority: f.priority,
    retry: {
      max_attempts: f.max_attempts,
      backoff_seconds: f.backoff_seconds,
      backoff_factor: 2,
      max_backoff_seconds: 600,
      retry_on_timeout: f.retry_on_timeout,
    },
    interrupt_policy: f.interrupt_policy,
    max_concurrent: f.max_concurrent === "" ? null : Number(f.max_concurrent),
    executor: f.executor,
    container:
      f.executor === "docker"
        ? {
            image: f.image.trim(),
            network: f.network,
            memory_mb: f.memory_mb === "" ? null : Number(f.memory_mb),
            cpus: f.cpus === "" ? null : Number(f.cpus),
            pull: "missing",
          }
        : null,
  };
}

export function JobFormPage() {
  const { jobId } = useParams();
  const existing = useQuery({ queryKey: ["job", jobId], queryFn: () => api.getJob(jobId!), enabled: !!jobId });
  if (jobId && existing.isPending) return <Card><Loading /></Card>;
  if (jobId && existing.isError) return <Card><ErrorState error={existing.error} /></Card>;
  return <JobForm key={jobId ?? "new"} job={existing.data} />;
}

function JobForm({ job }: { job?: Job }) {
  const [form, setForm] = useState<FormState>(() => fromJob(job));
  const [localError, setLocalError] = useState<string | null>(null);
  const navigate = useNavigate();
  const edition = useEdition();
  const queryClient = useQueryClient();
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setForm((f) => ({ ...f, [key]: value }));

  const save = useMutation({
    mutationFn: (spec: Partial<JobSpec>) =>
      job
        ? api.updateJob(job.id, { description: form.description, spec })
        : api.createJob({ name: form.name, description: form.description, spec }),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
      await queryClient.invalidateQueries({ queryKey: ["job", saved.id] });
      navigate(`/jobs/${saved.id}`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    try {
      save.mutate(toSpec(form));
    } catch (err) {
      setLocalError((err as Error).message);
    }
  }

  const isUntouched = TEMPLATES.some((t) => t.script === form.script) || form.script.trim() === "";
  const switchRuntime = (runtime: JobSpec["runtime"]) => {
    const t = TEMPLATES.find((x) => x.runtime === runtime)!;
    setForm((f) => ({ ...f, runtime, script: isUntouched ? t.script : f.script }));
  };
  const applyTemplate = (t: Template) => setForm((f) => ({ ...f, runtime: t.runtime, script: t.script, args: t.args ?? f.args }));

  return (
    <>
      <PageHeader
        title={job ? `Edit ${job.name}` : "New job"}
        subtitle={
          job
            ? `Saving a change creates v${job.current_version + 1}. Past runs keep the version they ran.`
            : "Write a script, pick how it runs, and you're ready to go."
        }
        crumbs={[{ to: "/jobs", label: "Jobs" }, ...(job ? [{ to: `/jobs/${job.id}`, label: job.name }] : [])]}
      />
      <form onSubmit={onSubmit} className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
          {!job && (
            <div className="flex flex-wrap items-center gap-2 lg:col-span-2">
              <span className="flex items-center gap-1.5 text-xs text-fg-subtle">
                <Sparkles className="size-3.5" aria-hidden="true" /> Start from
              </span>
              {TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => applyTemplate(t)}
                  className={cn(
                    "rounded-full px-3 py-1 text-xs ring-1 ring-inset transition",
                    form.script === t.script ? "bg-brand/12 text-brand ring-brand/35" : "text-fg-muted ring-line hover:text-fg hover:ring-line-strong",
                  )}
                >
                  {t.title}
                </button>
              ))}
            </div>
          )}
        <div className="min-w-0 space-y-6">
          <Card
            title="Script"
            icon={form.runtime === "python" ? FileCode2 : TerminalSquare}
            actions={
              <div className="flex rounded-lg bg-bg p-0.5 ring-1 ring-inset ring-line" role="radiogroup" aria-label="Runtime">
                {(["python", "shell"] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    role="radio"
                    aria-checked={form.runtime === r}
                    onClick={() => switchRuntime(r)}
                    className={cn(
                      "rounded-md px-2.5 py-1 font-mono text-xs transition",
                      form.runtime === r ? "bg-surface-3 text-fg shadow-sm" : "text-fg-muted hover:text-fg",
                    )}
                  >
                    {r === "python" ? "Python 3" : "Bash"}
                  </button>
                ))}
              </div>
            }
            bodyClassName="space-y-5 p-5"
          >
            <CodeEditor id="script" label="Script" value={form.script} onChange={(v) => set("script", v)} />
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
              <Field label="Arguments" htmlFor="args" hint="One per line. Passed as-is, never through a shell.">
                <textarea id="args" rows={3} value={form.args} onChange={(e) => set("args", e.target.value)} className={cn(inputClass, "font-mono text-xs")} />
              </Field>
              <Field label="Environment variables" htmlFor="env" hint="NAME=value per line. Visible to anyone who can view the job: put passwords in secrets.">
                <textarea id="env" rows={3} value={form.env} onChange={(e) => set("env", e.target.value)} className={cn(inputClass, "font-mono text-xs")} />
              </Field>
              {edition.has("secrets") ? (
                <Field label="Secrets" htmlFor="secrets" hint="NAME=secret-name per line. Injected at run time and masked in logs; values live under Admin → Secrets.">
                  <textarea id="secrets" rows={3} placeholder="DB_PASSWORD=prod-db-password" value={form.secrets} onChange={(e) => set("secrets", e.target.value)} className={cn(inputClass, "font-mono text-xs")} />
                </Field>
              ) : (
                <div>
                  <p className="mb-1.5 text-xs font-medium text-fg-muted">Secrets</p>
                  <p className="rounded-lg bg-surface-2/60 px-3 py-2.5 text-xs text-fg-muted ring-1 ring-inset ring-line">
                    Encrypted secrets, injected at run time and masked in logs, are part of{" "}
                    <Link to="/plans" className="font-medium text-brand hover:underline">Team &amp; Enterprise</Link>. In the Community
                    Edition, pass credentials through the agent host's environment.
                  </p>
                </div>
              )}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Settings" icon={Settings2} bodyClassName="space-y-4 p-5">
            <Field label="Name" htmlFor="name" hint={job ? "Names can't be changed." : "Lowercase letters, digits, - _ ."}>
              <input id="name" required disabled={!!job} pattern="[a-z0-9][a-z0-9_.\-]{0,62}" placeholder="nightly-backup" value={form.name} onChange={(e) => set("name", e.target.value)} className={cn(inputClass, "font-mono")} />
            </Field>
            <Field label="Description" htmlFor="description">
              <input id="description" placeholder="What does this job do?" value={form.description} onChange={(e) => set("description", e.target.value)} className={inputClass} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Timeout (s)" htmlFor="timeout">
                <input id="timeout" type="number" min={1} max={604800} required value={form.timeout_seconds} onChange={(e) => set("timeout_seconds", Number(e.target.value))} className={inputClass} />
              </Field>
              <Field label="Priority" htmlFor="priority">
                <input id="priority" type="number" min={-100} max={100} value={form.priority} onChange={(e) => set("priority", Number(e.target.value))} className={inputClass} />
              </Field>
            </div>
            <Field label="Queue" htmlFor="queue" hint="Only agents serving this queue run the job.">
              <input id="queue" required pattern="[a-z0-9][a-z0-9_.\-]{0,62}" value={form.queue} onChange={(e) => set("queue", e.target.value)} className={cn(inputClass, "font-mono")} />
            </Field>
          </Card>
          <Card title="Where it runs" icon={Box} bodyClassName="space-y-4 p-5">
            <Field
              label="Executor"
              htmlFor="executor"
              hint={
                form.executor === "docker"
                  ? "A fresh container per run: no capabilities, no privilege escalation, removed afterwards. Needs an agent with Docker."
                  : "A child process on the agent's machine, as the agent's user. Not a sandbox: only run scripts you trust."
              }
            >
              <select id="executor" value={form.executor} onChange={(e) => set("executor", e.target.value as JobSpec["executor"])} className={inputClass}>
                <option value="process">Process on the agent host</option>
                <option value="docker">Container (Docker)</option>
              </select>
            </Field>
            {form.executor === "docker" && (
              <>
                <Field label="Image" htmlFor="image" hint={`Must contain ${form.runtime === "python" ? "python3" : "bash"}.`}>
                  <input id="image" required pattern="[A-Za-z0-9][A-Za-z0-9._\/:@+\-]{0,254}" placeholder={form.runtime === "python" ? "python:3.12-slim" : "bash:5"} value={form.image} onChange={(e) => set("image", e.target.value)} className={cn(inputClass, "font-mono")} />
                </Field>
                <Field label="Network" htmlFor="network">
                  <select id="network" value={form.network} onChange={(e) => set("network", e.target.value as "bridge" | "none")} className={inputClass}>
                    <option value="bridge">Normal network access</option>
                    <option value="none">No network</option>
                  </select>
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Memory (MB)" htmlFor="memory">
                    <input id="memory" type="number" min={16} placeholder="no limit" value={form.memory_mb} onChange={(e) => set("memory_mb", e.target.value)} className={inputClass} />
                  </Field>
                  <Field label="CPUs" htmlFor="cpus">
                    <input id="cpus" type="number" min={0.1} step={0.1} placeholder="no limit" value={form.cpus} onChange={(e) => set("cpus", e.target.value)} className={inputClass} />
                  </Field>
                </div>
              </>
            )}
          </Card>
          <Card title="Reliability" icon={LifeBuoy} bodyClassName="space-y-4 p-5">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Attempts" htmlFor="max_attempts" hint="1 = no retries">
                <input id="max_attempts" type="number" min={1} max={20} value={form.max_attempts} onChange={(e) => set("max_attempts", Number(e.target.value))} className={inputClass} />
              </Field>
              <Field label="First retry after (s)" htmlFor="backoff" hint="doubles each time">
                <input id="backoff" type="number" min={1} max={3600} disabled={form.max_attempts <= 1} value={form.backoff_seconds} onChange={(e) => set("backoff_seconds", Number(e.target.value))} className={inputClass} />
              </Field>
            </div>
            <label className={cn("flex items-center gap-2 text-sm", form.max_attempts <= 1 && "opacity-50")}>
              <input type="checkbox" disabled={form.max_attempts <= 1} checked={form.retry_on_timeout} onChange={(e) => set("retry_on_timeout", e.target.checked)} className="size-4 accent-[var(--brand)]" />
              Also retry after a timeout
            </label>
            <Field label="If the agent dies mid-run" htmlFor="interrupt" hint="The script may have partly run. Only retry if running it twice is harmless.">
              <select id="interrupt" value={form.interrupt_policy} onChange={(e) => set("interrupt_policy", e.target.value as JobSpec["interrupt_policy"])} className={inputClass}>
                <option value="fail">Mark it lost (safe default)</option>
                <option value="retry">Retry it (job is safe to re-run)</option>
              </select>
            </Field>
            <Field label="Max concurrent runs" htmlFor="max_concurrent" hint="Blank = unlimited. Extra runs wait in the queue.">
              <input id="max_concurrent" type="number" min={1} max={1000} placeholder="unlimited" value={form.max_concurrent} onChange={(e) => set("max_concurrent", e.target.value)} className={inputClass} />
            </Field>
          </Card>
          {(localError || save.isError) && (
            <div role="alert" className="flex items-start gap-2.5 rounded-xl bg-bad/8 px-4 py-3 text-sm ring-1 ring-inset ring-bad/25">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden="true" />
              <span>{localError ?? (save.error as Error).message}</span>
            </div>
          )}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={save.isPending} className="flex-1">
              {save.isPending ? "Saving…" : job ? "Save changes" : "Create job"}
            </Button>
            <Button onClick={() => navigate(job ? `/jobs/${job.id}` : "/jobs")}>Cancel</Button>
          </div>
        </div>
      </form>
    </>
  );
}
