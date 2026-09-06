"""Run-log formatting: short argv, session id, failure reason."""

from pathlib import Path

from mdrunner.runlog import (
    argv_for_log,
    infer_failure_reason,
    parse_agent_session,
)


def test_argv_for_log_redacts_inlined_prompt():
    prompt = "A" * 5000
    argv = ["agy", "-p", prompt, "--model", "Gemini"]
    line = argv_for_log(argv, Path("/tmp/task.md"), prompt)
    assert prompt not in line
    assert "agy" in line
    assert "task.md" in line
    assert "5000" in line
    assert "--model" in line
    assert "Gemini" in line


def test_argv_for_log_keeps_short_args():
    argv = ["codex", "exec", "--yolo", "--model", "gpt-5.4", "-"]
    line = argv_for_log(argv, Path("/tmp/p.md"), None)
    assert "codex exec --yolo --model gpt-5.4 -" in line or "codex" in line
    assert "<prompt:" not in line


def test_parse_agent_session_codex_style():
    assert parse_agent_session("session id: 019f605e-15ee-7322-8957-2c6b0ec00155") == (
        "019f605e-15ee-7322-8957-2c6b0ec00155"
    )


def test_parse_agent_session_ignores_unrelated():
    assert parse_agent_session("Current session 24% used") is None
    assert parse_agent_session("hello world") is None


def test_infer_failure_reason_from_last_error_line():
    stdout = "working...\nError: timeout waiting for response\n"
    assert infer_failure_reason(stdout, exit_code=1, timed_out=False, error=None) == (
        "timeout waiting for response"
    )


def test_infer_failure_reason_timeout_flag():
    assert infer_failure_reason("still going", exit_code=None, timed_out=True, error=None) == (
        "mdrunner timeout"
    )


def test_infer_failure_reason_explicit_error_wins():
    assert infer_failure_reason("Error: x", exit_code=1, timed_out=False, error="interrupted") == (
        "interrupted"
    )
