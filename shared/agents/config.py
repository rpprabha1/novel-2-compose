from __future__ import annotations

from pathlib import Path

import yaml


def load_agent_config(repo_root: Path) -> dict:
    return yaml.safe_load((repo_root / "config" / "agents.yaml").read_text(encoding="utf-8"))


def resolve_model(agent_config: dict, stage_name: str) -> str:
    return agent_config.get("stage_models", {}).get(
        stage_name, agent_config["ollama"]["default_model"]
    )
