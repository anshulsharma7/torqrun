import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Pause, Play } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, StatusPill, Table } from "../components/ui";
import { api, type Queue } from "../lib/api";

function QueueRow({ q }: { q: Queue }) {
  const queryClient = useQueryClient();
  const [limit, setLimit] = useState(q.max_concurrency === null ? "" : String(q.max_concurrency));
  const save = useMutation({
    mutationFn: (body: { max_concurrency: number | null; paused: boolean }) => api.updateQueue(q.name, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["queues"] }),
  });
  const limitValue = limit === "" ? null : Number(limit);
  const dirty = limitValue !== q.max_concurrency;

  return (
    <tr className="transition hover:bg-surface-2/50">
      <td className="px-5 py-3"><Badge>{q.name}</Badge></td>
      <td className="px-5 py-3">
        {q.paused ? <StatusPill tone="warn" label="Paused" /> : q.agents_online ? <StatusPill tone="ok" label="Active" /> : <StatusPill tone="bad" label="No agents" />}
      </td>
      <td className="px-5 py-3 tabular-nums">{q.queued}</td>
      <td className="px-5 py-3 tabular-nums">{q.running}</td>
      <td className="px-5 py-3 tabular-nums text-fg-muted">{q.agents_online}</td>
      <td className="px-5 py-3">
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate({ max_concurrency: limitValue, paused: q.paused });
          }}
        >
          <label className="sr-only" htmlFor={`limit-${q.name}`}>Concurrency limit for {q.name}</label>
          <input
            id={`limit-${q.name}`}
            type="number"
            min={1}
            placeholder="unlimited"
            value={limit}
            onChange={(e) => setLimit(e.target.value)}
            className="h-7 w-28 rounded-md bg-bg px-2 text-xs ring-1 ring-inset ring-line outline-none focus:ring-brand/50"
          />
          {dirty && <Button size="sm" type="submit" disabled={save.isPending}>Save</Button>}
        </form>
      </td>
      <td className="px-5 py-3 text-right">
        <Button
          size="sm"
          icon={q.paused ? Play : Pause}
          onClick={() => save.mutate({ max_concurrency: q.max_concurrency, paused: !q.paused })}
          disabled={save.isPending}
        >
          {q.paused ? "Resume" : "Pause"}
        </Button>
      </td>
    </tr>
  );
}

export function QueuesPage() {
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.listQueues, refetchInterval: 3000 });
  return (
    <>
      <PageHeader
        icon={Layers}
        title="Queues"
        subtitle="Jobs name a queue; agents serve queues. Limit how many runs a queue executes at once, or pause it."
      />
      <Card>
        {queues.isPending ? (
          <Loading />
        ) : queues.isError ? (
          <ErrorState error={queues.error} />
        ) : queues.data.length === 0 ? (
          <EmptyState icon={Layers} title="No queues yet" />
        ) : (
          <Table head={["Queue", "State", "Waiting", "Running", "Agents", "Concurrency limit", ""]}>
            {queues.data.map((q) => <QueueRow key={`${q.name}-${q.max_concurrency}`} q={q} />)}
          </Table>
        )}
      </Card>
      <p className="mt-4 text-xs text-fg-subtle">
        Pausing stops new runs from starting; runs already in progress finish normally. Per-job limits are set on each job.
      </p>
    </>
  );
}
