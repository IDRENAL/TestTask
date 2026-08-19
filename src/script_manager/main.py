"""FastAPI application factory."""

import hmac
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, status

from script_manager.config import Settings
from script_manager.database import Database
from script_manager.runtime import (
    InvalidCronError,
    ScriptAlreadyRunningError,
    ScriptManager,
    ScriptNotFoundError,
)
from script_manager.schemas import ActionAccepted, ScheduleUpdate, ScriptView


def _control_key_dependency(expected_key: str) -> Callable[..., None]:
    def require_control_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if x_api_key is None or not hmac.compare_digest(x_api_key, expected_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid X-API-Key header is required",
            )

    return require_control_key


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings()  # type: ignore[call-arg]
    database = Database(runtime_settings.database_url)
    manager = ScriptManager(
        database=database,
        scripts_dir=runtime_settings.scripts_dir,
        timezone=runtime_settings.scheduler_timezone,
        timeout_seconds=runtime_settings.script_timeout_seconds,
        max_workers=runtime_settings.max_workers,
    )
    require_control_key = _control_key_dependency(runtime_settings.api_key.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        database.create_schema()
        manager.start()
        app.state.database = database
        app.state.manager = manager
        try:
            yield
        finally:
            manager.shutdown()
            database.dispose()

    app = FastAPI(title="Script Manager API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/scripts", response_model=list[ScriptView])
    def list_scripts() -> list[ScriptView]:
        return manager.list_scripts()

    @app.patch(
        "/api/scripts/{script_id}/schedule",
        response_model=ScriptView,
        dependencies=[Depends(require_control_key)],
    )
    def update_schedule(script_id: int, payload: ScheduleUpdate) -> ScriptView:
        try:
            return manager.update_schedule(script_id, payload.cron_expression)
        except ScriptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Script not found") from exc
        except InvalidCronError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid cron expression: {exc}") from exc

    @app.post(
        "/api/scripts/{script_id}/run",
        response_model=ActionAccepted,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_control_key)],
    )
    def run_script(script_id: int) -> ActionAccepted:
        try:
            manager.run_now(script_id)
        except ScriptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Script not found") from exc
        except ScriptAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail="Script is already running") from exc
        return ActionAccepted()

    @app.post(
        "/api/scripts/{script_id}/pause",
        response_model=ScriptView,
        dependencies=[Depends(require_control_key)],
    )
    def pause_script(script_id: int) -> ScriptView:
        try:
            return manager.pause(script_id)
        except ScriptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Script not found") from exc

    @app.post(
        "/api/scripts/{script_id}/resume",
        response_model=ScriptView,
        dependencies=[Depends(require_control_key)],
    )
    def resume_script(script_id: int) -> ScriptView:
        try:
            return manager.resume(script_id)
        except ScriptNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Script not found") from exc
        except InvalidCronError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid cron expression: {exc}") from exc

    return app


def run() -> None:
    uvicorn.run("script_manager.main:create_app", factory=True, host="0.0.0.0", port=8000)
