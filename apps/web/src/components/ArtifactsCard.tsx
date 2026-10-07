import { useQuery } from "@tanstack/react-query";
import { Download, FileArchive } from "lucide-react";

import { api } from "../lib/api";
import { formatBytes } from "../lib/format";
import { Card } from "./ui";

/** Files the run left in $TORQRUN_ARTIFACTS_DIR. Hidden when there are none. */
export function ArtifactsCard({ runId, live }: { runId: string; live: boolean }) {
  const artifacts = useQuery({
    queryKey: ["artifacts", runId, live], // refetch once the run ends: uploads finish right before
    queryFn: () => api.listArtifacts(runId),
    refetchInterval: live ? 3000 : false,
  });
  if (!artifacts.data?.length) return null;
  const attempts = new Set(artifacts.data.map((a) => a.attempt_no)).size;
  return (
    <Card title="Artifacts" icon={FileArchive} className="mt-6" actions={<span className="text-xs text-fg-subtle">{artifacts.data.length} file{artifacts.data.length === 1 ? "" : "s"}</span>}>
      <ul className="divide-y divide-line">
        {artifacts.data.map((a) => (
          <li key={a.id} className="flex items-center gap-3 px-5 py-2.5 text-sm">
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={`sha256 ${a.sha256}`}>
              {a.name}
            </span>
            {attempts > 1 && <span className="text-[11px] text-fg-subtle">attempt {a.attempt_no}</span>}
            <span className="w-20 text-right text-xs tabular-nums text-fg-muted">{formatBytes(a.size_bytes)}</span>
            <a
              href={api.artifactUrl(runId, a.id)}
              download={a.name}
              className="inline-flex h-7 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-fg-muted transition hover:bg-surface-2 hover:text-fg"
              aria-label={`Download ${a.name}`}
            >
              <Download className="size-3.5" aria-hidden="true" />
              Download
            </a>
          </li>
        ))}
      </ul>
    </Card>
  );
}
