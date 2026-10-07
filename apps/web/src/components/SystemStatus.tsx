import { useQuery } from "@tanstack/react-query";
import { Database } from "lucide-react";

import { ApiError, api, type DatabaseStatus } from "../lib/api";
import { Card, StatusPill } from "./ui";

const dbLabel: Record<DatabaseStatus, { tone: "ok" | "warn" | "bad"; text: string }> = {
  ok: { tone: "ok", text: "Healthy" },
  migrations_pending: { tone: "warn", text: "Migrations pending" },
  unreachable: { tone: "bad", text: "Unreachable" },
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <dt className="text-sm text-fg-muted">{label}</dt>
      <dd className="min-w-0 truncate text-right text-sm">{children}</dd>
    </div>
  );
}

export function SystemStatus() {
  const query = useQuery({
    queryKey: ["system-info"],
    queryFn: ({ signal }) => api.systemInfo(signal),
    refetchInterval: 10_000,
    retry: false,
  });

  const badge = query.isPending ? (
    <StatusPill tone="neutral" label="Checking…" />
  ) : query.isError ? (
    <StatusPill tone="bad" label="API offline" />
  ) : (
    <StatusPill
      tone={query.data.database.status === "ok" ? "ok" : "warn"}
      label={query.data.database.status === "ok" ? "Operational" : "Degraded"}
    />
  );

  return (
    <Card title="Control plane" icon={Database} actions={badge}>
      <div className="px-5 py-1">
        {query.isPending && (
          <p className="py-6 text-sm text-fg-muted" role="status">
            Contacting the API…
          </p>
        )}
        {query.isError && (
          <div className="py-6 text-sm" role="alert">
            <p className="font-medium text-bad">
              {query.error instanceof ApiError ? query.error.message : "Unexpected error."}
            </p>
            <p className="mt-1 text-fg-muted">
              Check that the <code className="font-mono">api</code> service is running: <code className="font-mono">make logs</code>
            </p>
          </div>
        )}
        {query.isSuccess && (
          <dl className="divide-y divide-line">
            <Row label="API version">
              <span className="font-mono">{query.data.version}</span>
            </Row>
            <Row label="Environment">{query.data.environment}</Row>
            <Row label="Database">
              <StatusPill tone={dbLabel[query.data.database.status].tone} label={dbLabel[query.data.database.status].text} />
            </Row>
            <Row label="Schema revision">
              <span className="font-mono text-xs">
                {query.data.database.schema_revision ?? "—"}
                {query.data.database.status === "migrations_pending" && (
                  <span className="text-fg-muted"> (expected {query.data.database.expected_revision})</span>
                )}
              </span>
            </Row>
          </dl>
        )}
      </div>
    </Card>
  );
}
