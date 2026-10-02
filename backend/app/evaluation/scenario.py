from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..schemas import WorkflowDefinition


SCENARIO_DIR = Path(__file__).resolve().parents[2] / "evals" / "scenarios"


def list_scenarios() -> list[str]:
    return sorted(path.stem for path in SCENARIO_DIR.glob("*.json"))


def load_scenario(name: str) -> tuple[dict[str, Any], WorkflowDefinition]:
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError("invalid scenario name")
    path = SCENARIO_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"evaluation scenario not found: {name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("workflow"), dict):
        raise ValueError(f"invalid evaluation scenario: {name}")
    return raw, WorkflowDefinition.model_validate(raw["workflow"])
