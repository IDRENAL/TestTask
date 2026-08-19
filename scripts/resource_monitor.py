"""Check three public resources and emit one JSON log line per result."""

import json
import multiprocessing
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RESOURCES = (
    "https://example.com",
    "https://www.python.org",
    "https://github.com",
)
REQUEST_TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 15


def check_resource(url: str) -> dict[str, object]:
    checked_at = datetime.now(UTC).isoformat()
    request = Request(url, headers={"User-Agent": "script-manager-monitor/1.0"})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
            return {
                "checked_at": checked_at,
                "url": url,
                "http_status": response.status,
                "available": 200 <= response.status < 400,
            }
    except HTTPError as exc:
        return {
            "checked_at": checked_at,
            "url": url,
            "http_status": exc.code,
            "available": False,
            "error": str(exc),
        }
    except (URLError, TimeoutError) as exc:
        return {
            "checked_at": checked_at,
            "url": url,
            "http_status": None,
            "available": False,
            "error": str(exc),
        }


def _check_in_child(url: str, connection) -> None:  # type: ignore[no-untyped-def]
    try:
        connection.send(check_resource(url))
    finally:
        connection.close()


def _timeout_result(url: str) -> dict[str, object]:
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "url": url,
        "http_status": None,
        "available": False,
        "error": f"Request did not finish within {TOTAL_TIMEOUT_SECONDS} seconds",
    }


def main() -> None:
    context = multiprocessing.get_context()
    workers = []
    for resource in RESOURCES:
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_check_in_child, args=(resource, sender))
        process.start()
        sender.close()
        workers.append((resource, process, receiver))

    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    results: dict[str, dict[str, object]] = {}
    for resource, process, receiver in workers:
        process.join(max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            process.terminate()
            process.join()
            results[resource] = _timeout_result(resource)
        elif receiver.poll():
            results[resource] = receiver.recv()
        else:
            results[resource] = {
                **_timeout_result(resource),
                "error": "Resource check worker exited without a result",
            }
        receiver.close()

    for resource in RESOURCES:
        print(json.dumps(results[resource], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
