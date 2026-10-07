import { Building2, Check, Mail, Minus, Server, Sparkles, Users, type LucideIcon } from "lucide-react";

import { Badge, Card, cn, PageHeader } from "../components/ui";
import { contactUrl, EDITION, REPO_URL, SALES_EMAIL, useEdition } from "../lib/edition";

type Availability = true | false | "service" | "roadmap";

interface Plan {
  name: string;
  icon: LucideIcon;
  tagline: string;
  price: string;
  priceNote: string;
  highlights: string[];
  cta: { label: string; href: string };
  key: "community" | "team" | "enterprise";
  featured?: boolean;
}

const PLANS: Plan[] = [
  {
    name: "Community",
    icon: Server,
    tagline: "Self-hosted, open source orchestration for one operator.",
    price: "Free",
    priceNote: "Apache-2.0, forever",
    highlights: [
      "Unlimited jobs, runs and agents",
      "Schedules, workflows (DAGs), retries, concurrency limits",
      "Process and Docker executors, artifacts, live logs",
      "One admin account with API tokens",
      "Metrics, backups, Docker Compose deployment",
      "Community support on GitHub",
    ],
    cta: { label: "Free on GitHub", href: REPO_URL },
    key: "community",
  },
  {
    name: "Team",
    icon: Users,
    tagline: "For teams: collaboration, security and alerts, self-hosted or managed by us.",
    price: "Contact us",
    priceNote: "Monthly, by usage",
    highlights: [
      "Everything in Community",
      "Multiple users with viewer / operator / admin roles",
      "Secrets manager (encrypted, masked in logs)",
      "Audit log of every change and sign-in",
      "Slack, Teams, email and webhook alerts",
      "Managed hosting option, priority email support",
    ],
    cta: { label: "Contact sales", href: contactUrl("Team") },
    key: "team",
    featured: true,
  },
  {
    name: "Enterprise",
    icon: Building2,
    tagline: "For organisations with security, compliance and scale requirements.",
    price: "Contact us",
    priceNote: "Annual agreement",
    highlights: [
      "Everything in Team",
      "SSO (SAML / OIDC) and SCIM provisioning*",
      "Fine-grained, per-project permissions*",
      "Audit log export to your SIEM*",
      "SLA-backed support, dedicated contact",
      "On-premises / air-gapped deployment help, custom terms",
    ],
    cta: { label: "Talk to us", href: contactUrl("Enterprise") },
    key: "enterprise",
  },
];

const ROWS: { group: string; items: [string, Availability, Availability, Availability][] }[] = [
  {
    group: "Orchestration",
    items: [
      ["Python & shell jobs, versioned", true, true, true],
      ["Cron & interval schedules with time zones", true, true, true],
      ["Workflows (DAGs) with trigger rules", true, true, true],
      ["Retries, timeouts, cancel, concurrency limits", true, true, true],
      ["Remote agents (Linux installer, Docker)", true, true, true],
      ["Container executor (Docker)", true, true, true],
      ["Artifacts and live logs", true, true, true],
    ],
  },
  {
    group: "Security & governance",
    items: [
      ["Sign-in and API tokens (single admin)", true, true, true],
      ["Multiple users, viewer/operator/admin roles", false, true, true],
      ["Secrets manager (encrypted, masked in logs)", false, true, true],
      ["Audit log", false, true, true],
      ["SSO (SAML / OIDC), SCIM", false, false, "roadmap"],
      ["Per-project permissions", false, false, "roadmap"],
      ["Audit log export (SIEM)", false, false, "roadmap"],
    ],
  },
  {
    group: "Operations & support",
    items: [
      ["Self-hosting (Docker Compose)", true, true, true],
      ["Prometheus metrics, alert rules, backups", true, true, true],
      ["Slack, Teams, email & webhook notifications", false, true, true],
      ["Managed control plane", false, "service", "service"],
      ["Upgrades & backups done for you", false, "service", "service"],
      ["Support", false, "service", "service"],
      ["SLA, dedicated contact, custom terms", false, false, "service"],
    ],
  },
];

function Cell({ value }: { value: Availability }) {
  if (value === true) return <Check className="mx-auto size-4 text-ok" aria-label="Included" />;
  if (value === false) return <Minus className="mx-auto size-4 text-fg-subtle" aria-label="Not included" />;
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset", value === "service" ? "bg-info/10 text-info ring-info/25" : "bg-warn/10 text-warn ring-warn/25")}>
      {value === "service" ? "Service" : "On request"}
    </span>
  );
}

export function PlansPage() {
  const edition = useEdition();
  const current = edition.edition;
  return (
    <>
      <PageHeader
        icon={Sparkles}
        title="Plans"
        subtitle={
          <>
            {current === "community" ? (
              <>
                You're running the <strong className="text-fg">{EDITION} Edition</strong>: free and open source. Team and Enterprise add multi-user access, secrets, audit and alerts.
              </>
            ) : (
              <>
                Licensed to <strong className="text-fg">{edition.license?.customer}</strong>: <span className="capitalize">{current}</span> plan, {edition.license?.seats} users, until{" "}
                {edition.license?.expires_at}
                {edition.license?.state === "grace" && <strong className="text-warn"> (expired: renew to keep these features)</strong>}.
              </>
            )}
          </>
        }
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {PLANS.map((p) => (
          <Card key={p.name} className={cn(p.featured && "ring-2 ring-brand/50")} bodyClassName="flex h-full flex-col p-6">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-semibold">
                <p.icon className="size-4 text-fg-muted" aria-hidden="true" />
                {p.name}
              </span>
              {p.key === current && <Badge className="text-ok">Current plan</Badge>}
              {p.featured && <Badge className="text-brand">Most popular</Badge>}
            </div>
            <p className="mt-4 text-3xl font-semibold tracking-tight">{p.price}</p>
            <p className="text-xs text-fg-subtle">{p.priceNote}</p>
            <p className="mt-3 text-sm text-fg-muted">{p.tagline}</p>
            <ul className="mt-5 flex-1 space-y-2 text-sm">
              {p.highlights.map((h) => (
                <li key={h} className="flex gap-2">
                  <Check className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden="true" />
                  {h}
                </li>
              ))}
            </ul>
            <a
              href={p.cta.href}
              target={p.key === "community" ? "_blank" : undefined}
              rel="noreferrer"
              className={cn(
                "mt-6 inline-flex h-9 items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition",
                p.key === "community" ? "bg-surface-2 text-fg-muted ring-1 ring-inset ring-line" : "bg-brand-gradient text-white hover:brightness-110",
              )}
            >
              {p.key !== "community" && <Mail className="size-4" aria-hidden="true" />}
              {p.cta.label}
            </a>
          </Card>
        ))}
      </div>
      <p className="mt-3 text-xs text-fg-subtle">* On the roadmap; delivered for Enterprise customers on request. Everything else listed is available today. Team and Enterprise run self-hosted with a license key, or managed by us.</p>

      <Card title="Compare plans" className="mt-8">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-fg-muted">
                <th className="px-5 py-3 text-left font-medium">Feature</th>
                {PLANS.map((p) => (
                  <th key={p.name} className="w-32 px-3 py-3 text-center font-medium">{p.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map((g) => [
                <tr key={g.group} className="bg-surface-2/40">
                  <td colSpan={4} className="px-5 py-2 text-[11px] font-semibold uppercase tracking-wider text-fg-subtle">{g.group}</td>
                </tr>,
                ...g.items.map(([label, ...vals]) => (
                  <tr key={label} className="border-b border-line last:border-0">
                    <td className="px-5 py-2.5">{label}</td>
                    {vals.map((v, i) => (
                      <td key={i} className="px-3 py-2.5 text-center">
                        <Cell value={v} />
                      </td>
                    ))}
                  </tr>
                )),
              ])}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="mt-8" bodyClassName="flex flex-wrap items-center justify-between gap-4 p-6">
        <div>
          <p className="font-medium">Questions, a custom quote, or help self-hosting?</p>
          <p className="mt-1 text-sm text-fg-muted">
            Write to <a className="text-brand hover:underline" href={`mailto:${SALES_EMAIL}`}>{SALES_EMAIL}</a>. Bugs and feature ideas are welcome on{" "}
            <a className="text-brand hover:underline" href={`${REPO_URL}/issues`} target="_blank" rel="noreferrer">GitHub</a>.
          </p>
        </div>
        <a href={contactUrl("Support")} className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand-gradient px-4 text-sm font-medium text-white hover:brightness-110">
          <Mail className="size-4" aria-hidden="true" /> Contact us
        </a>
      </Card>
    </>
  );
}
