import importlib

import pytest


@pytest.fixture()
def prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNCHER_DATA_DIR", str(tmp_path / "data"))
    import mdrunner.utils.paths as paths_mod
    import mdrunner.prompts as prompts_mod

    importlib.reload(paths_mod)
    importlib.reload(prompts_mod)
    return prompts_mod


def test_slugify(prompts):
    assert prompts.slugify("Leader Speaks!") == "leader-speaks"
    assert prompts.slugify("  ") == "prompt"
    assert prompts.slugify("한글 Task") == "task"  # non-ascii stripped
    assert len(prompts.slugify("x" * 200)) == 60


def test_save_inline_prompt_creates_md(prompts):
    p = prompts.save_inline_prompt("do the thing", name="My Task")
    assert p.exists()
    assert p.suffix == ".md"
    assert p.parent == prompts.prompts_dir()
    assert p.name.startswith("my-task-")
    assert p.read_text(encoding="utf-8") == "do the thing\n"
    assert prompts.is_managed_prompt(p)


def test_save_inline_prompt_edits_in_place_when_managed(prompts):
    first = prompts.save_inline_prompt("v1", name="T")
    second = prompts.save_inline_prompt("v2 body", name="T", existing_path=str(first))
    assert second == first  # same file overwritten, not a new one
    assert second.read_text(encoding="utf-8") == "v2 body\n"
    assert list(prompts.prompts_dir().glob("*.md")) == [first]


def test_save_inline_prompt_ignores_unmanaged_existing_path(prompts, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("original", encoding="utf-8")
    saved = prompts.save_inline_prompt("new", name="T", existing_path=str(outside))
    assert saved != outside
    assert saved.parent == prompts.prompts_dir()
    assert outside.read_text(encoding="utf-8") == "original"  # untouched


def test_is_managed_prompt_false_cases(prompts, tmp_path):
    assert prompts.is_managed_prompt(None) is False
    assert prompts.is_managed_prompt("") is False
    assert prompts.is_managed_prompt(str(tmp_path / "x.md")) is False
