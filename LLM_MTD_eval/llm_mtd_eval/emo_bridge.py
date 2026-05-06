from __future__ import annotations

from dataclasses import dataclass
import importlib
import sys
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class EmoStrategyModules:
    game_model: ModuleType
    policy_selector: ModuleType
    stage_transition: ModuleType
    state_builder: ModuleType
    strategy_manager: ModuleType
    strategy_runtime: ModuleType


def strategy_dir(workspace_root: Path) -> Path:
    return workspace_root / "LLM_MTD_emo" / "integrations" / "strategy"


def ensure_strategy_import_path(workspace_root: Path) -> Path:
    resolved = strategy_dir(workspace_root).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Could not find LLM_MTD_emo strategy directory: {resolved}")

    strategy_path = str(resolved)
    if strategy_path not in sys.path:
        sys.path.insert(0, strategy_path)
    return resolved


def load_emo_strategy_modules(workspace_root: Path) -> EmoStrategyModules:
    ensure_strategy_import_path(workspace_root)
    return EmoStrategyModules(
        game_model=importlib.import_module("game_model"),
        policy_selector=importlib.import_module("policy_selector"),
        stage_transition=importlib.import_module("stage_transition"),
        state_builder=importlib.import_module("state_builder"),
        strategy_manager=importlib.import_module("strategy_manager"),
        strategy_runtime=importlib.import_module("strategy_runtime"),
    )
