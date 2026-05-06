from __future__ import annotations

from typing import Any

from ..types import ActivePoolState


def build_active_pool_state(config: dict[str, Any] | None = None) -> ActivePoolState:
    cfg = config or {}
    return ActivePoolState(
        enabled=bool(cfg.get("enabled", False)),
        active_strategies=list(cfg.get("active_strategies") or []),
        pool_strategies=list(cfg.get("pool_strategies") or []),
    )
