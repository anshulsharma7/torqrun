import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { LogChunk } from "../lib/api";
import { LogViewer } from "./LogViewer";

const chunks: LogChunk[] = [
  { seq: 0, stream: "stdout", ts: "2026-01-01T10:00:00.123Z", data: "hello\n" },
  { seq: 1, stream: "stderr", ts: "2026-01-01T10:00:00.456Z", data: "oops\n" },
  { seq: 2, stream: "stdout", ts: "2026-01-01T10:00:01.000Z", data: "<script>alert(1)</script>\n" },
];

describe("LogViewer", () => {
  it("renders lines in order and counts them", () => {
    render(<LogViewer chunks={chunks} live={false} downloadUrl="/dl" />);
    const log = screen.getByRole("log");
    expect(log.textContent).toMatch(/hello.*oops/s);
    expect(screen.getByText("3 lines")).toBeInTheDocument();
  });

  it("renders output as text, never as HTML", () => {
    const { container } = render(<LogViewer chunks={chunks} live={false} downloadUrl="/dl" />);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
  });

  it("can filter to stderr", () => {
    render(<LogViewer chunks={chunks} live={false} downloadUrl="/dl" />);
    fireEvent.click(screen.getByLabelText("stderr only"));
    expect(screen.queryByText("hello")).toBeNull();
    expect(screen.getByText("oops")).toBeInTheDocument();
  });

  it("shows a waiting message while live with no output", () => {
    render(<LogViewer chunks={[]} live downloadUrl="/dl" />);
    expect(screen.getByText("Waiting for output…")).toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
  });
});

describe("LogViewer filter", () => {
  it("filters lines by text and highlights matches", () => {
    render(<LogViewer chunks={chunks} live={false} downloadUrl="/dl" />);
    fireEvent.change(screen.getByLabelText("Filter output"), { target: { value: "oop" } });
    expect(screen.queryByText("hello")).toBeNull();
    expect(screen.getByText("oop").tagName).toBe("MARK");
  });
});
