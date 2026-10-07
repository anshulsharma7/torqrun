import time

from torqrun_core.ids import uuid7


def test_uuid7_version_and_variant() -> None:
    u = uuid7()
    assert u.version == 7
    assert u.variant == "specified in RFC 4122"


def test_uuid7_embeds_current_time() -> None:
    before = time.time_ns() // 1_000_000
    u = uuid7()
    after = time.time_ns() // 1_000_000
    assert before <= u.int >> 80 <= after


def test_uuid7_is_ordered_across_milliseconds_and_unique() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()
    assert first < second
    assert len({uuid7() for _ in range(10_000)}) == 10_000
