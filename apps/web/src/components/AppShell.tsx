import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Boxes, CalendarClock, LayoutGrid, Layers, LogOut, Plus, Search, Server, Sparkles, UserRound, Workflow, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router";

import { api } from "../lib/api";
import { EDITION, PAID_FEATURES, useEdition, type PaidFeature } from "../lib/edition";
import { extension } from "../lib/extensions";
import { LockedNavItem } from "./Upgrade";
import { AUTH_KEY, Can, useMe } from "../lib/auth";
import { CommandPalette, useCommandPalette } from "./CommandPalette";
import { Wordmark } from "./Logo";
import { ButtonLink, cn, Dot, Kbd } from "./ui";

function NavItem({ to, label, icon: Icon, end, badge }: { to: string; label: string; icon: LucideIcon; end?: boolean; badge?: ReactNode }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        cn(
          "group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm whitespace-nowrap transition",
          isActive ? "bg-surface-2 text-fg ring-1 ring-inset ring-line" : "text-fg-muted hover:bg-surface-2/60 hover:text-fg",
        )
      }
    >
      <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      <span className="flex-1">{label}</span>
      {badge}
    </NavLink>
  );
}

function UserMenu() {
  const me = useMe();
  const queryClient = useQueryClient();
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: async () => {
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== AUTH_KEY[0] });
      await queryClient.invalidateQueries({ queryKey: AUTH_KEY });
    },
  });
  const initials = (me.name || me.email).split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
  return (
    <div className="flex items-center gap-1 rounded-xl bg-surface p-1.5 ring-1 ring-inset ring-line">
      <NavLink to="/account" className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg p-1 transition hover:bg-surface-2" title="Account and API tokens">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-brand-gradient text-[11px] font-semibold text-white">{initials}</span>
        <span className="min-w-0">
          <span className="block truncate text-xs font-medium">{me.name}</span>
          <span className="block truncate text-[10px] capitalize text-fg-subtle">{me.role}</span>
        </span>
      </NavLink>
      <button
        type="button"
        onClick={() => logout.mutate()}
        disabled={logout.isPending}
        aria-label="Sign out"
        title="Sign out"
        className="grid size-8 shrink-0 place-items-center rounded-lg text-fg-muted transition hover:bg-surface-2 hover:text-fg"
      >
        <LogOut className="size-4" />
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const palette = useCommandPalette();
  const edition = useEdition();
  const agents = useQuery({ queryKey: ["agents"], queryFn: api.listAgents, refetchInterval: 5000 });
  const running = useQuery({
    queryKey: ["runs", "count", "RUNNING"],
    queryFn: () => api.listRuns({ status: "RUNNING", limit: 1 }),
    select: (p) => p.total,
    refetchInterval: 3000,
  });
  const info = useQuery({ queryKey: ["system-info"], queryFn: ({ signal }) => api.systemInfo(signal), refetchInterval: 15000 });
  // Revoked agents can never come back, so they don't count toward the fleet size.
  const active = agents.data?.filter((a) => a.status !== "REVOKED") ?? [];
  const online = active.filter((a) => a.connected).length;
  const total = active.length;

  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <aside className="z-10 border-b border-line bg-bg-subtle/80 backdrop-blur md:sticky md:top-0 md:flex md:h-screen md:w-60 md:shrink-0 md:flex-col md:border-r md:border-b-0">
        <div className="flex h-14 items-center justify-between px-4">
          <Wordmark />
          <button
            type="button"
            onClick={() => palette.setOpen(true)}
            className="grid size-8 place-items-center rounded-lg text-fg-muted hover:bg-surface-2 md:hidden"
            aria-label="Search"
          >
            <Search className="size-4" />
          </button>
        </div>
        <div className="hidden px-3 pb-3 md:block">
          <button
            type="button"
            onClick={() => palette.setOpen(true)}
            className="flex w-full items-center gap-2 rounded-lg bg-surface px-2.5 py-1.5 text-sm text-fg-subtle ring-1 ring-inset ring-line transition hover:text-fg-muted hover:ring-line-strong"
          >
            <Search className="size-3.5" aria-hidden="true" />
            <span className="flex-1 text-left">Search…</span>
            <Kbd>⌘K</Kbd>
          </button>
        </div>
        <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:pb-0">
          <NavItem to="/" end label="Overview" icon={LayoutGrid} />
          <NavItem to="/jobs" label="Jobs" icon={Boxes} />
          <NavItem to="/workflows" label="Workflows" icon={Workflow} />
          <NavItem
            to="/runs"
            label="Runs"
            icon={Activity}
            badge={
              running.data ? (
                <span className="flex items-center gap-1 rounded-full bg-info/12 px-1.5 text-[10px] font-medium tabular-nums text-info">
                  <Dot tone="info" pulse className="size-1.5 [&>span]:size-1.5" />
                  {running.data}
                </span>
              ) : null
            }
          />
          <NavItem to="/schedules" label="Schedules" icon={CalendarClock} />
          <NavItem to="/queues" label="Queues" icon={Layers} />
          <NavItem
            to="/fleet"
            label="Fleet"
            icon={Server}
            badge={
              total ? (
                <span className="font-mono text-[10px] tabular-nums text-fg-subtle">
                  {online}/{total}
                </span>
              ) : null
            }
          />
          <Can role="admin">
            <p className="hidden px-2.5 pt-4 pb-1 text-[10px] font-medium uppercase tracking-wider text-fg-subtle md:block">Admin</p>
            {(Object.keys(PAID_FEATURES) as PaidFeature[]).map((feature) => {
              const page = extension.pages.find((p) => p.feature === feature);
              return page && edition.has(feature) ? (
                <NavItem key={feature} to={page.path} label={page.label} icon={page.icon} />
              ) : (
                <LockedNavItem key={feature} feature={feature} />
              );
            })}
          </Can>
          <span className="md:hidden">
            <NavItem to="/account" label="Account" icon={UserRound} />
          </span>
          <span className="md:hidden">
            <NavItem to="/plans" label="Plans" icon={Sparkles} />
          </span>
        </nav>

        <div className="mt-auto hidden space-y-3 p-3 md:block">
          <UserMenu />
          <NavLink to="/fleet" className="block rounded-xl bg-surface p-3 ring-1 ring-inset ring-line transition hover:ring-line-strong">
            <p className="text-[11px] font-medium uppercase tracking-wider text-fg-subtle">Fleet</p>
            <div className="mt-2 flex items-center gap-2 text-sm">
              <Dot tone={online ? "ok" : total ? "bad" : "neutral"} pulse={online > 0} />
              <span>
                {total === 0 ? "No agents yet" : `${online} of ${total} agent${total === 1 ? "" : "s"} online`}
              </span>
            </div>
          </NavLink>
          <NavLink
            to="/plans"
            className="flex items-center gap-2 rounded-xl bg-brand/8 px-3 py-2 text-xs ring-1 ring-inset ring-brand/25 transition hover:bg-brand/12"
          >
            <Sparkles className="size-3.5 text-brand" aria-hidden="true" />
            {edition.edition === "community" ? (
              <>
                <span className="flex-1 text-fg-muted">{EDITION} Edition</span>
                <span className="font-medium text-brand">Upgrade</span>
              </>
            ) : (
              <span className="flex-1 truncate text-fg-muted">
                <span className="font-medium capitalize text-brand">{edition.edition}</span> · {edition.license?.customer}
              </span>
            )}
          </NavLink>
          <div className="flex items-center justify-between px-1 text-[11px] text-fg-subtle">
            <span className="font-mono">v{info.data?.version ?? "…"}</span>
            {info.data && (
              <span className="rounded-full bg-warn/10 px-1.5 py-px text-warn ring-1 ring-inset ring-warn/20">
                {info.data.environment}
              </span>
            )}
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <div className="sticky top-0 z-[5] hidden h-14 items-center justify-end gap-2 border-b border-line bg-bg/70 px-8 backdrop-blur-md md:flex">
          <Can role="admin">
            <ButtonLink to="/fleet?connect=1" variant="ghost" size="sm" icon={Server}>
              Connect agent
            </ButtonLink>
          </Can>
          <Can role="operator">
            <ButtonLink to="/jobs/new" variant="primary" size="sm" icon={Plus}>
              New job
            </ButtonLink>
          </Can>
        </div>
        <main className="px-4 py-6 sm:px-8 sm:py-8">
          <div className="mx-auto max-w-7xl">{children}</div>
        </main>
      </div>
      <CommandPalette open={palette.open} onClose={() => palette.setOpen(false)} />
    </div>
  );
}
