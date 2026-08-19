"""Emit a JSON snapshot of the Python runtime and host load."""

import json
import os
import platform
from datetime import UTC, datetime


def main() -> None:
    load_average = os.getloadavg() if hasattr(os, "getloadavg") else None
    result = {
        "measured_at": datetime.now(UTC).isoformat(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "processor_count": os.cpu_count(),
        "load_average": load_average,
    }
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
