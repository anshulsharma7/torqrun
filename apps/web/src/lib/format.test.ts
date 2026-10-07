import { describe, expect, it } from "vitest";

import { parseEnv } from "../pages/JobFormPage";
import { formatBytes, formatDuration, shortId } from "./format";

describe("formatDuration", () => {
  it.each([
    [null, "—"],
    [0.0421, "42 ms"],
    [3.456, "3.46 s"],
    [42.31, "42.3 s"],
    [125, "2m 5s"],
    [3725, "1h 2m"],
  ])("%s -> %s", (input, expected) => {
    expect(formatDuration(input)).toBe(expected);
  });
});

describe("formatBytes", () => {
  it("scales units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KiB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MiB");
  });
});

describe("parseEnv", () => {
  it("parses NAME=value lines, keeping '=' in values and skipping comments", () => {
    expect(parseEnv("A=1\n# note\n\nURL=postgres://x?a=b\n")).toEqual({ A: "1", URL: "postgres://x?a=b" });
  });

  it("rejects lines without a name", () => {
    expect(() => parseEnv("=oops")).toThrow(/NAME=value/);
    expect(() => parseEnv("novalue")).toThrow(/NAME=value/);
  });
});

it("shortId keeps the time-random tail", () => {
  expect(shortId("01a11098-9208-72ae-8a30-a5fc9a935f91")).toBe("9a935f91");
});
