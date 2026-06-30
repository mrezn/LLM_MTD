from __future__ import annotations

from eval.types import NormalizedState


def compute_security_metrics(before: NormalizedState, after: NormalizedState | None = None) -> dict[str, float]:
    after_state = after or before
    return {
        "gateway_seen_before": float(before.security_context.gateway_seen),
        "gateway_seen_after": float(after_state.security_context.gateway_seen),
        "worker_seen_before": float(before.security_context.worker_seen),
        "worker_seen_after": float(after_state.security_context.worker_seen),
        "cloud_seen_before": float(before.security_context.cloud_seen),
        "cloud_seen_after": float(after_state.security_context.cloud_seen),
        "attack_effect_success_before": float(before.security_context.attack_effect_success),
        "attack_effect_success_after": float(after_state.security_context.attack_effect_success),
        "defense_success_before": float(before.security_context.defense_success),
        "defense_success_after": float(after_state.security_context.defense_success),
        "risk_score": before.attack_context.risk_score,
    }
