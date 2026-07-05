from __future__ import annotations
import subprocess
import shutil
import json
from pathlib import Path

def fetch_agent_models(agent_id: str, binary_path: str | None = None) -> list[str]:
    """Return the best-known list of model identifiers for the given agent.

    Tries in order:
      1. a static curated list per agent (covers agents whose CLI doesn't
         expose a model catalog, like claude-code and codex)
      2. the agent's own `models` subcommand (works for opencode, agy)
      3. for hermes, the local models_dev cache that ships with hermes-agent

    Always returns a list (possibly empty) — never raises.
    """
    binary = binary_path or agent_id

    # 1) Static curated lists (claude / codex / openclaw don't expose a CLI model list)
    static_lists: dict[str, list[str]] = {
        "claude": ["default", "best", "fable", "sonnet", "opus", "haiku"],
        "codex": [
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex-spark",
        ],
        "openclaw": [
            "minimax-coding-plan/MiniMax-M3",
            "zai-coding-plan/glm-5.2",
        ],
    }
    if agent_id in static_lists:
        return list(static_lists[agent_id])

    # 2) Hermes: try its local models_dev cache, then a static fallback
    if agent_id == "hermes":
        cache_path = Path("~/.hermes/models_dev_cache.json").expanduser()
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                models: list[str] = []
                for prov, p_data in data.items():
                    if isinstance(p_data, dict) and "models" in p_data:
                        models.extend(p_data["models"].keys())
                if models:
                    return sorted(set(models))
            except Exception:
                pass
        # Hermes static fallback (matches hermes-agent's known default providers)
        return [
            "grok-4.3",
            "grok-4.20-0309-reasoning",
            "grok-4.20-0309-non-reasoning",
            "grok-4.20-multi-agent-0309",
        ]

    # 3) opencode / agy: dynamic via `<binary> models`
    if agent_id in ("opencode", "agy"):
        resolved = shutil.which(binary)
        if resolved:
            try:
                res = subprocess.run(
                    [resolved, "models"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode == 0:
                    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                    if lines:
                        return lines
            except Exception:
                pass
        # Dynamic failed — minimal fallback so the dropdown isn't empty
        if agent_id == "opencode":
            return []
        if agent_id == "agy":
            return [
                "Gemini 3.5 Flash (Medium)",
                "Gemini 3.5 Flash (High)",
                "Gemini 3.5 Flash (Low)",
                "Gemini 3.1 Pro (Low)",
                "Gemini 3.1 Pro (High)",
            ]

    # 4) Unknown agent — try `<binary> models` as a last resort
    resolved = shutil.which(binary)
    if resolved:
        try:
            res = subprocess.run(
                [resolved, "models"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if lines:
                    return lines
        except Exception:
            pass

    return []
