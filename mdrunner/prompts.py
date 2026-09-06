"""Inline prompt authoring — save instruction text typed in the UI as a
managed ``.md`` file so it can be used as a task's ``prompt_file``.

Headless on purpose (no Qt): the UI calls :func:`save_inline_prompt`, and
it is unit-testable on its own.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from .utils.paths import prompts_dir

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Filesystem-safe lowercase slug; falls back to ``prompt`` when empty."""
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug[:60] or "prompt"


def is_managed_prompt(path: str | Path | None) -> bool:
    """True when ``path`` lives inside the managed prompts folder."""
    if not path:
        return False
    try:
        Path(path).expanduser().resolve().relative_to(prompts_dir().resolve())
        return True
    except (ValueError, OSError):
        return False


def save_inline_prompt(
    text: str,
    *,
    name: str,
    existing_path: str | Path | None = None,
) -> Path:
    """Write ``text`` to a ``.md`` file in :func:`prompts_dir` and return it.

    When ``existing_path`` already points at a file inside the managed
    prompts folder, it is overwritten in place (editing an inline prompt).
    Otherwise a new ``<slug>-<timestamp>.md`` file is created.
    """
    body = text.strip() + "\n"
    if is_managed_prompt(existing_path):
        target = Path(existing_path).expanduser().resolve()  # type: ignore[arg-type]
    else:
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = prompts_dir() / f"{slugify(name)}-{stamp}.md"
        # Extremely unlikely, but never clobber on a same-second collision.
        n = 2
        while target.exists():
            target = prompts_dir() / f"{slugify(name)}-{stamp}-{n}.md"
            n += 1
    target.write_text(body, encoding="utf-8")
    return target
