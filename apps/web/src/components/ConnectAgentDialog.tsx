import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, KeyRound, Loader2, ShieldAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { api, type CreatedEnrollmentToken } from "../lib/api";
import { Button, CodeBlock, ErrorState, Tabs } from "./ui";

type Method = "linux" | "docker" | "source";

const inputClass =
  "w-full rounded-lg bg-bg px-3 py-2 text-sm ring-1 ring-inset ring-line outline-none placeholder:text-fg-subtle focus:ring-brand/50";

function commands(created: CreatedEnrollmentToken, origin: string): Record<Method, string> {
  const t = created.token;
  return {
    linux: created.install_command,
    docker: `docker run -d --name torq-agent --restart unless-stopped \\
  -e TORQRUN_AGENT_SERVER_URL=${origin} \\
  -e TORQRUN_AGENT_ENROLLMENT_TOKEN=${t} \\
  -e TORQRUN_AGENT_NAME="$(hostname)" \\
  -v torq-agent-state:/var/lib/torqrun-agent \\
  torqrun/agent:dev`,
    source: `TORQRUN_AGENT_SERVER_URL=${origin} \\
TORQRUN_AGENT_ENROLLMENT_TOKEN=${t} \\
TORQRUN_AGENT_NAME="$(hostname)" \\
uv run torq-agent start`,
  };
}

const HINTS: Record<Method, string> = {
  linux:
    "Run on the server (Ubuntu, Debian, RHEL, Amazon Linux…). Installs a private Python 3.12, a `torqrun` system user and the `torq-agent` systemd service.",
  docker:
    "Jobs run inside the agent container. Uses the torqrun/agent image built by `make up`; add --network host to reach a control plane on localhost.",
  source: "From a checkout of this repository. Jobs run as your user. Requires uv.",
};

export function ConnectAgentDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const [method, setMethod] = useState<Method>("linux");
  const [description, setDescription] = useState("");
  const [queues, setQueues] = useState("");
  const [ttl, setTtl] = useState(60);
  const [created, setCreated] = useState<CreatedEnrollmentToken | null>(null);
  const [known, setKnown] = useState<Set<string> | null>(null);
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.listAgents, refetchInterval: open ? 2000 : 5000 });

  const create = useMutation({
    mutationFn: () =>
      api.createEnrollmentToken({
        description,
        ttl_minutes: ttl,
        max_uses: 1,
        queues: queues.split(",").map((q) => q.trim()).filter(Boolean),
        tags: [],
      }),
    onSuccess: async (token) => {
      setCreated(token);
      setKnown(new Set((agents.data ?? []).map((a) => a.id)));
      await queryClient.invalidateQueries({ queryKey: ["enrollment-tokens"] });
    },
  });

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    else if (!open && d.open) d.close();
    if (!open) {
      setCreated(null);
      setKnown(null);
      create.reset();
    }
  }, [open]);

  const fresh = known ? agents.data?.find((a) => !known.has(a.id) && a.connected) : undefined;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    create.mutate();
  }

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby="connect-title"
      className="mx-auto mt-[6vh] w-[min(700px,calc(100vw-2rem))] overflow-hidden rounded-2xl bg-surface p-0 text-fg shadow-2xl ring-1 ring-line-strong open:animate-slide-up"
    >
      <div className="flex items-start justify-between gap-4 px-6 pt-6">
        <div>
          <h2 id="connect-title" className="text-lg font-semibold tracking-tight">
            Connect an agent
          </h2>
          <p className="mt-1 text-sm text-fg-muted">
            An agent runs jobs on a machine you control. It connects <em>out</em> to Torqrun over HTTPS — no inbound
            ports, no SSH keys.
          </p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" className="grid size-8 shrink-0 place-items-center rounded-lg text-fg-muted hover:bg-surface-2">
          <X className="size-4" />
        </button>
      </div>

      {!created ? (
        <form onSubmit={onSubmit} className="space-y-4 px-6 py-5">
          <p className="flex items-center gap-2 text-sm font-medium">
            <KeyRound className="size-4 text-fg-subtle" aria-hidden="true" />
            Create a single-use enrollment token
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-xs text-fg-muted sm:col-span-3">
              Which machine is this for?
              <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="e.g. build-server-01" className={`${inputClass} mt-1.5`} />
            </label>
            <label className="text-xs text-fg-muted sm:col-span-2">
              Queues to serve (comma-separated, optional)
              <input value={queues} onChange={(e) => setQueues(e.target.value)} placeholder="default" className={`${inputClass} mt-1.5 font-mono`} />
            </label>
            <label className="text-xs text-fg-muted">
              Expires in
              <select value={ttl} onChange={(e) => setTtl(Number(e.target.value))} className={`${inputClass} mt-1.5`}>
                <option value={15}>15 minutes</option>
                <option value={60}>1 hour</option>
                <option value={1440}>1 day</option>
                <option value={10080}>7 days</option>
              </select>
            </label>
          </div>
          {create.isError && <ErrorState error={create.error} />}
          <div className="flex justify-end">
            <Button type="submit" variant="primary" icon={KeyRound} disabled={create.isPending}>
              {create.isPending ? "Creating…" : "Create token"}
            </Button>
          </div>
        </form>
      ) : (
        <>
          <div className="mt-5">
            <Tabs<Method>
              value={method}
              onChange={setMethod}
              tabs={[
                { value: "linux", label: "Linux server" },
                { value: "docker", label: "Docker" },
                { value: "source", label: "From source" },
              ]}
            />
          </div>
          <div className="space-y-4 px-6 py-5">
            <CodeBlock code={commands(created, window.location.origin)[method]} />
            <p className="text-xs text-fg-muted">{HINTS[method]}</p>
            <p className="text-xs text-fg-muted">
              Token <code className="font-mono">{created.prefix}…</code> works once and expires in {ttl >= 1440 ? `${ttl / 1440} day(s)` : `${ttl} minutes`}.
              It is shown only now.
            </p>
            <div className="flex items-start gap-3 rounded-xl bg-warn/8 px-4 py-3 text-xs ring-1 ring-inset ring-warn/20">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden="true" />
              <p className="text-fg-muted">
                Remote servers must reach this control plane: put it behind HTTPS (<code className="font-mono">make up-tls</code>) and turn on
                authentication before exposing it. Jobs run with the agent's OS permissions.
              </p>
            </div>
            <div className="flex items-center gap-3 rounded-xl bg-surface-2 px-4 py-3 text-sm ring-1 ring-inset ring-line" aria-live="polite">
              {fresh ? (
                <>
                  <CheckCircle2 className="size-5 text-ok" aria-hidden="true" />
                  <span>
                    <Link to={`/fleet/${fresh.id}`} onClick={onClose} className="font-medium hover:underline">
                      {fresh.name}
                    </Link>{" "}
                    connected. It's ready to run jobs.
                  </span>
                </>
              ) : (
                <>
                  <Loader2 className="size-4 animate-spin text-fg-subtle" aria-hidden="true" />
                  <span className="text-fg-muted">Waiting for the agent to connect…</span>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </dialog>
  );
}
