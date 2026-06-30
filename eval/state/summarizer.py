from __future__ import annotations

import json
from typing import Any

from eval.types import NormalizedState


def summarize_for_prompt(state: NormalizedState, features: dict[str, Any]) -> dict[str, str]:
    return {
        "attack_context": json.dumps(state.attack_context.model_dump(), indent=2, sort_keys=True),
        "qos_context": json.dumps(
            {**state.qos_context.model_dump(), **features},
            indent=2,
            sort_keys=True,
        ),
        "security_context": json.dumps(state.security_context.model_dump(), indent=2, sort_keys=True),
        "controller_context": json.dumps(state.controller_context.model_dump(), indent=2, sort_keys=True),
        "allowed_actions": json.dumps(state.allowed_actions, indent=2),
        "active_pool_state": json.dumps(state.active_pool.model_dump(), indent=2, sort_keys=True),
    }


def summarize_for_logs(state: NormalizedState, features: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": state.scenario_id,
        "entry_node": state.entry_node,
        "target_asset": state.target_asset,
        "risk_score": state.attack_context.risk_score,
        "allowed_actions": list(state.allowed_actions),
        **features,
    }
