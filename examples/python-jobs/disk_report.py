"""Report free disk space for the filesystems the agent can see. Fails if any is >90% full."""

import shutil
import sys

THRESHOLD = 0.90
failed = False
for path in sys.argv[1:] or ["/"]:
    usage = shutil.disk_usage(path)
    used = usage.used / usage.total
    print(f"{path}: {used:.0%} used ({usage.free // 2**30} GiB free)")
    if used > THRESHOLD:
        print(f"{path} is above {THRESHOLD:.0%}", file=sys.stderr)
        failed = True
sys.exit(1 if failed else 0)
