import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { useState } from "react";

import { RunsTable } from "../components/RunsTable";
import { Card, cn, EmptyState, ErrorState, Loading, PageHeader, Pager } from "../components/ui";
import { api, type RunStatus } from "../lib/api";

const LIMIT = 50;
const FILTERS: { label: string; value?: RunStatus }[] = [
  { label: "All" },
  { label: "Running", value: "RUNNING" },
  { label: "Queued", value: "QUEUED" },
  { label: "Succeeded", value: "SUCCEEDED" },
  { label: "Failed", value: "FAILED" },
  { label: "Timed out", value: "TIMED_OUT" },
  { label: "Retrying", value: "RETRY_WAIT" },
  { label: "Lost", value: "LOST" },
  { label: "Cancelled", value: "CANCELLED" },
];

export function RunsPage() {
  const [status, setStatus] = useState<RunStatus | undefined>();
  const [offset, setOffset] = useState(0);
  const runs = useQuery({
    queryKey: ["runs", { status, offset }],
    queryFn: () => api.listRuns({ status, limit: LIMIT, offset }),
    refetchInterval: 3000,
  });

  return (
    <>
      <PageHeader icon={Activity} title="Runs" subtitle="Every execution across all jobs, newest first." />
      <Card>
        <div className="flex flex-wrap gap-1 border-b border-line px-4 py-3" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button
              key={f.label}
              type="button"
              aria-pressed={status === f.value}
              onClick={() => {
                setStatus(f.value);
                setOffset(0);
              }}
              className={cn(
                "rounded-lg px-3 py-1.5 text-xs font-medium transition",
                status === f.value ? "bg-surface-3 text-fg ring-1 ring-inset ring-line-strong" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
              )}
            >
              {f.label}
            </button>
          ))}
          {runs.data && <span className="ml-auto self-center text-xs tabular-nums text-fg-subtle">{runs.data.total} runs</span>}
        </div>
        {runs.isPending ? (
          <Loading />
        ) : runs.isError ? (
          <ErrorState error={runs.error} />
        ) : runs.data.items.length === 0 ? (
          <EmptyState icon={Activity} title="No runs here yet" />
        ) : (
          <>
            <RunsTable runs={runs.data.items} />
            <Pager total={runs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
