import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SystemInfo } from "../lib/api";
import { SystemStatus } from "./SystemStatus";

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemStatus />
    </QueryClientProvider>,
  );
}

function mockFetch(impl: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

const healthy: SystemInfo = {
  name: "torqrun",
  version: "0.1.0.dev0",
  edition: "community",
  features: [],
  license: null,
  environment: "development",
  database: { status: "ok", schema_revision: "0001_baseline", expected_revision: "0001_baseline" },
};

afterEach(() => vi.unstubAllGlobals());

describe("SystemStatus", () => {
  it("shows version and healthy database", async () => {
    mockFetch(async () => new Response(JSON.stringify(healthy), { status: 200 }));
    renderWithClient();
    expect(await screen.findByText("0.1.0.dev0")).toBeInTheDocument();
    expect(screen.getByText("Operational")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("0001_baseline")).toBeInTheDocument();
  });

  it("flags pending migrations", async () => {
    const pending: SystemInfo = {
      ...healthy,
      database: { status: "migrations_pending", schema_revision: null, expected_revision: "0001_baseline" },
    };
    mockFetch(async () => new Response(JSON.stringify(pending), { status: 200 }));
    renderWithClient();
    expect(await screen.findByText("Migrations pending")).toBeInTheDocument();
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/expected 0001_baseline/)).toBeInTheDocument();
  });

  it("explains when the API cannot be reached", async () => {
    mockFetch(async () => {
      throw new TypeError("network down");
    });
    renderWithClient();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the Torqrun API.");
    expect(screen.getByText("API offline")).toBeInTheDocument();
  });
});
