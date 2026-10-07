import pytest

from torqrun_agent.client import ControlPlaneError, LeaseLostError
from torqrun_agent.logship import BATCH_CHUNKS, LogShipper
from torqrun_protocol.agent import LogChunk


class Sink:
    def __init__(self, fail_times: int = 0, exc: Exception | None = None) -> None:
        self.batches: list[list[LogChunk]] = []
        self.fail_times = fail_times
        self.exc = exc or ControlPlaneError("boom")

    async def __call__(self, batch: list[LogChunk]) -> None:
        if self.fail_times:
            self.fail_times -= 1
            raise self.exc
        self.batches.append(batch)


async def test_sequence_numbers_are_shared_across_streams() -> None:
    sink = Sink()
    ship = LogShipper(sink, max_bytes=10_000, flush_interval=1)
    ship.emit("stdout", "a\n")
    ship.emit("stderr", "b\n")
    ship.emit("stdout", "c\n")
    await ship.flush()
    chunks = [c for b in sink.batches for c in b]
    assert [(c.seq, c.stream, c.data) for c in chunks] == [
        (0, "stdout", "a\n"),
        (1, "stderr", "b\n"),
        (2, "stdout", "c\n"),
    ]
    assert ship.last_seq == 2


async def test_failed_send_keeps_buffer_for_retry() -> None:
    sink = Sink(fail_times=1)
    ship = LogShipper(sink, max_bytes=10_000, flush_interval=1)
    ship.emit("stdout", "a\n")
    with pytest.raises(ControlPlaneError):
        await ship.flush()
    await ship.flush()
    assert [c.data for b in sink.batches for c in b] == ["a\n"]


async def test_output_limit_truncates_with_single_marker() -> None:
    sink = Sink()
    ship = LogShipper(sink, max_bytes=10, flush_interval=1)
    for _ in range(5):
        ship.emit("stdout", "12345\n")
    await ship.flush()
    chunks = [c for b in sink.batches for c in b]
    assert [c.stream for c in chunks] == ["stdout", "system"]
    assert "output limit" in chunks[-1].data


async def test_large_backlog_is_sent_in_bounded_batches() -> None:
    sink = Sink()
    ship = LogShipper(sink, max_bytes=10**9, flush_interval=1)
    for i in range(BATCH_CHUNKS * 2 + 5):
        ship.emit("stdout", f"{i}\n")
    await ship.flush()
    assert [len(b) for b in sink.batches] == [BATCH_CHUNKS, BATCH_CHUNKS, 5]


async def test_run_propagates_lease_loss() -> None:
    sink = Sink(fail_times=1, exc=LeaseLostError("lease_revoked"))
    ship = LogShipper(sink, max_bytes=10_000, flush_interval=0.01)
    ship.emit("stdout", "a\n")
    with pytest.raises(LeaseLostError):
        await ship.run()
