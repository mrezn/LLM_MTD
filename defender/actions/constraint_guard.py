from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eval.settings import SUPPORTED_EXECUTABLE_ACTIONS
from eval.types import LLMDecision, NormalizedState


@dataclass(slots=True)
class GuardResult:
    executable: bool
    issues: list[str] = field(default_factory=list)
    sanitized_parameters: dict[str, Any] = field(default_factory=dict)


class ConstraintGuard:
    def __init__(self, supported_actions: list[str] | None = None) -> None:
        self.supported_actions = supported_actions or list(SUPPORTED_EXECUTABLE_ACTIONS)

    def evaluate(self, decision: LLMDecision, state: NormalizedState) -> GuardResult:
        issues: list[str] = []
        parameters = dict(decision.parameters)
        action = decision.selected_defender_strategy

        if action not in self.supported_actions:
            issues.append(f"unsupported action: {action}")

        allowed_targets = {
            state.entry_node,
            state.target_asset,
            *state.attack_context.mulval_path,
            "",
        }
        if action != "observe" and decision.target not in allowed_targets:
            issues.append(f"target not present in normalized state context: {decision.target}")

        if action == "rate_limit":
            kbps = parameters.get("kbps")
            try:
                kbps = int(kbps)
            except (TypeError, ValueError):
                kbps = 0
            if kbps <= 0:
                issues.append("rate_limit requires a positive kbps parameter")
            else:
                parameters["kbps"] = kbps

        if action == "reroute_traffic" and "via" in parameters:
            via = str(parameters.get("via", "")).strip()
            if via and not via.startswith("s_edge"):
                issues.append(f"reroute via must look like an edge switch id: {via}")

        return GuardResult(
            executable=not issues,
            issues=issues,
            sanitized_parameters=parameters,
        )
