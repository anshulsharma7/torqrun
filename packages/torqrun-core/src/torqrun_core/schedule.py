"""Cron and interval schedules: parsing, next fire times, DST and missed-fire policy.

Pure functions, no I/O. All public functions take and return timezone-aware UTC datetimes.

Cron syntax: the standard five fields ``minute hour day-of-month month day-of-week`` with
``*``, ``a-b``, ``*/n``, ``a-b/n``, lists (``1,15``), month and weekday names (``jan``,
``mon-fri``), ``7`` as Sunday, and the aliases ``@hourly @daily @weekly @monthly @yearly``.
As in Vixie cron, when both day-of-month and day-of-week are restricted a day matches if
*either* does.

Daylight saving time (cron fields are evaluated in the schedule's time zone):

* a local time that doesn't exist (spring forward) fires at the first valid instant after
  the gap, e.g. 02:30 on the change day in Europe/Berlin fires at 03:00;
* a local time that happens twice (fall back) fires once, at its first occurrence.
"""

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MisfirePolicy = Literal["skip", "run_once", "run_all"]

ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
# (name, min, max, names)
FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, MONTHS),
    ("day of week", 0, 7, DAYS),
]
SEARCH_LIMIT_DAYS = 366 * 5  # e.g. "0 0 29 2 *" fires only in leap years


class ScheduleError(ValueError):
    """Invalid cron expression, interval or time zone."""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    dom_restricted: bool
    dow_restricted: bool

    def day_matches(self, d: datetime) -> bool:
        dom = d.day in self.days
        dow = (d.isoweekday() % 7) in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow


def _value(token: str, lo: int, hi: int, names: dict[str, int], field: str) -> int:
    token = token.lower()
    if token in names:
        return names[token]
    if not token.isdigit():
        raise ScheduleError(f"invalid {field} value {token!r}")
    v = int(token)
    if not lo <= v <= hi:
        raise ScheduleError(f"{field} value {v} out of range {lo}-{hi}")
    return v


def _field(spec: str, lo: int, hi: int, names: dict[str, int], field: str) -> frozenset[int]:
    out: set[int] = set()
    for part in spec.split(","):
        if not part:
            raise ScheduleError(f"empty list item in {field}")
        rng, _, step_s = part.partition("/")
        step = 1
        if step_s:
            if not step_s.isdigit() or int(step_s) == 0:
                raise ScheduleError(f"invalid step {step_s!r} in {field}")
            step = int(step_s)
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = _value(a, lo, hi, names, field), _value(b, lo, hi, names, field)
            if start > end:
                raise ScheduleError(f"range {rng!r} in {field} is backwards")
        else:
            start = _value(rng, lo, hi, names, field)
            end = hi if step_s else start
        out.update(range(start, end + 1, step))
    return frozenset(out)


def parse_cron(expr: str) -> Cron:
    text = ALIASES.get(expr.strip().lower(), expr.strip())
    parts = text.split()
    if len(parts) != 5:
        raise ScheduleError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    values = [
        _field(p, lo, hi, names, name)
        for p, (name, lo, hi, names) in zip(parts, FIELDS, strict=True)
    ]
    weekdays = frozenset(d % 7 for d in values[4])  # 7 is also Sunday
    return Cron(
        minutes=values[0],
        hours=values[1],
        days=values[2],
        months=values[3],
        weekdays=weekdays,
        dom_restricted=parts[2] != "*",
        dow_restricted=parts[4] != "*",
    )


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown time zone {name!r}") from None


def _next_local(cron: Cron, after: datetime) -> datetime:
    """Next naive local wall-clock time strictly after ``after`` that matches ``cron``."""
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=SEARCH_LIMIT_DAYS)
    while t <= limit:
        if t.month not in cron.months:
            t = (t.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
            continue
        if not cron.day_matches(t):
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in cron.hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute not in cron.minutes:
            t += timedelta(minutes=1)
            continue
        return t
    raise ScheduleError("cron expression never fires (e.g. 30 February)")


def _to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    """Resolve a naive local time: first occurrence if repeated, next valid instant if skipped."""
    before = local.replace(tzinfo=tz, fold=0).astimezone(UTC)
    if before.astimezone(tz).replace(tzinfo=None) == local:
        return before  # exists (fold=0 = first occurrence when the hour repeats)
    # In the spring-forward gap. fold=1/fold=0 bracket the transition; it is at most a few
    # hours wide, so walk it by the minute to find the first instant after the gap.
    lo = local.replace(tzinfo=tz, fold=1).astimezone(UTC)
    t = min(lo, before)
    end = max(lo, before)
    while t <= end:
        if t.astimezone(tz).replace(tzinfo=None) > local:
            return t
        t += timedelta(minutes=1)
    return end


def next_cron_fire(expr: str | Cron, tz_name: str, after: datetime) -> datetime:
    """First fire time (UTC) strictly after ``after`` (UTC)."""
    cron = parse_cron(expr) if isinstance(expr, str) else expr
    tz = zone(tz_name)
    local = after.astimezone(tz).replace(tzinfo=None)
    while True:
        candidate = _next_local(cron, local)
        fire = _to_utc(candidate, tz)
        if fire > after:
            return fire
        local = candidate  # repeated hour: this local time already fired (first occurrence)


def next_interval_fire(interval_seconds: int, anchor: datetime, after: datetime) -> datetime:
    """Next ``anchor + k * interval`` strictly after ``after``."""
    if interval_seconds < 1:
        raise ScheduleError("interval must be at least 1 second")
    if after < anchor:
        return anchor
    k = int((after - anchor).total_seconds() // interval_seconds) + 1
    return anchor + timedelta(seconds=k * interval_seconds)


@dataclass(frozen=True)
class ScheduleSpec:
    kind: Literal["cron", "interval"]
    cron: str | None = None
    interval_seconds: int | None = None
    timezone: str = "UTC"
    anchor: datetime | None = None  # interval schedules: phase reference

    def validate(self) -> None:
        if self.kind == "cron":
            if not self.cron:
                raise ScheduleError("cron expression required")
            parse_cron(self.cron)
            zone(self.timezone)
        elif not self.interval_seconds or self.interval_seconds < 1:
            raise ScheduleError("interval must be at least 1 second")

    def next_after(self, after: datetime) -> datetime:
        if self.kind == "cron":
            assert self.cron is not None  # noqa: S101 - guaranteed by validate()
            return next_cron_fire(self.cron, self.timezone, after)
        assert self.interval_seconds is not None  # noqa: S101
        return next_interval_fire(self.interval_seconds, self.anchor or after, after)

    def upcoming(self, after: datetime, count: int) -> list[datetime]:
        out: list[datetime] = []
        t = after
        for _ in range(count):
            t = self.next_after(t)
            out.append(t)
        return out


@dataclass(frozen=True)
class DueFires:
    fire: list[datetime]  # scheduled_for values to create runs for (oldest first)
    skipped: int  # missed slots dropped by the policy (a lower bound for enormous backlogs)
    next_fire_at: datetime


def due_fires(
    spec: ScheduleSpec,
    next_fire_at: datetime,
    now: datetime,
    *,
    policy: MisfirePolicy,
    grace: timedelta,
    max_catchup: int,
) -> DueFires:
    """Which slots in ``[next_fire_at, now]`` to run, per the misfire policy.

    A slot within ``grace`` of now is on time and always runs. Older slots were missed (the
    scheduler was down or overloaded): ``skip`` drops them, ``run_once`` runs only the latest
    missed slot, ``run_all`` runs each (at most ``max_catchup`` most recent).
    """
    slots: list[datetime] = []
    t = next_fire_at
    while t <= now:
        slots.append(t)
        if len(slots) > MAX_SCAN:
            # Enormous backlog (down for months on a per-minute schedule). Only recent slots
            # can matter (on-time ones, or the newest missed ones), so restart from a window
            # just before now instead of walking millions of slots.
            missed_total = len(slots)
            t = spec.next_after(now - max(grace, RECENT_WINDOW))
            slots = []
            while t <= now:
                slots.append(t)
                t = spec.next_after(t)
            chosen = _choose(slots, now, policy, grace, max_catchup)
            return DueFires(
                fire=chosen, skipped=missed_total + len(slots) - len(chosen), next_fire_at=t
            )
        t = spec.next_after(t)
    chosen = _choose(slots, now, policy, grace, max_catchup)
    return DueFires(fire=chosen, skipped=len(slots) - len(chosen), next_fire_at=t)


MAX_SCAN = 10_000
RECENT_WINDOW = timedelta(days=1)


def _choose(
    slots: list[datetime], now: datetime, policy: MisfirePolicy, grace: timedelta, max_catchup: int
) -> list[datetime]:
    on_time = [s for s in slots if now - s <= grace]
    missed = [s for s in slots if now - s > grace]
    if policy == "skip":
        return on_time
    if policy == "run_once":
        return on_time or missed[-1:]
    return (missed + on_time)[-max_catchup:] if max_catchup > 0 else on_time
