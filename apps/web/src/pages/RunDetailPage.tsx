import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, Ban, Clock, Cpu, Hash, History, Redo2, RotateCw, Server, SquareTerminal, Timer } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ArtifactsCard } from "../components/ArtifactsCard";
import { LogViewer } from "../components/LogViewer";
import { RunStepper } from "../components/RunStepper";
import { Button, Card, cn, ErrorState, Loading, PageHeader, RUN_LABEL, RUN_TONE, RunStatusBadge, Tabs, toneDot } from "../components/ui";
import { Can } from "../lib/auth";
import { api, TERMINAL_STATUSES, type RunDetail } from "../lib/api";
import { formatBytes, formatClock, formatDuration, formatTimestamp, shortId } from "../lib/format";
import { useRunStream } from "../lib/useRunStream";

function useNow(active: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function Fact({ icon: Icon, label, children }: { icon: typeof Clock; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <Icon className="size-4 shrink-0 text-fg-subtle" aria-hidden="true" />
      <dl className="min-w-0">
        <dt className="text-[11px] text-fg-subtle">{label}</dt>
        <dd className="truncate text-sm font-medium">{children}</dd>
      </dl>
    </div>
  );
}

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // Which attempt's output to show: follows the latest attempt unless the user picks one.
  const [pinnedAttempt, setPinnedAttempt] = useState<number | null>(null);
  const detail = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId),
    refetchInterval: (q) => (q.state.data && TERMINAL_STATUSES.has(q.state.data.status) ? false : 1500),
  });
  const shownAttempt = pinnedAttempt ?? detail.data?.current_attempt;
  const stream = useRunStream(runId, shownAttempt);
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["runs"] });
    await detail.refetch();
  };
  const rerun = useMutation({
    mutationFn: () => api.rerunRun(runId),
    onSuccess: async (r) => {
      await queryClient.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${r.id}`);
    },
  });
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: refresh });
  const retry = useMutation({
    mutationFn: () => api.retryRun(runId),
    onSuccess: async () => {
      setPinnedAttempt(null);
      await refresh();
    },
  });

  // Merge the live status from the stream only while it describes the same attempt.
  const live = stream.run && detail.data && stream.run.current_attempt === detail.data.current_attempt ? stream.run : null;
  const run: RunDetail | undefined = detail.data && live ? { ...detail.data, ...live } : detail.data;
  const running = !!run && !TERMINAL_STATUSES.has(run.status);
  const now = useNow(running);
  const attemptLive = running && shownAttempt === run?.current_attempt;

  useEffect(() => {
    if (stream.ended) void detail.refetch();
    // A new attempt started: the latest-attempt view moves to it automatically.
    // Refetch once when the stream reports the end; `detail` identity changes every render.
  }, [stream.ended]); // eslint-disable-line react-hooks/exhaustive-deps

  if (detail.isPending) return <Card><Loading /></Card>;
  if (detail.isError || !run) return <Card><ErrorState error={detail.error} /></Card>;

  const attempt = run.attempts.find((a) => a.attempt_no === shownAttempt) ?? run.attempts[run.attempts.length - 1];
  const canCancel = !TERMINAL_STATUSES.has(run.status) && run.status !== "CANCEL_REQUESTED";
  const canRetry = run.status === "FAILED" || run.status === "TIMED_OUT" || run.status === "LOST" || run.status === "RETRY_WAIT";
  const actionError = cancel.error ?? retry.error ?? rerun.error;
  const liveDuration = run.duration_seconds ?? (run.started_at ? (now - new Date(run.started_at).getTime()) / 1000 : null);

  return (
    <>
      <PageHeader
        crumbs={[
          { to: "/runs", label: "Runs" },
          { to: `/jobs/${run.job_id}`, label: run.job_name },
        ]}
        icon={SquareTerminal}
        title={
          <span className="flex items-center gap-3">
            <span>
              {run.job_name} <span className="font-mono text-base text-fg-subtle">#{shortId(run.id)}</span>
            </span>
            <RunStatusBadge status={run.status} />
          </span>
        }
        subtitle={`v${run.job_version} · ${run.trigger} trigger · queued ${formatTimestamp(run.queued_at)}`}
        actions={
          <Can role="operator">
            {canCancel && (
              <Button icon={Ban} onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                {cancel.isPending ? "Cancelling…" : "Cancel"}
              </Button>
            )}
            {canRetry && (
              <Button icon={Redo2} onClick={() => retry.mutate()} disabled={retry.isPending}>
                {run.status === "RETRY_WAIT" ? "Retry now" : "Retry"}
              </Button>
            )}
            <Button
              icon={RotateCw}
              variant={TERMINAL_STATUSES.has(run.status) ? "primary" : "secondary"}
              onClick={() => rerun.mutate()}
              disabled={rerun.isPending}
              title="Start a new run of this job"
            >
              Run again
            </Button>
          </Can>
        }
      />
      {actionError && <ErrorState error={actionError} />}
      {run.status === "RETRY_WAIT" && run.next_attempt_at && (
        <div role="status" className="mb-6 flex items-center gap-3 rounded-2xl bg-warn/8 px-5 py-3.5 text-sm ring-1 ring-inset ring-warn/25">
          <Timer className="size-4 shrink-0 text-warn" aria-hidden="true" />
          <span>
            Attempt {run.current_attempt} failed. Attempt {run.current_attempt + 1} of {run.max_attempts} starts in{" "}
            <span className="font-medium tabular-nums">
              {Math.max(0, Math.ceil((new Date(run.next_attempt_at).getTime() - now) / 1000))}s
            </span>
            .
          </span>
        </div>
      )}
      {run.status === "CANCEL_REQUESTED" && (
        <div role="status" className="mb-6 flex items-center gap-3 rounded-2xl bg-warn/8 px-5 py-3.5 text-sm ring-1 ring-inset ring-warn/25">
          <Ban className="size-4 shrink-0 text-warn" aria-hidden="true" />
          Cancelling: the agent is stopping the process…
        </div>
      )}

      <Card className="mb-6" bodyClassName="p-5">
        <RunStepper run={run} attemptNo={shownAttempt ?? run.current_attempt} now={now} />
      </Card>

      <div className="card mb-6 grid grid-cols-2 divide-line md:grid-cols-5 md:divide-x">
        <Fact icon={Hash} label="Exit code">
          <span className="font-mono">{attempt?.exit_code ?? run.exit_code ?? "—"}</span>
        </Fact>
        <Fact icon={Clock} label="Duration">
          <span className="tabular-nums">{formatDuration(liveDuration)}</span>
        </Fact>
        <Fact icon={Server} label="Agent">
          {attempt?.agent_id ? (
            <Link to={`/fleet/${attempt.agent_id}`} className="hover:text-brand">
              {attempt.agent_name}
            </Link>
          ) : (
            "—"
          )}
        </Fact>
        <Fact icon={Cpu} label="Process">
          {attempt?.pid ? <span className="font-mono">pid {attempt.pid}</span> : "—"}
        </Fact>
        <Fact icon={SquareTerminal} label="Output">
          {attempt ? formatBytes(attempt.log_bytes) : "—"}
        </Fact>
      </div>

      {(attempt?.error_summary ?? run.error_summary) && (
        <div role="alert" className="mb-6 flex gap-3 rounded-2xl bg-bad/8 px-5 py-4 ring-1 ring-inset ring-bad/25">
          <AlertOctagon className="mt-0.5 size-5 shrink-0 text-bad" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-bad">
              {run.attempts.length > 1 || run.status === "RETRY_WAIT"
                ? `Attempt ${attempt?.attempt_no} ${RUN_LABEL[attempt?.status ?? run.status].toLowerCase()}`
                : RUN_LABEL[run.status]}
            </p>
            <pre className="mt-1 font-mono text-xs whitespace-pre-wrap text-fg">{attempt?.error_summary ?? run.error_summary}</pre>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_300px]">
        <Card
          title="Output"
          icon={SquareTerminal}
          actions={
            <span className="text-xs text-fg-subtle tabular-nums">
              attempt {shownAttempt} of {run.max_attempts}
            </span>
          }
        >
          {run.attempts.length > 1 && (
            <Tabs<string>
              value={String(shownAttempt)}
              onChange={(v) => setPinnedAttempt(Number(v) === run.current_attempt ? null : Number(v))}
              tabs={run.attempts.map((a) => ({ value: String(a.attempt_no), label: `Attempt ${a.attempt_no} · ${RUN_LABEL[a.status]}` }))}
            />
          )}
          <LogViewer chunks={stream.chunks} live={attemptLive} downloadUrl={api.logDownloadUrl(run.id, shownAttempt)} />
        </Card>
        <Card title="Timeline" icon={History}>
          <ol className="relative px-5 py-4">
            <span aria-hidden="true" className="absolute top-6 bottom-6 left-[27px] w-px bg-line-strong" />
            {run.events.map((e, i) => (
              <li key={i} className="relative flex gap-3 pb-4 last:pb-0">
                <span className={cn("relative z-[1] mt-1 size-2.5 shrink-0 rounded-full ring-4 ring-surface", toneDot[RUN_TONE[e.to_status]])} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium">{RUN_LABEL[e.to_status]}</span>
                    <time className="font-mono text-[11px] text-fg-subtle" dateTime={e.at} title={formatTimestamp(e.at)}>
                      {formatClock(e.at)}
                    </time>
                  </div>
                  <p className="truncate text-xs text-fg-muted" title={e.reason}>
                    {e.reason}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </Card>
      </div>
      <ArtifactsCard runId={run.id} live={!TERMINAL_STATUSES.has(run.status)} />
    </>
  );
}
