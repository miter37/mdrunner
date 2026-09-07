"""Adapter argv-building tests.

These tests don't execute any CLI — they only verify that each adapter
composes the argv in the expected order with the expected flags.
"""

from __future__ import annotations

from pathlib import Path

from mdrunner.agents import get_adapter


def test_opencode_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("hello world", encoding="utf-8")
    adapter = get_adapter("opencode")
    res = adapter.build_argv(md, model="anthropic/claude-sonnet-4-5",
                             working_dir=None, extra_args=(), bypass_flags=("--auto",))
    assert res.argv[0] == "opencode"
    assert res.argv[1] == "run"
    assert res.argv[2] == "hello world"  # prompt inlined
    assert "--auto" in res.argv
    assert "--model" in res.argv
    assert res.argv[res.argv.index("--model") + 1] == "anthropic/claude-sonnet-4-5"


def test_opencode_no_model_no_bypass(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("body", encoding="utf-8")
    adapter = get_adapter("opencode")
    res = adapter.build_argv(md, model=None, working_dir=None,
                             extra_args=(), bypass_flags=())
    assert res.argv == ["opencode", "run", "body"]


def test_codex_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("codex prompt", encoding="utf-8")
    wd = Path("/tmp")
    adapter = get_adapter("codex")
    res = adapter.build_argv(md, model="gpt-5.4", working_dir=wd,
                             extra_args=("--sandbox", "workspace-write"),
                             bypass_flags=("--yolo",))
    assert res.argv[0] == "codex"
    assert res.argv[1] == "exec"
    assert res.argv[-1] == "-"
    assert "--cd" in res.argv
    assert str(wd) in res.argv
    assert "--yolo" in res.argv
    assert "--sandbox" in res.argv
    assert res.stdin_text == "codex prompt"


def test_codex_prompt_starting_with_frontmatter_goes_via_stdin(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("---\nname: demo\n---\nbody", encoding="utf-8")
    adapter = get_adapter("codex")
    res = adapter.build_argv(md, model="gpt-5.4", working_dir=None,
                             extra_args=(), bypass_flags=("--yolo",))
    assert res.argv[-1] == "-"
    assert "---\nname: demo\n---\nbody" == res.stdin_text


def test_codex_defaults_to_noninteractive_yolo_when_bypass_is_empty(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("codex prompt", encoding="utf-8")
    adapter = get_adapter("codex")
    res = adapter.build_argv(md, model="gpt-5.4", working_dir=None,
                             extra_args=(), bypass_flags=())
    assert "--yolo" in res.argv


def test_openclaw_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("ignored — file path is the message", encoding="utf-8")
    adapter = get_adapter("openclaw")
    res = adapter.build_argv(md, model="anthropic/claude-sonnet-4-5",
                             working_dir=None, extra_args=(),
                             bypass_flags=("--local",))
    assert res.argv[0] == "openclaw"
    assert res.argv[1] == "agent"
    assert "--local" in res.argv
    assert "--message-file" in res.argv
    # message-file points to the actual md path, not its text
    fi = res.argv.index("--message-file")
    assert res.argv[fi + 1] == str(md)
    assert "--model" in res.argv


def test_claude_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("claude prompt", encoding="utf-8")
    wd = Path("/tmp")
    adapter = get_adapter("claude")
    res = adapter.build_argv(
        md,
        model="glm-5.2",
        working_dir=wd,
        extra_args=("--verbose",),
        bypass_flags=("--dangerously-skip-permissions",),
    )
    assert res.argv[0] == "claude"
    assert res.argv[1] == "-p"
    assert res.argv[2] == "claude prompt"
    assert "--dangerously-skip-permissions" in res.argv
    assert "--model" in res.argv
    assert res.argv[res.argv.index("--model") + 1] == "glm-5.2"
    assert "--add-dir" in res.argv
    assert str(wd) in res.argv
    assert "--verbose" in res.argv


def test_claude_default_bypass(tmp_path: Path) -> None:
    """When bypass is empty, adapter should inject --dangerously-skip-permissions
    automatically (claude -p is non-interactive and hangs without it)."""
    md = tmp_path / "task.md"
    md.write_text("body", encoding="utf-8")
    adapter = get_adapter("claude")
    res = adapter.build_argv(md, model=None, working_dir=None,
                             extra_args=(), bypass_flags=())
    assert "--dangerously-skip-permissions" in res.argv


def test_agy_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("agy prompt", encoding="utf-8")
    wd = Path("/tmp")
    adapter = get_adapter("agy")
    res = adapter.build_argv(
        md,
        model="Gemini 3.5 Flash (Medium)",
        working_dir=wd,
        extra_args=(),
        bypass_flags=("--dangerously-skip-permissions",),
    )
    assert res.argv[0] == "agy"
    assert res.argv[1] == "-p"
    assert res.argv[2] == "agy prompt"
    assert "--dangerously-skip-permissions" in res.argv
    assert "--model" in res.argv
    assert res.argv[res.argv.index("--model") + 1] == "Gemini 3.5 Flash (Medium)"
    assert "--add-dir" in res.argv
    assert str(wd) in res.argv


def test_agy_default_bypass(tmp_path: Path) -> None:
    """When bypass is empty, adapter should inject --dangerously-skip-permissions."""
    md = tmp_path / "task.md"
    md.write_text("body", encoding="utf-8")
    adapter = get_adapter("agy")
    res = adapter.build_argv(md, model=None, working_dir=None,
                             extra_args=(), bypass_flags=())
    assert "--dangerously-skip-permissions" in res.argv


def test_hermes_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("hermes prompt", encoding="utf-8")
    wd = Path("/tmp")
    adapter = get_adapter("hermes")
    res = adapter.build_argv(
        md,
        model=None,  # user wants claude-code-style default model selection
        working_dir=wd,
        extra_args=(),
        bypass_flags=("--yolo",),
    )
    assert res.argv[0] == "hermes"
    assert res.argv[1] == "chat"
    assert "-q" in res.argv
    assert "-Q" in res.argv
    assert "hermes prompt" in res.argv
    assert "--yolo" in res.argv
    assert "--add-dir" in res.argv
    assert str(wd) in res.argv
    # No --model when model is None
    assert "--model" not in res.argv


def test_hermes_default_bypass(tmp_path: Path) -> None:
    """When bypass is empty, adapter should inject --yolo (hermes TUI hangs
    without it on tool-call requests)."""
    md = tmp_path / "task.md"
    md.write_text("body", encoding="utf-8")
    adapter = get_adapter("hermes")
    res = adapter.build_argv(md, model=None, working_dir=None,
                             extra_args=(), bypass_flags=())
    assert "--yolo" in res.argv


def test_grok_basic(tmp_path: Path) -> None:
    md = tmp_path / "task.md"
    md.write_text("ignored — file path is the prompt", encoding="utf-8")
    wd = Path("/tmp")
    adapter = get_adapter("grok")
    res = adapter.build_argv(
        md,
        model="grok-4.6",
        working_dir=wd,
        extra_args=("--reasoning-effort", "high"),
        bypass_flags=("--permission-mode", "bypassPermissions"),
    )
    assert res.argv[0] == "grok"
    assert "--prompt-file" in res.argv
    # prompt-file points at the md path, not its text
    assert res.argv[res.argv.index("--prompt-file") + 1] == str(md)
    assert "--permission-mode" in res.argv
    assert "bypassPermissions" in res.argv
    assert res.argv[res.argv.index("--model") + 1] == "grok-4.6"
    assert "--cwd" in res.argv and str(wd) in res.argv
    assert "--reasoning-effort" in res.argv


def test_grok_default_bypass(tmp_path: Path) -> None:
    """Empty bypass → adapter injects bypassPermissions (headless grok
    otherwise stalls on the first permission prompt)."""
    md = tmp_path / "task.md"
    md.write_text("body", encoding="utf-8")
    adapter = get_adapter("grok")
    res = adapter.build_argv(md, model=None, working_dir=None,
                             extra_args=(), bypass_flags=())
    assert res.argv == [
        "grok", "--prompt-file", str(md), "--permission-mode", "bypassPermissions",
    ]
