from datetime import UTC, datetime, timedelta

import pytest

from torqrun_core.schedule import (
    MisfirePolicy,
    ScheduleError,
    ScheduleSpec,
    due_fires,
    next_cron_fire,
    next_interval_fire,
    parse_cron,
)


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("expr", "field", "expected"),
    [
        ("*/15 * * * *", "minutes", {0, 15, 30, 45}),
        ("0 9-17/4 * * *", "hours", {9, 13, 17}),
        ("0 0 1,15 * *", "days", {1, 15}),
        ("0 0 * jan-mar *", "months", {1, 2, 3}),
        ("0 0 * * mon-fri", "weekdays", {1, 2, 3, 4, 5}),
        ("0 0 * * 7", "weekdays", {0}),
        ("5/20 * * * *", "minutes", {5, 25, 45}),
    ],
)
def test_parse_fields(expr: str, field: str, expected: set[int]) -> None:
    assert getattr(parse_cron(expr), field) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "* * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "*/0 * * * *",
        "5-1 * * * *",
        "* * * foo *",
        "a b c d e",
        "1,,2 * * * *",
    ],
)
def test_invalid_expressions_are_rejected(bad: str) -> None:
    with pytest.raises(ScheduleError):
        parse_cron(bad)


def test_never_firing_expression_is_reported() -> None:
    with pytest.raises(ScheduleError, match="never fires"):
        next_cron_fire("0 0 30 2 *", "UTC", utc(2026, 1, 1))


def test_unknown_timezone() -> None:
    with pytest.raises(ScheduleError, match="unknown time zone"):
        next_cron_fire("* * * * *", "Mars/Olympus", utc(2026, 1, 1))


# ---------------------------------------------------------------- next fire


def test_simple_next_times_in_utc() -> None:
    assert next_cron_fire("*/15 * * * *", "UTC", utc(2026, 10, 7, 10, 7, 30)) == utc(
        2026, 10, 7, 10, 15
    )
    assert next_cron_fire("@daily", "UTC", utc(2026, 10, 7, 0, 0)) == utc(
        2026, 10, 8
    )  # strictly after
    assert next_cron_fire("0 9 * * mon", "UTC", utc(2026, 10, 7)) == utc(
        2026, 10, 12, 9
    )  # Wed -> Mon


def test_month_and_leap_day() -> None:
    assert next_cron_fire("0 0 31 * *", "UTC", utc(2026, 4, 1)) == utc(2026, 5, 31)
    assert next_cron_fire("0 0 29 2 *", "UTC", utc(2026, 3, 1)) == utc(2028, 2, 29)


def test_dom_or_dow_when_both_restricted() -> None:
    # 1st of the month OR any Friday (Vixie cron semantics).
    fires = ScheduleSpec(kind="cron", cron="0 0 1 * fri").upcoming(utc(2026, 10, 7), 3)
    assert fires == [utc(2026, 10, 9), utc(2026, 10, 16), utc(2026, 10, 23)]
    assert ScheduleSpec(kind="cron", cron="0 0 1 * fri").upcoming(utc(2026, 10, 30, 1), 1) == [
        utc(2026, 11, 1)
    ]


def test_time_zone_is_respected() -> None:
    # 09:00 in Kolkata (UTC+5:30) is 03:30 UTC.
    assert next_cron_fire("0 9 * * *", "Asia/Kolkata", utc(2026, 10, 7)) == utc(2026, 10, 7, 3, 30)


# ---------------------------------------------------------------- daylight saving time


def test_spring_forward_nonexistent_time_fires_at_end_of_gap() -> None:
    # Europe/Berlin 2026-03-29: 02:00 -> 03:00 local. 02:30 doesn't exist that day.
    fire = next_cron_fire("30 2 * * *", "Europe/Berlin", utc(2026, 3, 28, 12))
    assert fire == utc(2026, 3, 29, 1, 0)  # 03:00 CEST
    following = next_cron_fire("30 2 * * *", "Europe/Berlin", fire)
    assert following == utc(2026, 3, 30, 0, 30)  # back to normal: 02:30 CEST


def test_spring_forward_does_not_double_fire_when_gap_maps_onto_a_real_slot() -> None:
    # Every 30 min: 02:00 and 02:30 vanish; both map to 03:00, which is itself a slot.
    spec = ScheduleSpec(kind="cron", cron="*/30 * * * *", timezone="Europe/Berlin")
    fires = spec.upcoming(utc(2026, 3, 29, 0, 15), 4)  # from 01:15 local
    assert fires == [
        utc(2026, 3, 29, 0, 30),
        utc(2026, 3, 29, 1, 0),
        utc(2026, 3, 29, 1, 30),
        utc(2026, 3, 29, 2, 0),
    ]
    assert len(set(fires)) == len(fires)


def test_fall_back_repeated_time_fires_once_at_first_occurrence() -> None:
    # America/New_York 2026-11-01: 02:00 EDT -> 01:00 EST; 01:30 happens twice.
    spec = ScheduleSpec(kind="cron", cron="30 1 * * *", timezone="America/New_York")
    fires = spec.upcoming(utc(2026, 10, 31, 12), 2)
    assert fires[0] == utc(2026, 11, 1, 5, 30)  # 01:30 EDT (first occurrence)
    assert fires[1] == utc(2026, 11, 2, 6, 30)  # next day 01:30 EST, not 01:30 EST same day


def test_hourly_across_fall_back_keeps_every_real_hour() -> None:
    spec = ScheduleSpec(kind="cron", cron="0 * * * *", timezone="America/New_York")
    fires = spec.upcoming(utc(2026, 11, 1, 3, 30), 4)  # from 23:30 EDT
    # 00:00 EDT, 01:00 EDT, 01:00 EST would be the repeated hour (cron fires once), 02:00 EST
    assert fires == [
        utc(2026, 11, 1, 4),
        utc(2026, 11, 1, 5),
        utc(2026, 11, 1, 7),
        utc(2026, 11, 1, 8),
    ]


# ---------------------------------------------------------------- intervals


def test_interval_stays_on_its_phase() -> None:
    anchor = utc(2026, 10, 7, 10, 0, 0)
    assert next_interval_fire(300, anchor, utc(2026, 10, 7, 10, 7)) == utc(2026, 10, 7, 10, 10)
    assert next_interval_fire(300, anchor, utc(2026, 10, 7, 10, 10)) == utc(2026, 10, 7, 10, 15)
    assert next_interval_fire(300, anchor, utc(2026, 10, 7, 9)) == anchor


# ---------------------------------------------------------------- misfire policy

EVERY_MIN = ScheduleSpec(kind="cron", cron="* * * * *")
GRACE = timedelta(seconds=60)


def test_on_time_fire_runs() -> None:
    d = due_fires(
        EVERY_MIN,
        utc(2026, 1, 1, 10),
        utc(2026, 1, 1, 10, 0, 2),
        policy="skip",
        grace=GRACE,
        max_catchup=5,
    )
    assert (d.fire, d.skipped, d.next_fire_at) == ([utc(2026, 1, 1, 10)], 0, utc(2026, 1, 1, 10, 1))


def test_not_yet_due() -> None:
    d = due_fires(
        EVERY_MIN,
        utc(2026, 1, 1, 10),
        utc(2026, 1, 1, 9, 59, 59),
        policy="run_all",
        grace=GRACE,
        max_catchup=5,
    )
    assert (d.fire, d.next_fire_at) == ([], utc(2026, 1, 1, 10))


@pytest.mark.parametrize(
    ("policy", "expected_minutes", "skipped"),
    [("skip", [10], 10), ("run_once", [10], 10), ("run_all", [7, 8, 9, 10], 7)],
)
def test_missed_slots_after_downtime(
    policy: MisfirePolicy, expected_minutes: list[int], skipped: int
) -> None:
    # Scheduler was down from 10:00 to 10:10:30; slots 10:00..10:10 were due.
    d = due_fires(
        EVERY_MIN,
        utc(2026, 1, 1, 10, 0),
        utc(2026, 1, 1, 10, 10, 30),
        policy=policy,
        grace=GRACE,
        max_catchup=4,
    )
    assert [f.minute for f in d.fire] == expected_minutes
    assert d.skipped == skipped
    assert d.next_fire_at == utc(2026, 1, 1, 10, 11)


def test_run_once_picks_latest_missed_when_nothing_is_on_time() -> None:
    hourly = ScheduleSpec(kind="cron", cron="@hourly")
    d = due_fires(
        hourly,
        utc(2026, 1, 1, 1),
        utc(2026, 1, 1, 4, 30),
        policy="run_once",
        grace=GRACE,
        max_catchup=5,
    )
    assert d.fire == [utc(2026, 1, 1, 4)]
    assert d.next_fire_at == utc(2026, 1, 1, 5)


def test_huge_backlog_is_bounded() -> None:
    d = due_fires(
        EVERY_MIN, utc(2020, 1, 1), utc(2026, 1, 1), policy="run_all", grace=GRACE, max_catchup=3
    )
    assert len(d.fire) == 3
    assert d.next_fire_at == utc(2026, 1, 1, 0, 1)


def test_huge_backlog_still_runs_the_on_time_slot_under_skip() -> None:
    # Down for years on a per-minute schedule; the slot due right now must still run.
    d = due_fires(
        EVERY_MIN,
        utc(2020, 1, 1),
        utc(2026, 1, 1, 0, 0, 5),
        policy="skip",
        grace=GRACE,
        max_catchup=3,
    )
    assert d.fire == [utc(2026, 1, 1)]
    assert d.skipped > 10_000  # a lower bound: huge backlogs are not counted slot by slot
