"""Host stats for heartbeats, using only the standard library and /proc where available."""

import contextlib
import os
import platform
import shutil
from functools import cache
from pathlib import Path

from torqrun_protocol.agent import SystemStats


@cache
def _os_pretty() -> str | None:
    with contextlib.suppress(OSError):
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')[:128]
    system = platform.system()
    return f"{system} {platform.release()}"[:128] if system else None


def _meminfo() -> tuple[int | None, int | None]:
    """(total, available) bytes from /proc/meminfo; (None, None) where unavailable."""
    values: dict[str, int] = {}
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = int(rest.split()[0]) * 1024
    return values.get("MemTotal"), values.get("MemAvailable")


def _uptime() -> float | None:
    with contextlib.suppress(OSError, ValueError, IndexError):
        return float(Path("/proc/uptime").read_text().split()[0])
    return None


def collect(disk_path: Path) -> SystemStats:
    load: tuple[float, float, float] | tuple[None, None, None] = (None, None, None)
    with contextlib.suppress(OSError, AttributeError):
        load = os.getloadavg()
    disk_total = disk_free = None
    with contextlib.suppress(OSError):
        usage = shutil.disk_usage(disk_path if disk_path.exists() else Path("/"))
        disk_total, disk_free = usage.total, usage.free
    mem_total, mem_available = _meminfo()
    return SystemStats(
        os_pretty=_os_pretty(),
        kernel=platform.release()[:128] or None,
        python_version=platform.python_version(),
        cpu_count=os.cpu_count(),
        load_1m=load[0],
        load_5m=load[1],
        load_15m=load[2],
        mem_total_bytes=mem_total,
        mem_available_bytes=mem_available,
        disk_total_bytes=disk_total,
        disk_free_bytes=disk_free,
        uptime_seconds=_uptime(),
    )
