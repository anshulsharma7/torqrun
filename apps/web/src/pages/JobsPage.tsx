import { useQuery } from "@tanstack/react-query";
import { Boxes, FileCode2, Plus, Search, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { RunDots } from "../components/RunHistory";
import { Badge, ButtonLink, Card, EmptyState, ErrorState, Loading, PageHeader, Pager, RunStatusBadge, Table } from "../components/ui";
import { Can } from "../lib/auth";
import { api } from "../lib/api";
import { formatRelative } from "../lib/format";

const LIMIT = 50;

export function JobsPage() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const jobs = useQuery({
    queryKey: ["jobs", q, offset],
    queryFn: () => api.listJobs({ q, limit: LIMIT, offset }),
    refetchInterval: 4000,
  });

  return (
    <>
      <PageHeader
        icon={Boxes}
        title="Jobs"
        subtitle="Scripts Torqrun can run. Every edit creates a new immutable version."
        actions={<Can role="operator"><ButtonLink to="/jobs/new" variant="primary" icon={Plus}>New job</ButtonLink></Can>}
      />
      <Card>
        <div className="border-b border-line px-4 py-3">
          <div className="relative max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-subtle" aria-hidden="true" />
            <input
              type="search"
              aria-label="Search jobs"
              placeholder="Search jobs…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
              className="h-8.5 w-full rounded-lg bg-bg pr-3 pl-8.5 text-sm ring-1 ring-inset ring-line outline-none placeholder:text-fg-subtle focus:ring-brand/50"
            />
          </div>
        </div>
        {jobs.isPending ? (
          <Loading />
        ) : jobs.isError ? (
          <ErrorState error={jobs.error} />
        ) : jobs.data.items.length === 0 ? (
          <EmptyState
            icon={Boxes}
            title={q ? "No jobs match your search" : "No jobs yet"}
            action={!q && <Can role="operator"><ButtonLink to="/jobs/new" variant="primary" icon={Plus}>Create your first job</ButtonLink></Can>}
          >
            {!q && "A job is a Python or Bash script plus how to run it."}
          </EmptyState>
        ) : (
          <>
            <Table head={["Job", "Recent runs", "Last run", "Queue", "Version"]}>
              {jobs.data.items.map((job) => {
                const Icon = job.spec.runtime === "python" ? FileCode2 : TerminalSquare;
                return (
                  <tr key={job.id} className="transition hover:bg-surface-2/50">
                    <td className="px-5 py-3">
                      <Link to={`/jobs/${job.id}`} className="group flex items-center gap-3">
                        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-2 ring-1 ring-line">
                          <Icon className="size-4 text-fg-muted" aria-hidden="true" />
                        </span>
                        <span className="min-w-0">
                          <span className="block font-medium whitespace-nowrap group-hover:text-brand">{job.name}</span>
                          <span className="block max-w-sm truncate text-xs text-fg-muted">
                            {job.description || (job.spec.runtime === "python" ? "Python 3" : "Bash")}
                          </span>
                        </span>
                      </Link>
                    </td>
                    <td className="px-5 py-3">
                      <RunDots runs={job.recent_runs} />
                    </td>
                    <td className="px-5 py-3 whitespace-nowrap">
                      {job.last_run ? (
                        <Link to={`/runs/${job.last_run.id}`} className="flex items-center gap-2">
                          <RunStatusBadge status={job.last_run.status} />
                          <span className="text-xs text-fg-subtle">{formatRelative(job.last_run.created_at)}</span>
                        </Link>
                      ) : (
                        <span className="text-xs text-fg-subtle">never run</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <Badge>{job.spec.queue}</Badge>
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-fg-muted">v{job.current_version}</td>
                  </tr>
                );
              })}
            </Table>
            <Pager total={jobs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
