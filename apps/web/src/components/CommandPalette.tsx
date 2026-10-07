import { useQuery } from "@tanstack/react-query";
import { Activity, Boxes, CalendarClock, CornerDownLeft, Workflow, LayoutGrid, Layers, PlayCircle, Plus, Search, Server, Terminal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { api } from "../lib/api";
import { cn, Kbd } from "./ui";

interface Item {
  id: string;
  label: string;
  hint?: string;
  icon: typeof Search;
  to: string;
}

const STATIC: Item[] = [
  { id: "nav-overview", label: "Overview", icon: LayoutGrid, to: "/" },
  { id: "nav-jobs", label: "Jobs", icon: Boxes, to: "/jobs" },
  { id: "nav-workflows", label: "Workflows", icon: Workflow, to: "/workflows" },
  { id: "nav-runs", label: "Runs", icon: Activity, to: "/runs" },
  { id: "nav-schedules", label: "Schedules", icon: CalendarClock, to: "/schedules" },
  { id: "nav-queues", label: "Queues", icon: Layers, to: "/queues" },
  { id: "nav-fleet", label: "Fleet", hint: "agents", icon: Server, to: "/fleet" },
  { id: "new-job", label: "New job", icon: Plus, to: "/jobs/new" },
  { id: "new-workflow", label: "New workflow", icon: Plus, to: "/workflows/new" },
  { id: "connect-agent", label: "Connect an agent", icon: Terminal, to: "/fleet?connect=1" },
];

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return { open, setOpen };
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const jobs = useQuery({ queryKey: ["jobs", "palette"], queryFn: () => api.listJobs({ limit: 200 }), enabled: open });

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setQ("");
      setActive(0);
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  const items = useMemo(() => {
    const jobItems: Item[] = (jobs.data?.items ?? []).map((j) => ({
      id: `job-${j.id}`,
      label: j.name,
      hint: "job",
      icon: PlayCircle,
      to: `/jobs/${j.id}`,
    }));
    const needle = q.trim().toLowerCase();
    return [...STATIC, ...jobItems].filter((i) => !needle || i.label.toLowerCase().includes(needle)).slice(0, 12);
  }, [jobs.data, q]);

  const go = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    navigate(item.to);
  };

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-label="Command palette"
      className="mx-auto mt-[12vh] w-[min(560px,calc(100vw-2rem))] overflow-hidden rounded-2xl bg-surface p-0 text-fg shadow-2xl ring-1 ring-line-strong open:animate-slide-up"
    >
      <div className="flex items-center gap-3 border-b border-line px-4">
        <Search className="size-4 text-fg-subtle" aria-hidden="true" />
        <input
          autoFocus
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, items.length - 1));
            else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
            else if (e.key === "Enter") go(items[active]);
            else return;
            e.preventDefault();
          }}
          placeholder="Search jobs, pages and actions…"
          aria-label="Search"
          className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-fg-subtle"
        />
        <Kbd>esc</Kbd>
      </div>
      <ul role="listbox" className="max-h-80 overflow-y-auto p-1.5">
        {items.length === 0 && <li className="px-3 py-6 text-center text-sm text-fg-muted">No matches</li>}
        {items.map((item, i) => (
          <li
            key={item.id}
            role="option"
            aria-selected={i === active}
            onMouseEnter={() => setActive(i)}
            onClick={() => go(item)}
            className={cn(
              "flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm",
              i === active ? "bg-surface-3 text-fg" : "text-fg-muted",
            )}
          >
            <item.icon className="size-4 shrink-0" aria-hidden="true" />
            <span className="flex-1 truncate">{item.label}</span>
            {item.hint && <span className="text-xs text-fg-subtle">{item.hint}</span>}
            {i === active && <CornerDownLeft className="size-3.5 text-fg-subtle" aria-hidden="true" />}
          </li>
        ))}
      </ul>
    </dialog>
  );
}
