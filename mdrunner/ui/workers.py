"""Background workers — run tasks and health checks off the UI thread."""

from __future__ import annotations


from PySide6.QtCore import QObject, QThread, Signal

from ..config import Settings, Task
from ..health import probe_health
from ..runner import LockBusyError, run_task


class _TaskSignals(QObject):
    line = Signal(str)
    finished_with_result = Signal(dict)


class TaskRunWorker(QThread):
    """Runs one task via mdrunner.runner.run_task and streams output via signals.

    Implemented as QThread (not QThreadPool) so the run-button is exclusive
    and progress is observable. For Phase 2 this is enough; Phase 4 may move
    to a process-based runner for better signal handling.
    """

    def __init__(self, task: Task, *, settings: Settings, mode: str) -> None:
        super().__init__()
        self.task = task
        self.settings = settings
        self.mode = mode
        self.signals = _TaskSignals()
        # Expose signals as direct attributes so call sites can do
        # `worker.line.connect(...)` without reaching into `.signals`.
        self.line = self.signals.line
        self.finished_with_result = self.signals.finished_with_result

    def run(self) -> None:  # noqa: D401
        try:
            result = run_task(
                self.task.id,
                mode=self.mode,
                settings=self.settings,
            )
        except LockBusyError as exc:
            self.signals.line.emit(f"lock busy: {exc}")
            self.signals.finished_with_result.emit(
                {
                    "task_id": self.task.id,
                    "ok": False,
                    "error": str(exc),
                    "duration": 0.0,
                    "saved_file": None,
                }
            )
            return
        except Exception as exc:  # noqa: BLE001
            self.signals.line.emit(f"runner error: {exc}")
            self.signals.finished_with_result.emit(
                {
                    "task_id": self.task.id,
                    "ok": False,
                    "error": str(exc),
                    "duration": 0.0,
                    "saved_file": None,
                }
            )
            return

        # Telegram on failure (best-effort)
        if not result.ok and self.task.on_failure.notify:
            self._send_telegram_on_failure(result)

        self.signals.finished_with_result.emit(
            {
                "task_id": result.task_id,
                "ok": result.ok,
                "error": result.error,
                "duration": result.duration_seconds,
                "saved_file": result.saved_file,
            }
        )

    def _send_telegram_on_failure(self, result) -> None:
        try:
            from .. import telegram
            from . import _telegram_settings

            cfg = _telegram_settings.load()
            if not cfg.get("bot_token") or not cfg.get("chat_id"):
                return
            if not cfg.get("notify_on_failure", True):
                return
            body = telegram.format_failure(
                self.task.name,
                {
                    "duration": result.duration_seconds,
                    "exit_code": result.exit_code,
                    "error": result.error,
                    "saved_file": result.saved_file,
                },
            )
            ok, resp = telegram.send(
                bot_token=cfg["bot_token"],
                chat_id=cfg["chat_id"],
                text=body,
            )
            if ok:
                self.signals.line.emit("[telegram] failure notification sent")
            else:
                self.signals.line.emit(f"[telegram] notify failed: {resp}")
        except Exception as exc:  # noqa: BLE001
            self.signals.line.emit(f"[telegram] error: {exc}")


class _HealthSignals(QObject):
    results_ready = Signal(list)
    progress = Signal(str)


class HealthCheckWorker(QThread):
    """Probes every configured agent's binary in a background thread."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.signals = _HealthSignals()
        # Expose signals as direct attributes (see TaskRunWorker note).
        self.results_ready = self.signals.results_ready
        self.progress = self.signals.progress
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401
        rows: list[dict] = []
        for agent_id, cfg in self.settings.agents.items():
            if self._cancel:
                return
            self.signals.progress.emit(f"probing {agent_id}…")
            r = probe_health(cfg.binary, cfg.health_cmd)
            rows.append(
                {
                    "agent": agent_id,
                    "binary": cfg.binary,
                    "binary_path": r.binary_path,
                    "ok": r.ok,
                    "version": r.version,
                    "error": r.error,
                }
            )
        self.signals.results_ready.emit(rows)


class _ModelSignals(QObject):
    models_ready = Signal(list)
    error = Signal(str)


class ModelFetchWorker(QThread):
    """Fetches model lists for a given agent in a background thread."""

    def __init__(self, agent_id: str, binary_path: str | None = None) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.binary_path = binary_path
        self.signals = _ModelSignals()
        self.models_ready = self.signals.models_ready
        self.error = self.signals.error

    def run(self) -> None:
        try:
            from ..utils.models import fetch_agent_models
            models = fetch_agent_models(self.agent_id, self.binary_path)
            self.signals.models_ready.emit(models)
        except Exception as e:
            self.signals.error.emit(str(e))


class _QuotaSignals(QObject):
    results_ready = Signal(list)  # list[QuotaResult]


class QuotaFetchWorker(QThread):
    """Refresh agent quota by shelling out to ``mdrunner quota --json --write``.

    The claude/agy probes call ``pty.fork()``; doing that from inside a Qt
    worker thread is unsafe (fork in a multi-threaded process). Run it in a
    clean child process instead — which also writes the snapshot itself.
    Emits a list of plain dicts (one per agent).
    """

    def __init__(self, agent_ids: list[str]) -> None:
        super().__init__()
        self.agent_ids = agent_ids
        self.signals = _QuotaSignals()
        self.results_ready = self.signals.results_ready

    def run(self) -> None:
        import subprocess
        import sys

        try:
            subprocess.run(
                [sys.executable, "-m", "mdrunner", "quota", "--write",
                 "--agents", ",".join(self.agent_ids)],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except Exception:  # noqa: BLE001
            pass
        # Read back the snapshot the child just wrote — it carries a working
        # agent's last-good value forward if this round's probe missed.
        try:
            from ..quota import load_snapshot

            agents = load_snapshot().get("agents", {})
            self.signals.results_ready.emit(list(agents.values()))
        except Exception:  # noqa: BLE001
            self.signals.results_ready.emit([])


class _SingleHealthSignals(QObject):
    progress = Signal(str)
    finished = Signal(object)


class SingleAgentHealthWorker(QThread):
    """Runs a 4-step interactive health check for a single agent."""

    def __init__(self, agent_id: str, binary: str, model: str | None) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.binary = binary
        self.model = model
        self.signals = _SingleHealthSignals()
        self.progress = self.signals.progress
        self.finished = self.signals.finished

    def run(self) -> None:
        try:
            from ..health import probe_health_interactive
            result = probe_health_interactive(
                self.agent_id,
                self.binary,
                self.model,
                progress_callback=self.signals.progress.emit
            )
            self.signals.finished.emit(result)
        except Exception as e:
            from ..health import HealthResult
            err_msg = f"헬스체크 내부 오류: {e}"
            self.signals.progress.emit(f"✗ 오류: {err_msg}")
            self.signals.finished.emit(HealthResult(ok=False, error=err_msg))