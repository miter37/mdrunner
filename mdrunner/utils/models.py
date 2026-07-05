from __future__ import annotations
import subprocess
import shutil
import json
from pathlib import Path

def fetch_agent_models(agent_id: str, binary_path: str | None = None) -> list[str]:
    binary = binary_path or agent_id

    if agent_id == "claude":
        return ["default", "best", "fable", "sonnet", "opus", "haiku"]

    if agent_id == "codex":
        return ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex-spark"]

    if agent_id == "openclaw":
        return ["minimax-coding-plan/MiniMax-M3", "zai-coding-plan/glm-5.2"]

    if agent_id == "hermes":
        cache_path = Path("~/.hermes/models_dev_cache.json").expanduser()
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                models = []
                for prov, p_data in data.items():
                    if isinstance(p_data, dict) and "models" in p_data:
                        models.extend(p_data["models"].keys())
                if models:
                    return sorted(list(set(models)))
            except Exception:
                pass
        return ["grok-4.3", "grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning", "grok-4.20-multi-agent-0309"]

    resolved = shutil.which(binary)
    if not resolved:
        return []

    if agent_id == "opencode":
        try:
            res = subprocess.run([resolved, "models"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                return lines
        except Exception:
            pass

    if agent_id == "agy":
        try:
            res = subprocess.run([resolved, "models"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                return lines
        except Exception:
            pass

    return []
