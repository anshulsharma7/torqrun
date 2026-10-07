import { useQuery } from "@tanstack/react-query";

import { api, type Role } from "./api";

// Edition, paid features and commercial contact. The open-source build is the Community Edition;
// Team/Enterprise features come from a separate package (src/ee) and a license key.

export const EDITION = "Community";
export const SALES_EMAIL = "anshulshrm12@gmail.com";
export const REPO_URL = "https://github.com/anshulsharma7/torqrun";

export function contactUrl(plan: "Team" | "Enterprise" | "Support", detail = ""): string {
  const subject = `Torqrun ${plan} plan`;
  const body = [
    `Hi, I'm interested in the Torqrun ${plan} plan.`,
    "",
    "Company:",
    "Number of agents / jobs per day (approx.):",
    "What we need (managed hosting, support, SSO, …):",
    detail,
  ].join("\n");
  return `mailto:${SALES_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export type PaidFeature = "users" | "secrets" | "audit" | "notifications";

export const PAID_FEATURES: Record<PaidFeature, { label: string; path: string }> = {
  users: { label: "Users", path: "/users" },
  secrets: { label: "Secrets", path: "/secrets" },
  audit: { label: "Audit log", path: "/audit" },
  notifications: { label: "Notifications", path: "/notifications" },
};

export const ROLE_HELP: Record<Role, string> = {
  viewer: "Read-only: jobs, runs, logs, workflows, fleet.",
  operator: "Viewer + create, edit and run jobs, workflows and schedules.",
  admin: "Operator + users, secrets, agents and the audit log.",
};

/** Edition and licensed features, from /api/v1/system/info. */
export function useEdition() {
  const info = useQuery({ queryKey: ["system-info"], queryFn: ({ signal }) => api.systemInfo(signal), staleTime: 30_000 });
  const features = new Set(info.data?.features ?? []);
  return {
    loaded: info.isSuccess,
    edition: info.data?.edition ?? "community",
    license: info.data?.license ?? null,
    has: (f: PaidFeature) => features.has(f),
  };
}
