"""Health + runner tests using only a fake binary.

These tests use a tiny shell snippet as a stand-in CLI agent. They verify
the orchestrator end-to-end (lock, argv, log, saved-file detection)
without ever invoking real opencode / codex / openclaw.
"""

from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest

import mdrunner.agents as agents_mod
from mdrunner.agents import AgentAdapter, ArgvResult
from mdrunner.config import Settings, load_settings
from mdrunner.health import bypass_risk_level, probe_health
from mdrunner.runner import detect_saved_file, run_task, preview_task


# ---------------------------------------------------------------------------
# Fixtures: a fake agent binary in tmp_path/bin/<name>
# ---------------------------------------------------------------------------


class FakeAgentAdapter(AgentAdapter):
    id = "fake"
    binary = "fakeagent"
    display_name = "Fake"

    def build_argv(self, prompt_file, *, model, working_dir, extra_args, bypass_flags):
        body = prompt_file.read_text(encoding="utf-8")
        argv = ["fakeagent", body]
        argv.extend(bypass_flags)
        if model:
            argv += ["--model", model]
        argv.extend(extra_args)
        return ArgvResult(argv=argv, cwd=working_dir)


@pytest.fixture()
def fake_agent_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bin"
    d.mkdir()
    script = d / "fakeagent"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # fake agent: echos its argv, prints a "Saved:" line, exits 0.
            echo "[fakeagent] argv: $@"
            # Simulate saved-file marker (also test Korean marker).
            # NOTE: single quotes around the path keep bash from treating
            # the backticks as command substitution.
            echo 'Saved: /tmp/output.md'
            echo '저장 완료: `/tmp/output-ko.md`'
            # exit non-zero if --fail appears anywhere in argv
            for arg in "$@"; do
                if [ "$arg" = "--fail" ]; then exit 7; fi
            done
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    # Prepend to PATH so resolve_binary / shutil.which can find it
    os.environ["PATH"] = f"{d}{os.pathsep}{os.environ['PATH']}"
    # Register the fake adapter for the duration of the test
    agents_mod.ADAPTERS["fake"] = FakeAgentAdapter()
    yield d
    agents_mod.ADAPTERS.pop("fake", None)


@pytest.fixture()
def config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_agent_dir: Path) -> tuple[Path, Path]:
    monkeypatch.setenv("RUNCHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("RUNCHER_LOG_DIR", str(tmp_path / "log"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "log").mkdir()
    return (tmp_path / "cfg" / "tasks.yaml", tmp_path / "cfg" / "settings.yaml")


def write_settings(p: Path, binary: str = "fakeagent") -> Settings:
    s = Settings.from_dict = None  # noqa: F841
    import yaml

    raw = {
        "agents": {
            "fake": {
                "enabled": True,
                "binary": binary,
                "default_model": "fake-model-1",
                "health_cmd": [binary, "--version"],
                "bypass": {"scheduled": ["--auto"], "manual": []},
                "presets": [],
            }
        },
        "defaults": {"timeout_minutes": 5},
    }
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh)
    return load_settings(p)


def write_task(p: Path, *, prompt: Path, workdir: Path | None = None, timeout: int = 2,
               extra: list[str] | None = None) -> None:
    import yaml

    raw = {
        "tasks": [
            {
                "id": "t",
                "name": "T",
                "enabled": True,
                "agent": "fake",
                "prompt_file": str(prompt),
                "working_dir": str(workdir) if workdir else None,
                "extra_args": extra or [],
                "timeout_minutes": timeout,
            }
        ]
    }
    with p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_probe_health_ok(fake_agent_dir: Path) -> None:
    r = probe_health("fakeagent", ["fakeagent", "--version"])
    assert r.ok
    assert r.binary_path and r.binary_path.endswith("fakeagent")
    assert r.version is not None


def test_probe_health_missing() -> None:
    r = probe_health("definitely-not-a-binary-xyz", ["definitely-not-a-binary-xyz"])
    assert not r.ok
    assert "not found" in (r.error or "")


def test_bypass_risk_classification() -> None:
    assert bypass_risk_level([]) == "none"
    assert bypass_risk_level(["--auto"]) == "medium"
    assert bypass_risk_level(["--yolo"]) == "high"
    assert bypass_risk_level(["--sandbox", "danger-full-access"]) == "high"


# ---------------------------------------------------------------------------
# Saved-file detection
# ---------------------------------------------------------------------------


def test_detect_saved_file_korean_marker() -> None:
    text = "[step 1] ran\n[step 2] done\n> 저장 완료: `/home/doyoonkim/keep/test.md`\n[bye]\n"
    assert detect_saved_file(text) == "/home/doyoonkim/keep/test.md"


def test_detect_saved_file_english_marker() -> None:
    text = "Working...\nSaved: /tmp/out.md\nDone.\n"
    assert detect_saved_file(text) == "/tmp/out.md"


def test_detect_saved_file_absent() -> None:
    assert detect_saved_file("nothing here") is None


# ---------------------------------------------------------------------------
# Runner end-to-end
# ---------------------------------------------------------------------------


def test_run_task_success(tmp_path: Path, fake_agent_dir: Path, config_paths) -> None:
    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("hello fake agent", encoding="utf-8")
    write_settings(settings_p)
    write_task(tasks_p, prompt=prompt, workdir=tmp_path)

    settings = load_settings(settings_p)
    result = run_task("t", mode="manual", settings=settings,
                      tasks_file=tasks_p, settings_file=settings_p)

    assert result.ok, result.error
    assert result.exit_code == 0
    assert result.saved_file == "/tmp/output-ko.md"  # last-seen wins
    assert result.log_file and Path(result.log_file).exists()
    # log contains the agent's stdout
    log = Path(result.log_file).read_text(encoding="utf-8")
    assert "Saved: /tmp/output.md" in log
    assert "저장 완료" in log


def test_run_task_nonzero_exit(tmp_path: Path, fake_agent_dir: Path, config_paths) -> None:
    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("--fail please", encoding="utf-8")
    write_settings(settings_p)
    write_task(tasks_p, prompt=prompt, workdir=tmp_path)

    settings = load_settings(settings_p)
    # Pass --fail to the fake agent (extra args propagation)
    write_task(tasks_p, prompt=prompt, workdir=tmp_path, extra=["--fail"])
    result = run_task("t", mode="manual", settings=settings,
                      tasks_file=tasks_p, settings_file=settings_p)
    assert not result.ok
    assert result.exit_code == 7


def test_run_task_binary_missing(tmp_path: Path, config_paths) -> None:
    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("hello", encoding="utf-8")
    write_settings(settings_p, binary="totally-missing-binary")
    write_task(tasks_p, prompt=prompt, workdir=tmp_path)
    settings = load_settings(settings_p)
    result = run_task("t", mode="manual", settings=settings,
                      tasks_file=tasks_p, settings_file=settings_p)
    assert not result.ok
    assert "not found" in (result.error or "")


def test_run_task_disabled(tmp_path: Path, fake_agent_dir: Path, config_paths) -> None:
    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("hello", encoding="utf-8")
    write_settings(settings_p)
    # Override with enabled=False
    import yaml

    raw = {
        "tasks": [
            {
                "id": "t",
                "name": "T",
                "enabled": False,
                "agent": "fake",
                "prompt_file": str(prompt),
                "timeout_minutes": 1,
            }
        ]
    }
    with tasks_p.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh)
    settings = load_settings(settings_p)
    result = run_task("t", mode="manual", settings=settings,
                      tasks_file=tasks_p, settings_file=settings_p)
    assert not result.ok
    assert "disabled" in (result.error or "")


def test_preview_quotes_argv(tmp_path: Path, fake_agent_dir: Path, config_paths) -> None:
    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("hello", encoding="utf-8")
    write_settings(settings_p)
    write_task(tasks_p, prompt=prompt, workdir=tmp_path)
    settings = load_settings(settings_p)
    info = preview_task("t", mode="manual", settings=settings,
                        tasks_file=tasks_p, settings_file=settings_p)
    assert info["argv"][0] == "fakeagent"
    assert info["argv_quoted"][0] == "fakeagent"
    # quoted command should be shell-safe
    quoted = " ".join(info["argv_quoted"])
    assert "fakeagent" in quoted


def test_run_task_lock_blocks_second(
    tmp_path: Path, fake_agent_dir: Path, config_paths
) -> None:
    """Holding a long-lived lock should make a second run fail fast."""
    import threading

    from mdrunner.runner import task_lock

    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with task_lock("t"):
            held.set()
            release.wait(timeout=5)

    th = threading.Thread(target=hold_lock, daemon=True)
    th.start()
    assert held.wait(timeout=2)

    tasks_p, settings_p = config_paths
    prompt = tmp_path / "p.md"
    prompt.write_text("hello", encoding="utf-8")
    write_settings(settings_p)
    write_task(tasks_p, prompt=prompt, workdir=tmp_path)
    settings = load_settings(settings_p)
    result = run_task("t", mode="manual", settings=settings,
                      tasks_file=tasks_p, settings_file=settings_p)
    assert not result.ok
    assert "already running" in (result.error or "")

    release.set()
    th.join(timeout=2)