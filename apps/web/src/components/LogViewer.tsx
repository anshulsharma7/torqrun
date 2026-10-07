import { ArrowDownToLine, Download, Search, WrapText } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { LogChunk } from "../lib/api";
import { formatClock } from "../lib/format";
import { cn, CopyButton, Dot } from "./ui";

const streamClass: Record<LogChunk["stream"], string> = {
  stdout: "text-fg",
  stderr: "text-bad",
  system: "text-warn italic",
};

function Toggle({ on, onChange, label, icon: Icon }: { on: boolean; onChange: (v: boolean) => void; label: string; icon?: typeof Search }) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-xs transition select-none",
        on ? "bg-surface-3 text-fg" : "text-fg-subtle hover:text-fg-muted",
      )}
    >
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} className="sr-only" />
      {Icon && <Icon className="size-3.5" aria-hidden="true" />}
      {label}
    </label>
  );
}

function highlight(text: string, needle: string) {
  if (!needle) return text;
  const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return parts.map((p, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="rounded-sm bg-brand/30 text-fg">
        {p}
      </mark>
    ) : (
      p
    ),
  );
}

export function LogViewer({ chunks, live, downloadUrl }: { chunks: LogChunk[]; live: boolean; downloadUrl: string }) {
  const [showTime, setShowTime] = useState(true);
  const [follow, setFollow] = useState(true);
  const [wrap, setWrap] = useState(true);
  const [stderrOnly, setStderrOnly] = useState(false);
  const [query, setQuery] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const visible = useMemo(() => {
    const q = query.toLowerCase();
    return chunks.filter((c) => (!stderrOnly || c.stream !== "stdout") && (!q || c.data.toLowerCase().includes(q)));
  }, [chunks, stderrOnly, query]);

  // Scroll only the log panel, never the page around it.
  useEffect(() => {
    const el = scroller.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [visible.length, follow]);

  const allText = useMemo(() => chunks.map((c) => c.data).join(""), [chunks]);

  return (
    <div className="flex flex-col overflow-hidden rounded-b-[14px]">
      <div className="flex flex-wrap items-center gap-1 border-b border-line bg-surface-2/50 px-3 py-2">
        <div className="mr-2 flex items-center gap-2 px-1 text-xs text-fg-muted">
          {live ? <Dot tone="info" pulse /> : <Dot tone="neutral" />}
          <span className="tabular-nums">
            {chunks.length} line{chunks.length === 1 ? "" : "s"}
          </span>
          {live && <span className="text-info">live</span>}
        </div>
        <div className="relative mr-1">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-fg-subtle" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter output"
            aria-label="Filter output"
            className="h-7 w-40 rounded-md bg-bg pr-2 pl-7 text-xs ring-1 ring-inset ring-line outline-none placeholder:text-fg-subtle focus:ring-brand/50"
          />
        </div>
        <Toggle on={showTime} onChange={setShowTime} label="Timestamps" />
        <Toggle on={stderrOnly} onChange={setStderrOnly} label="stderr only" />
        <Toggle on={wrap} onChange={setWrap} label="Wrap" icon={WrapText} />
        <Toggle on={follow} onChange={setFollow} label="Follow" icon={ArrowDownToLine} />
        <div className="ml-auto flex items-center gap-0.5">
          <CopyButton text={allText} label="Copy output" />
          <a href={downloadUrl} title="Download" aria-label="Download" className="grid size-7 place-items-center rounded-md text-fg-muted hover:bg-surface-3 hover:text-fg">
            <Download className="size-3.5" />
          </a>
        </div>
      </div>
      <div
        ref={scroller}
        className="max-h-[62vh] min-h-64 overflow-auto bg-bg/80 py-3 font-mono text-[12.5px] leading-[1.6]"
        role="log"
        aria-live={live ? "polite" : "off"}
        aria-label="Run output"
      >
        {visible.length === 0 ? (
          <p className="px-4 text-fg-subtle">
            {query ? "No lines match." : live ? "Waiting for output…" : "No output."}
          </p>
        ) : (
          visible.map((c) => (
            <div key={c.seq} className="group flex gap-4 px-4 hover:bg-surface-2/60">
              <span className="w-8 shrink-0 text-right text-fg-subtle/60 tabular-nums select-none">{c.seq + 1}</span>
              {showTime && <span className="shrink-0 text-fg-subtle tabular-nums select-none">{formatClock(c.ts)}</span>}
              <span className={cn(streamClass[c.stream], wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre")}>
                {highlight(c.data.replace(/\n$/, ""), query)}
              </span>
            </div>
          ))
        )}
        {live && <div className="px-4 pt-1 pl-16"><span className="inline-block h-4 w-2 animate-pulse bg-fg-muted/70 align-middle" /></div>}
      </div>
    </div>
  );
}
