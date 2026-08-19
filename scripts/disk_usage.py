"""Emit a JSON snapshot of filesystem usage."""

import json
import shutil
from datetime import UTC, datetime


def main() -> None:
    usage = shutil.disk_usage("/")
    result = {
        "measured_at": datetime.now(UTC).isoformat(),
        "filesystem": "/",
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": round(usage.used / usage.total * 100, 2),
    }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
