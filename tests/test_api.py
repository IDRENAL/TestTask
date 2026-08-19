import time
from pathlib import Path

from conftest import TEST_API_KEY
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from script_manager.config import Settings
from script_manager.main import create_app
from script_manager.models import Execution

AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


def test_list_scripts_is_public_and_mutations_require_key(
    app_bundle: tuple[TestClient, FastAPI, Settings],
) -> None:
    client, _app, _settings = app_bundle

    response = client.get("/api/scripts")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "name": "Slow Script",
            "filename": "slow_script.py",
            "cron_expression": "0 * * * *",
            "enabled": False,
            "running": False,
        }
    ]
    assert client.post("/api/scripts/1/run").status_code == 401
    assert client.post("/api/scripts/1/run", headers={"X-API-Key": "wrong"}).status_code == 401


def test_schedule_pause_and_resume(app_bundle: tuple[TestClient, FastAPI, Settings]) -> None:
    client, _app, _settings = app_bundle

    invalid = client.patch(
        "/api/scripts/1/schedule",
        headers=AUTH_HEADERS,
        json={"cron_expression": "not a cron value"},
    )
    updated = client.patch(
        "/api/scripts/1/schedule",
        headers=AUTH_HEADERS,
        json={"cron_expression": "*/2  * * * *"},
    )
    resumed = client.post("/api/scripts/1/resume", headers=AUTH_HEADERS)
    paused = client.post("/api/scripts/1/pause", headers=AUTH_HEADERS)

    assert invalid.status_code == 422
    assert updated.status_code == 200
    assert updated.json()["cron_expression"] == "*/2 * * * *"
    assert resumed.status_code == 200
    assert resumed.json()["enabled"] is True
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False


def test_manual_run_prevents_overlap_and_persists_output(
    app_bundle: tuple[TestClient, FastAPI, Settings],
) -> None:
    client, app, _settings = app_bundle

    accepted = client.post("/api/scripts/1/run", headers=AUTH_HEADERS)
    duplicate = client.post("/api/scripts/1/run", headers=AUTH_HEADERS)

    assert accepted.status_code == 202
    assert duplicate.status_code == 409

    deadline = time.monotonic() + 3
    execution = None
    while time.monotonic() < deadline:
        with app.state.database.session() as session:
            execution = session.scalar(select(Execution).order_by(Execution.id.desc()))
        if execution is not None and execution.status != "running":
            break
        time.sleep(0.02)

    assert execution is not None
    assert execution.status == "succeeded"
    assert execution.trigger_type == "manual"
    assert execution.return_code == 0
    assert execution.stdout == "execution complete\n"
    assert execution.stderr == ""
    assert execution.finished_at is not None


def test_unknown_script_returns_not_found(app_bundle: tuple[TestClient, FastAPI, Settings]) -> None:
    client, _app, _settings = app_bundle

    response = client.post("/api/scripts/999/run", headers=AUTH_HEADERS)

    assert response.status_code == 404


def test_schedule_and_pause_state_survive_restart(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "job.py").write_text("print('ok')\n", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'persistent.db'}",
        api_key=TEST_API_KEY,
        scripts_dir=scripts_dir,
    )

    with TestClient(create_app(settings)) as client:
        script_id = client.get("/api/scripts").json()[0]["id"]
        client.patch(
            f"/api/scripts/{script_id}/schedule",
            headers=AUTH_HEADERS,
            json={"cron_expression": "15 4 * * 1"},
        )
        client.post(f"/api/scripts/{script_id}/resume", headers=AUTH_HEADERS)

    with TestClient(create_app(settings)) as client:
        script = client.get("/api/scripts").json()[0]

    assert script["cron_expression"] == "15 4 * * 1"
    assert script["enabled"] is True


def test_included_scripts_have_expected_defaults(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    for filename in ("resource_monitor.py", "disk_usage.py", "runtime_info.py"):
        (scripts_dir / filename).write_text("print('ok')\n", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'defaults.db'}",
        api_key=TEST_API_KEY,
        scripts_dir=scripts_dir,
    )

    with TestClient(create_app(settings)) as client:
        scripts = {item["filename"]: item for item in client.get("/api/scripts").json()}

    assert len(scripts) == 3
    assert scripts["resource_monitor.py"]["cron_expression"] == "* * * * *"
    assert scripts["resource_monitor.py"]["enabled"] is True
    assert scripts["disk_usage.py"]["cron_expression"] == "*/5 * * * *"
    assert scripts["runtime_info.py"]["cron_expression"] == "0 * * * *"
