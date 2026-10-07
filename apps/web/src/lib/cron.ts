// Human-friendly descriptions for common cron patterns (anything else is shown verbatim).

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const pad = (n: string) => n.padStart(2, "0");

export function describeCron(expr: string): string {
  const aliases: Record<string, string> = {
    "@hourly": "Every hour",
    "@daily": "Every day at 00:00",
    "@midnight": "Every day at 00:00",
    "@weekly": "Every Sunday at 00:00",
    "@monthly": "On the 1st of every month at 00:00",
    "@yearly": "Every year on 1 January",
    "@annually": "Every year on 1 January",
  };
  const e = expr.trim().toLowerCase();
  if (aliases[e]) return aliases[e]!;
  const parts = e.split(/\s+/);
  if (parts.length !== 5) return expr;
  const [m, h, dom, mon, dow] = parts as [string, string, string, string, string];
  const isNum = (x: string) => /^\d+$/.test(x);
  if (m === "*" && h === "*" && dom === "*" && mon === "*" && dow === "*") return "Every minute";
  const everyMin = /^\*\/(\d+)$/.exec(m);
  if (everyMin && h === "*" && dom === "*" && mon === "*" && dow === "*") return `Every ${everyMin[1]} minutes`;
  if (isNum(m) && h === "*" && dom === "*" && mon === "*" && dow === "*") return `Every hour at :${pad(m)}`;
  const everyHour = /^\*\/(\d+)$/.exec(h);
  if (isNum(m) && everyHour && dom === "*" && mon === "*" && dow === "*") return `Every ${everyHour[1]} hours at :${pad(m)}`;
  if (isNum(m) && isNum(h) && mon === "*") {
    const time = `${pad(h)}:${pad(m)}`;
    if (dom === "*" && dow === "*") return `Every day at ${time}`;
    if (dom === "*" && (dow === "1-5" || dow === "mon-fri")) return `Weekdays at ${time}`;
    if (dom === "*" && isNum(dow)) return `Every ${DAYS[Number(dow) % 7]} at ${time}`;
    if (isNum(dom) && dow === "*") return `Monthly on day ${dom} at ${time}`;
  }
  return expr;
}

export function describeInterval(seconds: number): string {
  const unit = (n: number, word: string) => (n === 1 ? `Every ${word}` : `Every ${n} ${word}s`);
  if (seconds % 86400 === 0) return unit(seconds / 86400, "day");
  if (seconds % 3600 === 0) return unit(seconds / 3600, "hour");
  if (seconds % 60 === 0) return unit(seconds / 60, "minute");
  return unit(seconds, "second");
}

export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Every 5 min", cron: "*/5 * * * *" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily 02:00", cron: "0 2 * * *" },
  { label: "Weekdays 09:00", cron: "0 9 * * 1-5" },
  { label: "Mondays 08:00", cron: "0 8 * * 1" },
  { label: "Monthly", cron: "0 0 1 * *" },
];

export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  const zones = intl.supportedValuesOf?.("timeZone") ?? [];
  return ["UTC", ...zones.filter((z) => z !== "UTC")];
}

export function localTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}
