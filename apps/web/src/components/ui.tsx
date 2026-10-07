import { AlertTriangle, Check, ChevronRight, Copy, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import type { RunStatus } from "../lib/api";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/* ---------------------------------------------------------------- status */

type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

const toneText: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-info",
  neutral: "text-fg-muted",
};
const toneBg: Record<Tone, string> = {
  ok: "bg-ok/10 ring-ok/25",
  warn: "bg-warn/10 ring-warn/25",
  bad: "bg-bad/10 ring-bad/25",
  info: "bg-info/10 ring-info/25",
  neutral: "bg-surface-3 ring-line-strong",
};
export const toneDot: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  bad: "bg-bad",
  info: "bg-info",
  neutral: "bg-fg-subtle",
};

export const RUN_TONE: Record<RunStatus, Tone> = {
  QUEUED: "neutral",
  DISPATCHED: "info",
  STARTING: "info",
  RUNNING: "info",
  RETRY_WAIT: "warn",
  SUCCEEDED: "ok",
  FAILED: "bad",
  CANCEL_REQUESTED: "warn",
  CANCELLED: "neutral",
  TIMED_OUT: "bad",
  LOST: "bad",
};

export const RUN_LABEL: Record<RunStatus, string> = {
  QUEUED: "Queued",
  DISPATCHED: "Dispatched",
  STARTING: "Starting",
  RUNNING: "Running",
  RETRY_WAIT: "Retry wait",
  SUCCEEDED: "Succeeded",
  FAILED: "Failed",
  CANCEL_REQUESTED: "Cancelling",
  CANCELLED: "Cancelled",
  TIMED_OUT: "Timed out",
  LOST: "Lost",
};

export function Dot({ tone, pulse = false, className }: { tone: Tone; pulse?: boolean; className?: string }) {
  return (
    <span aria-hidden="true" className={cn("relative inline-flex size-2 shrink-0", className)}>
      {pulse && <span className={cn("absolute inset-0 animate-ping rounded-full opacity-60", toneDot[tone])} />}
      <span className={cn("relative size-2 rounded-full", toneDot[tone])} />
    </span>
  );
}

export function StatusPill({ tone, label, pulse = false }: { tone: Tone; label: string; pulse?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        toneText[tone],
        toneBg[tone],
      )}
    >
      <Dot tone={tone} pulse={pulse} className="size-1.5 [&>span]:size-1.5" />
      {label}
    </span>
  );
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const live = status === "RUNNING" || status === "STARTING" || status === "DISPATCHED";
  return <StatusPill tone={RUN_TONE[status]} label={RUN_LABEL[status]} pulse={live} />;
}

export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md bg-surface-3 px-1.5 py-0.5 font-mono text-[11px] text-fg-muted ring-1 ring-inset ring-line",
        className,
      )}
    >
      {children}
    </span>
  );
}

/* ----------------------------------------------------------------- forms */

export const inputClass =
  "w-full rounded-lg bg-bg px-3 py-2 text-sm ring-1 ring-inset ring-line outline-none transition placeholder:text-fg-subtle focus:ring-brand/50 disabled:opacity-60";

export function Field({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium text-fg-muted">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1.5 text-[11px] text-fg-subtle">{hint}</p>}
    </div>
  );
}

/* --------------------------------------------------------------- buttons */

const buttonVariant = {
  primary:
    "bg-brand-gradient text-white shadow-[0_6px_20px_-8px_var(--brand)] hover:brightness-110 active:brightness-95",
  secondary: "bg-surface-2 text-fg ring-1 ring-inset ring-line-strong hover:bg-surface-3",
  ghost: "text-fg-muted hover:bg-surface-2 hover:text-fg",
} as const;
const buttonSize = { sm: "h-7 px-2.5 text-xs", md: "h-8.5 px-3.5 text-sm" } as const;

type ButtonStyle = { variant?: keyof typeof buttonVariant; size?: keyof typeof buttonSize; icon?: LucideIcon };

function buttonClass(variant: keyof typeof buttonVariant, size: keyof typeof buttonSize, extra?: string) {
  return cn(
    "inline-flex select-none items-center justify-center gap-1.5 rounded-lg font-medium transition outline-none",
    "focus-visible:ring-2 focus-visible:ring-brand/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
    "disabled:pointer-events-none disabled:opacity-50",
    buttonVariant[variant],
    buttonSize[size],
    extra,
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  icon: Icon,
  className,
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & ButtonStyle) {
  return (
    <button type="button" {...props} className={buttonClass(variant, size, className)}>
      {Icon && <Icon className="size-4" strokeWidth={2} aria-hidden="true" />}
      {children}
    </button>
  );
}

export function ButtonLink({
  to,
  variant = "secondary",
  size = "md",
  icon: Icon,
  children,
}: ButtonStyle & { to: string; children: ReactNode }) {
  return (
    <Link to={to} className={buttonClass(variant, size)}>
      {Icon && <Icon className="size-4" strokeWidth={2} aria-hidden="true" />}
      {children}
    </Link>
  );
}

/* ---------------------------------------------------------------- layout */

export function PageHeader({
  title,
  subtitle,
  actions,
  crumbs,
  icon: Icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  crumbs?: { to: string; label: string }[];
  icon?: LucideIcon;
}) {
  return (
    <header className="mb-7 animate-fade-in">
      {crumbs && (
        <nav aria-label="Breadcrumb" className="mb-3 -ml-1 flex items-center gap-1 text-xs text-fg-muted">
          {crumbs.map((c, i) => (
            <span key={c.to} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="size-3 text-fg-subtle" aria-hidden="true" />}
              <Link to={c.to} className="rounded px-1 py-0.5 hover:bg-surface-2 hover:text-fg">
                {c.label}
              </Link>
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          {Icon && (
            <div className="mt-0.5 grid size-10 shrink-0 place-items-center rounded-xl bg-surface-2 ring-1 ring-line-strong">
              <Icon className="size-5 text-fg-muted" aria-hidden="true" />
            </div>
          )}
          <div className="min-w-0">
            <h1 className="truncate text-[22px] font-semibold tracking-[-0.02em]">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-fg-muted">{subtitle}</p>}
          </div>
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function Card({
  title,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  icon?: LucideIcon;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    // min-w-0: grid/flex children may shrink below their content (wide tables scroll instead).
    <section className={cn("card flex min-w-0 animate-slide-up flex-col", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            {Icon && <Icon className="size-4 text-fg-subtle" aria-hidden="true" />}
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      {Icon && (
        <div className="mb-4 grid size-12 place-items-center rounded-2xl bg-surface-2 ring-1 ring-line-strong">
          <Icon className="size-5 text-fg-muted" aria-hidden="true" />
        </div>
      )}
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1.5 max-w-sm text-sm text-fg-muted">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, className = "m-4" }: { error: unknown; className?: string }) {
  const message = error instanceof Error ? error.message : "Unexpected error.";
  return (
    <div role="alert" className={cn(className, "flex items-start gap-3 rounded-xl bg-bad/8 px-4 py-3 text-sm ring-1 ring-inset ring-bad/25")}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden="true" />
      <p className="text-fg">{message}</p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "animate-shimmer rounded-md bg-[linear-gradient(90deg,var(--surface-2),var(--surface-3),var(--surface-2))] bg-[length:200%_100%]",
        className,
      )}
    />
  );
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-3 p-5">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-5" />
      ))}
    </div>
  );
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-[11px] font-medium uppercase tracking-wider text-fg-subtle">
            {head.map((h, i) => (
              <th key={`${h}-${i}`} scope="col" className="px-5 py-2.5 font-medium whitespace-nowrap">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  );
}

export function Pager({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const end = Math.min(offset + limit, total);
  return (
    <div className="flex items-center justify-between border-t border-line px-5 py-3 text-xs text-fg-muted">
      <span className="tabular-nums">
        {offset + 1}–{end} of {total}
      </span>
      <div className="flex gap-2">
        <Button size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button size="sm" disabled={end >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- misc */

export function Meter({
  value,
  label,
  detail,
}: {
  value: number | null | undefined;
  label: string;
  detail?: string;
}) {
  const pct = value === null || value === undefined ? null : Math.max(0, Math.min(1, value));
  const color = pct === null ? "bg-fg-subtle" : pct > 0.9 ? "bg-bad" : pct > 0.7 ? "bg-warn" : "bg-ok";
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-baseline justify-between gap-2 text-[11px]">
        <span className="shrink-0 text-fg-muted">{label}</span>
        <span className="truncate font-mono tabular-nums text-fg-muted">{detail ?? (pct === null ? "—" : `${Math.round(pct * 100)}%`)}</span>
      </div>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-surface-3"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct === null ? undefined : Math.round(pct * 100)}
      >
        <div className={cn("h-full rounded-full transition-[width] duration-500", color)} style={{ width: `${(pct ?? 0) * 100}%` }} />
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line-strong bg-surface-2 px-1.5 py-px font-mono text-[10px] text-fg-muted">
      {children}
    </kbd>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied" : label}
      title={label}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="grid size-7 place-items-center rounded-md text-fg-muted transition hover:bg-surface-3 hover:text-fg"
    >
      {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
    </button>
  );
}

export function CodeBlock({ code, label }: { code: string; label?: string }) {
  return (
    <div className="group relative overflow-hidden rounded-xl bg-bg ring-1 ring-line-strong">
      {label && (
        <div className="flex items-center justify-between border-b border-line px-3.5 py-1.5 text-[11px] text-fg-subtle">
          <span>{label}</span>
        </div>
      )}
      <pre className="overflow-x-auto px-3.5 py-3 pr-12 font-mono text-[12.5px] leading-5 text-fg">{code}</pre>
      <div className={cn("absolute right-2", label ? "top-9" : "top-2")}>
        <CopyButton text={code} />
      </div>
    </div>
  );
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-line px-3">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          type="button"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "relative -mb-px flex items-center gap-1.5 px-2.5 py-2.5 text-sm transition",
            value === t.value ? "text-fg" : "text-fg-muted hover:text-fg",
          )}
        >
          {t.label}
          {t.count !== undefined && (
            <span className="rounded-full bg-surface-3 px-1.5 text-[10px] tabular-nums text-fg-muted">{t.count}</span>
          )}
          {value === t.value && <span className="absolute inset-x-1 -bottom-px h-0.5 rounded-full bg-brand-gradient" />}
        </button>
      ))}
    </div>
  );
}
