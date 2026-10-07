import { Lock, Mail, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { contactUrl, PAID_FEATURES, useEdition, type PaidFeature } from "../lib/edition";
import { ButtonLink, Card, EmptyState } from "./ui";

const DESCRIPTION: Record<PaidFeature, string> = {
  users: "Invite your team with viewer, operator and admin roles, each with their own API tokens.",
  secrets: "Store passwords and API keys encrypted, inject them into jobs at run time and mask them in logs.",
  audit: "See who created, changed, ran or deleted what, and every sign-in attempt.",
  notifications: "Get Slack, Teams, email or webhook alerts when runs fail or agents go offline.",
};

/** Shown instead of a paid page when the feature isn't licensed. */
export function UpgradeCard({ feature }: { feature: PaidFeature }) {
  return (
    <Card>
      <EmptyState
        icon={Lock}
        title={`${PAID_FEATURES[feature].label} is a Team & Enterprise feature`}
        action={
          <div className="flex flex-wrap justify-center gap-2">
            <ButtonLink to="/plans" variant="primary" icon={Sparkles}>
              See plans
            </ButtonLink>
            <a
              href={contactUrl("Team", `Interested in: ${PAID_FEATURES[feature].label}`)}
              className="inline-flex h-8.5 items-center gap-1.5 rounded-lg px-3.5 text-sm font-medium text-fg-muted ring-1 ring-inset ring-line-strong hover:text-fg"
            >
              <Mail className="size-4" aria-hidden="true" /> Contact us
            </a>
          </div>
        }
      >
        {DESCRIPTION[feature]}
      </EmptyState>
    </Card>
  );
}

/** Renders the page if its feature is licensed, otherwise the upgrade card. */
export function FeatureGate({ feature, children }: { feature: PaidFeature; children: ReactNode }) {
  const edition = useEdition();
  if (!edition.loaded) return null;
  return edition.has(feature) ? <>{children}</> : <UpgradeCard feature={feature} />;
}

/** A sidebar entry for a feature the current edition doesn't include. */
export function LockedNavItem({ feature }: { feature: PaidFeature }) {
  return (
    <Link
      to={PAID_FEATURES[feature].path}
      className="group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm whitespace-nowrap text-fg-subtle transition hover:bg-surface-2/60 hover:text-fg-muted"
      title="Team & Enterprise"
    >
      <Lock className="size-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
      <span className="flex-1">{PAID_FEATURES[feature].label}</span>
      <span className="rounded-full bg-brand/10 px-1.5 text-[10px] font-medium text-brand">Team</span>
    </Link>
  );
}
