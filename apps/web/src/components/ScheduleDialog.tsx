import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { api, type Schedule, type ScheduleInput } from "../lib/api";
import { CRON_PRESETS, describeCron, describeInterval, localTimeZone, timeZones } from "../lib/cron";
import { Button, cn, ErrorState } from "./ui";

const inputClass =
  "w-full rounded-lg bg-bg px-3 py-2 text-sm ring-1 ring-inset ring-line outline-none placeholder:text-fg-subtle focus:ring-brand/50";

function initial(jobId: string | undefined, existing?: Schedule, workflowId?: string): ScheduleInput {
  if (existing) {
    const { id: _id, target: _t, job_name: _jn, next_fire_at: _n, upcoming: _u, last_fired_at: _lf, last_run_id: _lr,
      last_run_status: _ls, skipped_count: _sc, last_skipped_at: _lsa, last_skip_reason: _lsr, created_at: _c, ...rest } = existing;
    return rest;
  }
  return {
    name: "",
    job_id: workflowId ? null : (jobId ?? ""),
    workflow_id: workflowId ?? null,
    kind: "cron",
    cron: "0 2 * * *",
    interval_seconds: 3600,
    timezone: localTimeZone(),
    misfire_policy: "run_once",
    misfire_grace_seconds: 60,
    max_catchup: 5,
    overlap_policy: "allow",
    enabled: true,
  };
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function ScheduleDialog({
  open,
  onClose,
  jobId,
  workflowId,
  schedule,
}: {
  open: boolean;
  onClose: () => void;
  jobId?: string;
  workflowId?: string;
  schedule?: Schedule;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ScheduleInput>(() => initial(jobId, schedule, workflowId));
  const set = <K extends keyof ScheduleInput>(k: K, v: ScheduleInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const pickTarget = !jobId && !workflowId && !schedule;
  const jobs = useQuery({ queryKey: ["jobs", "all"], queryFn: () => api.listJobs({ limit: 200 }), enabled: open && pickTarget });
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows, enabled: open && pickTarget });
  const serverZones = useQuery({ queryKey: ["timezones"], queryFn: api.listTimezones, staleTime: Infinity, enabled: open });
  const fallbackZones = useMemo(timeZones, []);
  const zones = serverZones.data ?? fallbackZones;

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setForm(initial(jobId, schedule, workflowId));
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open, jobId, schedule, workflowId]);

  const definition = useDebounced(
    { kind: form.kind, cron: form.kind === "cron" ? form.cron : null, interval_seconds: form.kind === "interval" ? form.interval_seconds : null, timezone: form.timezone },
    300,
  );
  const preview = useQuery({
    queryKey: ["schedule-preview", definition],
    queryFn: () => api.previewSchedule({ ...definition, count: 5 }),
    enabled: open,
    retry: false,
  });

  const save = useMutation({
    mutationFn: (body: ScheduleInput) => (schedule ? api.updateSchedule(schedule.id, body) : api.createSchedule(body)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["schedules"] });
      onClose();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate({
      ...form,
      cron: form.kind === "cron" ? form.cron : null,
      interval_seconds: form.kind === "interval" ? form.interval_seconds : null,
    });
  }

  const fmt = (iso: string, tz: string) =>
    new Date(iso).toLocaleString(undefined, { timeZone: tz, weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const describe = form.kind === "cron" ? describeCron(form.cron ?? "") : describeInterval(form.interval_seconds ?? 0);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-labelledby="schedule-title"
      className="mx-auto mt-[5vh] w-[min(640px,calc(100vw-2rem))] overflow-hidden rounded-2xl bg-surface p-0 text-fg shadow-2xl ring-1 ring-line-strong open:animate-slide-up"
    >
      <form onSubmit={onSubmit}>
        <div className="flex items-start justify-between gap-4 px-6 pt-6">
          <h2 id="schedule-title" className="flex items-center gap-2 text-lg font-semibold tracking-tight">
            <CalendarClock className="size-5 text-fg-subtle" aria-hidden="true" />
            {schedule ? "Edit schedule" : "New schedule"}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close" className="grid size-8 place-items-center rounded-lg text-fg-muted hover:bg-surface-2">
            <X className="size-4" />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-muted">
              Name
              <input required value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="nightly" className={`${inputClass} mt-1.5`} />
            </label>
            {pickTarget && (
              <label className="text-xs text-fg-muted">
                Runs
                <select
                  required
                  aria-label="Target"
                  value={form.workflow_id ? `w:${form.workflow_id}` : form.job_id ? `j:${form.job_id}` : ""}
                  onChange={(e) => {
                    const [kind, id] = e.target.value.split(":") as ["j" | "w", string];
                    setForm((f) => ({ ...f, job_id: kind === "j" ? id : null, workflow_id: kind === "w" ? id : null }));
                  }}
                  className={`${inputClass} mt-1.5`}
                >
                  <option value="">Choose a job or workflow…</option>
                  <optgroup label="Jobs">
                    {jobs.data?.items.map((j) => <option key={j.id} value={`j:${j.id}`}>{j.name}</option>)}
                  </optgroup>
                  <optgroup label="Workflows">
                    {workflows.data?.map((w) => <option key={w.id} value={`w:${w.id}`}>{w.name}</option>)}
                  </optgroup>
                </select>
              </label>
            )}
          </div>

          <div className="flex rounded-lg bg-bg p-0.5 ring-1 ring-inset ring-line" role="radiogroup" aria-label="Schedule type">
            {(["cron", "interval"] as const).map((k) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={form.kind === k}
                onClick={() => set("kind", k)}
                className={cn("flex-1 rounded-md px-3 py-1.5 text-sm transition", form.kind === k ? "bg-surface-3 text-fg" : "text-fg-muted hover:text-fg")}
              >
                {k === "cron" ? "Cron expression" : "Fixed interval"}
              </button>
            ))}
          </div>

          {form.kind === "cron" ? (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-1.5">
                {CRON_PRESETS.map((p) => (
                  <button
                    key={p.cron}
                    type="button"
                    onClick={() => set("cron", p.cron)}
                    className={cn("rounded-full px-2.5 py-1 text-xs ring-1 ring-inset", form.cron === p.cron ? "bg-brand/12 text-brand ring-brand/35" : "text-fg-muted ring-line hover:text-fg")}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <div className="grid gap-3 sm:grid-cols-[1fr_220px]">
                <label className="text-xs text-fg-muted">
                  Cron (minute hour day month weekday)
                  <input aria-label="Cron expression" value={form.cron ?? ""} onChange={(e) => set("cron", e.target.value)} className={`${inputClass} mt-1.5 font-mono`} />
                </label>
                <label className="text-xs text-fg-muted">
                  Time zone
                  <select aria-label="Time zone" value={form.timezone} onChange={(e) => set("timezone", e.target.value)} className={`${inputClass} mt-1.5`}>
                    {!zones.includes(form.timezone) && <option value={form.timezone}>{form.timezone}</option>}
                    {zones.map((z) => (
                      <option key={z} value={z}>{z}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          ) : (
            <label className="block text-xs text-fg-muted">
              Every (seconds, min 10)
              <input aria-label="Interval seconds" type="number" min={10} value={form.interval_seconds ?? 3600} onChange={(e) => set("interval_seconds", Number(e.target.value))} className={`${inputClass} mt-1.5`} />
            </label>
          )}

          <div className="rounded-xl bg-surface-2 px-4 py-3 ring-1 ring-inset ring-line" aria-live="polite">
            <p className="text-sm font-medium">{describe}</p>
            {preview.isError ? (
              <p className="mt-1 text-xs text-bad">{(preview.error as Error).message}</p>
            ) : (
              <ul className="mt-2 space-y-0.5 text-xs text-fg-muted" aria-label="Next runs">
                {preview.data?.next.map((t) => (
                  <li key={t} className="flex justify-between gap-4 tabular-nums">
                    <span>{fmt(t, form.timezone)}</span>
                    {form.timezone !== localTimeZone() && <span className="text-fg-subtle">{fmt(t, localTimeZone())} your time</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-muted">
              If runs were missed (scheduler down)
              <select value={form.misfire_policy} onChange={(e) => set("misfire_policy", e.target.value as ScheduleInput["misfire_policy"])} className={`${inputClass} mt-1.5`}>
                <option value="run_once">Run once to catch up</option>
                <option value="run_all">Run each missed slot (max {form.max_catchup})</option>
                <option value="skip">Skip them</option>
              </select>
            </label>
            <label className="text-xs text-fg-muted">
              If the previous run is still going
              <select value={form.overlap_policy} onChange={(e) => set("overlap_policy", e.target.value as ScheduleInput["overlap_policy"])} className={`${inputClass} mt-1.5`}>
                <option value="allow">Start anyway</option>
                <option value="skip">Skip this run</option>
              </select>
            </label>
          </div>
          {save.isError && <ErrorState error={save.error} />}
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={save.isPending || preview.isError}>
            {save.isPending ? "Saving…" : schedule ? "Save schedule" : "Create schedule"}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
