import { useEffect, useState } from "react";

import { api, type LogChunk, type Run } from "./api";

export interface RunStreamState {
  chunks: LogChunk[];
  run: Run | null;
  ended: boolean;
  connected: boolean;
}

/**
 * Subscribes to /api/v1/runs/{id}/stream (Server-Sent Events) for one attempt. The server
 * replays existing log lines, streams new ones, and sends `end` once that attempt has finished
 * and every line was delivered. EventSource reconnects on its own and resumes from the last
 * sequence number (Last-Event-ID), so lines are neither lost nor repeated.
 */
export function useRunStream(runId: string, attempt?: number): RunStreamState {
  const [state, setState] = useState<RunStreamState>({ chunks: [], run: null, ended: false, connected: false });

  useEffect(() => {
    setState((s) => ({ chunks: [], run: s.run, ended: false, connected: false }));
    const source = new EventSource(api.runStreamUrl(runId, attempt));
    let lastSeq = -1;

    source.onopen = () => setState((s) => ({ ...s, connected: true }));
    source.onerror = () => setState((s) => ({ ...s, connected: false }));
    source.addEventListener("run", (e) => {
      const run = JSON.parse((e as MessageEvent<string>).data) as Run;
      setState((s) => ({ ...s, run }));
    });
    source.addEventListener("logs", (e) => {
      const incoming = (JSON.parse((e as MessageEvent<string>).data) as LogChunk[]).filter((c) => c.seq > lastSeq);
      if (incoming.length === 0) return;
      lastSeq = incoming[incoming.length - 1]!.seq;
      setState((s) => ({ ...s, chunks: [...s.chunks, ...incoming] }));
    });
    source.addEventListener("end", () => {
      source.close();
      setState((s) => ({ ...s, ended: true, connected: false }));
    });
    return () => source.close();
  }, [runId, attempt]);

  return state;
}
