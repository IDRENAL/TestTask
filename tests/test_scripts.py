import importlib.util
import json
from pathlib import Path
from types import ModuleType


def load_script(name: str) -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


disk_usage = load_script("disk_usage")
resource_monitor = load_script("resource_monitor")
runtime_info = load_script("runtime_info")


class FakeResponse:
    status = 204

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_resource_monitor_records_http_status(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(resource_monitor, "urlopen", lambda *_args, **_kwargs: FakeResponse())

    result = resource_monitor.check_resource("https://example.test")

    assert result["http_status"] == 204
    assert result["available"] is True
    assert result["url"] == "https://example.test"


def test_local_report_scripts_emit_json(capsys, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    disk_usage.main()
    disk_result = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(runtime_info.os, "getloadavg", lambda: (0.1, 0.2, 0.3))
    runtime_info.main()
    runtime_result = json.loads(capsys.readouterr().out)

    assert Path(disk_result["filesystem"]) == Path("/")
    assert 0 <= disk_result["used_percent"] <= 100
    assert runtime_result["python_version"]
    assert runtime_result["load_average"] == [0.1, 0.2, 0.3]
