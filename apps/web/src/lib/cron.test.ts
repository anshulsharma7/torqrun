import { describe, expect, it } from "vitest";

import { describeCron, describeInterval } from "./cron";

describe("describeCron", () => {
  it.each([
    ["* * * * *", "Every minute"],
    ["*/15 * * * *", "Every 15 minutes"],
    ["5 * * * *", "Every hour at :05"],
    ["0 */6 * * *", "Every 6 hours at :00"],
    ["0 2 * * *", "Every day at 02:00"],
    ["30 9 * * 1-5", "Weekdays at 09:30"],
    ["0 8 * * 1", "Every Monday at 08:00"],
    ["0 0 15 * *", "Monthly on day 15 at 00:00"],
    ["@daily", "Every day at 00:00"],
    ["7 3 */2 * 1,3", "7 3 */2 * 1,3"],
  ])("%s -> %s", (expr, text) => expect(describeCron(expr)).toBe(text));
});

describe("describeInterval", () => {
  it.each([
    [30, "Every 30 seconds"],
    [300, "Every 5 minutes"],
    [3600, "Every hour"],
    [7200, "Every 2 hours"],
    [86400, "Every day"],
  ])("%s -> %s", (s, text) => expect(describeInterval(s)).toBe(text));
});
