from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from script_manager.config import Settings
from script_manager.main import create_app

TEST_API_KEY = "test-control-key-1234"


@pytest.fixture
def app_bundle(tmp_path: Path) -> Iterator[tuple[TestClient, FastAPI, Settings]]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "slow_script.py").write_text(
        "import time\ntime.sleep(0.2)\nprint('execution complete')\n",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_key=TEST_API_KEY,
        scripts_dir=scripts_dir,
        scheduler_timezone="UTC",
        script_timeout_seconds=2,
        max_workers=2,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app, settings
