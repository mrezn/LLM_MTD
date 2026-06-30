"""game — evolutionary attacker-defender game layer for LLM_MTD_modular."""
try:
    from .strategy_manager import StrategyManager
    from .game_model import evolutionary_step
    from .policy_selector import select_pair
except ImportError:
    pass

__all__ = [
    "StrategyManager",
    "evolutionary_step",
    "select_pair",
]
