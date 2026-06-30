from __future__ import annotations

from eval.types import NormalizedState


def compute_overhead_metrics(before: NormalizedState, after: NormalizedState | None = None) -> dict[str, float]:
    after_state = after or before
    return {
        "active_policy_actions_before": float(before.controller_context.active_policy_actions),
        "active_policy_actions_after": float(after_state.controller_context.active_policy_actions),
        "flow_rules_installed_before": float(before.controller_context.flow_rules_installed),
        "flow_rules_installed_after": float(after_state.controller_context.flow_rules_installed),
        "meters_added_before": float(before.controller_context.meters_added),
        "meters_added_after": float(after_state.controller_context.meters_added),
        "ryu_apply_duration_ms_before": before.controller_context.ryu_apply_duration_ms,
        "ryu_apply_duration_ms_after": after_state.controller_context.ryu_apply_duration_ms,
    }
