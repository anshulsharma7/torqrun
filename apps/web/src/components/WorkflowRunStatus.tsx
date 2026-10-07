import type { WorkflowRunStatus } from "../lib/api";
import { StatusPill } from "./ui";

const tone = { RUNNING: "info", SUCCEEDED: "ok", FAILED: "bad", CANCELLED: "neutral" } as const;
const label = { RUNNING: "Running", SUCCEEDED: "Succeeded", FAILED: "Failed", CANCELLED: "Cancelled" } as const;

export function WorkflowRunBadge({ status }: { status: WorkflowRunStatus }) {
  return <StatusPill tone={tone[status]} label={label[status]} pulse={status === "RUNNING"} />;
}
