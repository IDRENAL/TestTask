"""Script discovery, execution, and scheduling."""

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update

from script_manager.database import Database
from script_manager.models import Execution, Script
from script_manager.schemas import ScriptView

logger = logging.getLogger(__name__)

DEFAULT_SCRIPTS: dict[str, tuple[str, str, bool]] = {
    "resource_monitor.py": ("Resource availability monitor", "* * * * *", True),
    "disk_usage.py": ("Disk usage report", "*/5 * * * *", True),
    "runtime_info.py": ("Python runtime report", "0 * * * *", True),
}


class ScriptNotFoundError(Exception):
    """Raised when a script is missing from the current catalog."""


class ScriptAlreadyRunningError(Exception):
    """Raised when overlapping execution is requested."""


class InvalidCronError(Exception):
    """Raised when a five-field cron expression is invalid."""


@dataclass(frozen=True)
class RunSpec:
    script_id: int
    filename: str


class ScriptRunner:
    """Runs allow-listed files without a shell and persists their output."""

    def __init__(
        self,
        database: Database,
        scripts_dir: Path,
        timeout_seconds: int,
        max_workers: int,
    ) -> None:
        self._database = database
        self._scripts_dir = scripts_dir.resolve()
        self._timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="script")
        self._lock = Lock()
        self._running: set[int] = set()

    def is_running(self, script_id: int) -> bool:
        with self._lock:
            return script_id in self._running

    def submit(self, spec: RunSpec, trigger_type: str) -> bool:
        with self._lock:
            if spec.script_id in self._running:
                return False
            self._running.add(spec.script_id)

        try:
            self._executor.submit(self._execute, spec, trigger_type)
        except RuntimeError:
            with self._lock:
                self._running.discard(spec.script_id)
            raise
        return True

    def _safe_script_path(self, filename: str) -> Path:
        candidate = (self._scripts_dir / filename).resolve()
        if candidate.parent != self._scripts_dir or candidate.suffix != ".py":
            raise ValueError("script path is outside the configured scripts directory")
        if not candidate.is_file():
            raise FileNotFoundError(f"script not found: {filename}")
        return candidate

    @staticmethod
    def _text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value

    def _execute(self, spec: RunSpec, trigger_type: str) -> None:
        execution_id: int | None = None
        try:
            with self._database.session() as session:
                execution = Execution(
                    script_id=spec.script_id,
                    trigger_type=trigger_type,
                    status="running",
                )
                session.add(execution)
                session.commit()
                execution_id = execution.id

            script_path = self._safe_script_path(spec.filename)
            safe_environment = {
                "PATH": os.defpath,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            }
            result = subprocess.run(  # noqa: S603 - path comes from the discovered allow-list
                [sys.executable, str(script_path)],
                cwd=self._scripts_dir,
                env=safe_environment,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
            status = "succeeded" if result.returncode == 0 else "failed"
            self._finish_execution(
                execution_id,
                status=status,
                return_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        except subprocess.TimeoutExpired as exc:
            self._finish_execution(
                execution_id,
                status="timed_out",
                return_code=None,
                stdout=self._text(exc.stdout),
                stderr=self._text(exc.stderr) or f"Timed out after {self._timeout_seconds} seconds",
            )
        except Exception as exc:  # execution failures must be recorded, not kill the scheduler
            logger.exception("Failed to execute script %s", spec.filename)
            self._finish_execution(
                execution_id,
                status="failed",
                return_code=None,
                stdout="",
                stderr=str(exc),
            )
        finally:
            with self._lock:
                self._running.discard(spec.script_id)

    def _finish_execution(
        self,
        execution_id: int | None,
        *,
        status: str,
        return_code: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        if execution_id is None:
            return
        with self._database.session() as session:
            execution = session.get(Execution, execution_id)
            if execution is None:
                return
            execution.status = status
            execution.return_code = return_code
            execution.stdout = stdout
            execution.stderr = stderr
            execution.finished_at = datetime.now(UTC)
            session.commit()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)


class ScriptManager:
    """Coordinates the database catalog, runner, and APScheduler."""

    def __init__(
        self,
        database: Database,
        scripts_dir: Path,
        timezone: str,
        timeout_seconds: int,
        max_workers: int,
    ) -> None:
        self._database = database
        self._scripts_dir = scripts_dir
        self._timezone = timezone
        self._scheduler = BackgroundScheduler(timezone=timezone)
        self._runner = ScriptRunner(database, scripts_dir, timeout_seconds, max_workers)

    def start(self) -> None:
        self._scripts_dir.mkdir(parents=True, exist_ok=True)
        self._sync_catalog()
        self._mark_interrupted_executions()
        with self._database.session() as session:
            scripts = session.scalars(
                select(Script).where(Script.available.is_(True), Script.enabled.is_(True))
            ).all()
        for script in scripts:
            try:
                self._add_job(script)
            except InvalidCronError:
                logger.exception("Disabling script with invalid stored cron: %s", script.filename)
                with self._database.session() as session:
                    stored = session.get(Script, script.id)
                    if stored is not None:
                        stored.enabled = False
                        session.commit()
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._runner.shutdown()

    def _sync_catalog(self) -> None:
        files = sorted(
            path
            for path in self._scripts_dir.glob("*.py")
            if path.is_file() and not path.name.startswith("_")
        )
        with self._database.session() as session:
            session.execute(update(Script).values(available=False))
            existing = {script.filename: script for script in session.scalars(select(Script)).all()}
            for path in files:
                script = existing.get(path.name)
                if script is None:
                    name, cron, enabled = DEFAULT_SCRIPTS.get(
                        path.name,
                        (path.stem.replace("_", " ").title(), "0 * * * *", False),
                    )
                    script = Script(
                        name=name,
                        filename=path.name,
                        cron_expression=cron,
                        enabled=enabled,
                    )
                    session.add(script)
                script.available = True
            session.commit()

    def _mark_interrupted_executions(self) -> None:
        with self._database.session() as session:
            session.execute(
                update(Execution)
                .where(Execution.status == "running")
                .values(
                    status="interrupted",
                    finished_at=datetime.now(UTC),
                    stderr="Backend stopped before execution completed",
                )
            )
            session.commit()

    def _trigger(self, expression: str) -> CronTrigger:
        try:
            return CronTrigger.from_crontab(expression, timezone=self._timezone)
        except ValueError as exc:
            raise InvalidCronError(str(exc)) from exc

    @staticmethod
    def _job_id(script_id: int) -> str:
        return f"script:{script_id}"

    def _add_job(self, script: Script) -> None:
        trigger = self._trigger(script.cron_expression)
        self._scheduler.add_job(
            self._run_scheduled,
            trigger=trigger,
            args=[script.id],
            id=self._job_id(script.id),
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )

    def _get_script(self, script_id: int) -> Script:
        with self._database.session() as session:
            script = session.get(Script, script_id)
            if script is None or not script.available:
                raise ScriptNotFoundError
            return script

    def _run_scheduled(self, script_id: int) -> None:
        try:
            script = self._get_script(script_id)
        except ScriptNotFoundError:
            logger.warning("Scheduled script %s is no longer available", script_id)
            return
        if not self._runner.submit(RunSpec(script.id, script.filename), "scheduled"):
            logger.warning("Skipping overlapping run for %s", script.filename)

    def list_scripts(self) -> list[ScriptView]:
        with self._database.session() as session:
            scripts = session.scalars(
                select(Script).where(Script.available.is_(True)).order_by(Script.name)
            ).all()
        return [
            ScriptView(
                id=script.id,
                name=script.name,
                filename=script.filename,
                cron_expression=script.cron_expression,
                enabled=script.enabled,
                running=self._runner.is_running(script.id),
            )
            for script in scripts
        ]

    def get_script(self, script_id: int) -> ScriptView:
        script = self._get_script(script_id)
        return ScriptView(
            id=script.id,
            name=script.name,
            filename=script.filename,
            cron_expression=script.cron_expression,
            enabled=script.enabled,
            running=self._runner.is_running(script.id),
        )

    def update_schedule(self, script_id: int, cron_expression: str) -> ScriptView:
        trigger = self._trigger(cron_expression)
        with self._database.session() as session:
            script = session.get(Script, script_id)
            if script is None or not script.available:
                raise ScriptNotFoundError
            script.cron_expression = cron_expression
            session.commit()
            if script.enabled:
                self._scheduler.add_job(
                    self._run_scheduled,
                    trigger=trigger,
                    args=[script.id],
                    id=self._job_id(script.id),
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
        return self.get_script(script_id)

    def pause(self, script_id: int) -> ScriptView:
        with self._database.session() as session:
            script = session.get(Script, script_id)
            if script is None or not script.available:
                raise ScriptNotFoundError
            script.enabled = False
            session.commit()
        with suppress(JobLookupError):
            self._scheduler.remove_job(self._job_id(script_id))
        return self.get_script(script_id)

    def resume(self, script_id: int) -> ScriptView:
        with self._database.session() as session:
            script = session.get(Script, script_id)
            if script is None or not script.available:
                raise ScriptNotFoundError
            self._trigger(script.cron_expression)
            script.enabled = True
            session.commit()
            self._add_job(script)
        return self.get_script(script_id)

    def run_now(self, script_id: int) -> None:
        script = self._get_script(script_id)
        if not self._runner.submit(RunSpec(script.id, script.filename), "manual"):
            raise ScriptAlreadyRunningError
