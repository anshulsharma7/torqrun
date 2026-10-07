import sys
from pathlib import Path

import pytest

from torqrun_agent import sysinfo


def test_collect_reports_basic_host_facts(tmp_path: Path) -> None:
    stats = sysinfo.collect(tmp_path)
    assert stats.cpu_count is not None and stats.cpu_count >= 1
    assert stats.python_version
    assert stats.disk_total_bytes is not None and stats.disk_free_bytes is not None
    assert stats.disk_free_bytes <= stats.disk_total_bytes


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")
def test_linux_memory_load_and_uptime(tmp_path: Path) -> None:
    stats = sysinfo.collect(tmp_path)
    assert stats.mem_total_bytes and stats.mem_available_bytes is not None
    assert stats.mem_available_bytes <= stats.mem_total_bytes
    assert stats.load_1m is not None
    assert stats.uptime_seconds and stats.uptime_seconds > 0


def test_missing_disk_path_falls_back_to_root(tmp_path: Path) -> None:
    assert sysinfo.collect(tmp_path / "missing").disk_total_bytes is not None
