import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { Compass, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import { AppShell } from "./components/AppShell";
import { LogoMark } from "./components/Logo";
import { FeatureGate, UpgradeCard } from "./components/Upgrade";
import { ButtonLink, EmptyState } from "./components/ui";
import { AuthGate, useCan } from "./lib/auth";
import { PAID_FEATURES, type PaidFeature } from "./lib/edition";
import { extension } from "./lib/extensions";
import { AccountPage } from "./pages/AccountPage";
import { AgentDetailPage } from "./pages/AgentDetailPage";
import { FleetPage } from "./pages/FleetPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobFormPage } from "./pages/JobFormPage";
import { JobsPage } from "./pages/JobsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlansPage } from "./pages/PlansPage";
import { QueuesPage } from "./pages/QueuesPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";
import { SchedulesPage } from "./pages/SchedulesPage";
import { SignInPage } from "./pages/SignInPage";
import { WorkflowDetailPage } from "./pages/WorkflowDetailPage";
import { WorkflowEditorPage } from "./pages/WorkflowEditorPage";
import { WorkflowRunPage } from "./pages/WorkflowRunPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";

function AdminOnly({ children }: { children: ReactNode }) {
  const admin = useCan("admin");
  if (admin) return <>{children}</>;
  return (
    <EmptyState icon={ShieldAlert} title="Admins only" action={<ButtonLink to="/">Back to overview</ButtonLink>}>
      Ask an administrator if you need access to this page.
    </EmptyState>
  );
}

function Splash() {
  return (
    <div className="grid min-h-screen place-items-center" role="status" aria-label="Loading">
      <LogoMark className="size-10 animate-pulse" />
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthGate loading={<Splash />} signedOut={(setup) => <SignInPage setup={setup} />}>
        <Shell />
      </AuthGate>
    </BrowserRouter>
  );
}

function Shell() {
  return (
      <AppShell>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/new" element={<JobFormPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/jobs/:jobId/edit" element={<JobFormPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/workflows/new" element={<WorkflowEditorPage />} />
          <Route path="/workflows/:workflowId" element={<WorkflowDetailPage />} />
          <Route path="/workflows/:workflowId/edit" element={<WorkflowEditorPage />} />
          <Route path="/workflow-runs/:runId" element={<WorkflowRunPage />} />
          <Route path="/schedules" element={<SchedulesPage />} />
          <Route path="/queues" element={<QueuesPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="/fleet/:agentId" element={<AgentDetailPage />} />
          <Route path="/agents" element={<Navigate to="/fleet" replace />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/plans" element={<PlansPage />} />
          {/* Paid pages: from src/ee when present, otherwise an upgrade card at the same URL. */}
          {(Object.keys(PAID_FEATURES) as PaidFeature[]).map((feature) => {
            const page = extension.pages.find((p) => p.feature === feature);
            return (
              <Route
                key={feature}
                path={PAID_FEATURES[feature].path}
                element={
                  <AdminOnly>
                    {page ? <FeatureGate feature={feature}>{page.element}</FeatureGate> : <UpgradeCard feature={feature} />}
                  </AdminOnly>
                }
              />
            );
          })}
          <Route
            path="*"
            element={
              <EmptyState icon={Compass} title="Page not found" action={<ButtonLink to="/">Back to overview</ButtonLink>} />
            }
          />
        </Routes>
      </AppShell>
  );
}
