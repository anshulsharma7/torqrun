import { useRef } from "react";

/**
 * Lightweight code editor: textarea with a synced line-number gutter and Tab indentation.
 * (A full editor such as CodeMirror can replace this later without changing callers.)
 */
export function CodeEditor({
  id,
  value,
  onChange,
  rows = 18,
  label,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  label: string;
}) {
  const gutter = useRef<HTMLDivElement>(null);
  const lines = value.split("\n").length;
  return (
    <div className="flex overflow-hidden rounded-xl bg-bg ring-1 ring-inset ring-line focus-within:ring-brand/50">
      <div
        ref={gutter}
        aria-hidden="true"
        className="overflow-hidden border-r border-line bg-surface/50 px-3 py-3 text-right font-mono text-[12.5px] leading-5 text-fg-subtle/70 select-none"
        style={{ height: `${rows * 20 + 24}px` }}
      >
        {Array.from({ length: Math.max(lines, rows) }, (_, i) => (
          <div key={i}>{i + 1}</div>
        ))}
      </div>
      <textarea
        id={id}
        aria-label={label}
        value={value}
        spellCheck={false}
        rows={rows}
        required
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop;
        }}
        onKeyDown={(e) => {
          if (e.key !== "Tab" || e.shiftKey) return;
          e.preventDefault();
          const el = e.currentTarget;
          const { selectionStart: start, selectionEnd: end } = el;
          onChange(value.slice(0, start) + "    " + value.slice(end));
          requestAnimationFrame(() => el.setSelectionRange(start + 4, start + 4));
        }}
        className="min-w-0 flex-1 resize-none bg-transparent px-4 py-3 font-mono text-[12.5px] leading-5 whitespace-pre text-fg outline-none"
      />
    </div>
  );
}
