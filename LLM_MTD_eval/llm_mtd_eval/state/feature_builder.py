from __future__ import annotations

from ..types import NormalizedState


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def build_features(state: NormalizedState) -> dict[str, float]:
    qos = state.qos_context
    security = state.security_context
    controller = state.controller_context
    attack = state.attack_context

    qos_stress = _clamp(
        (
            (qos.sensor_to_gateway_latency_ms / 250.0)
            + (qos.edge_to_cloud_latency_ms / 250.0)
            + (qos.queue_length / 50.0)
            + qos.message_loss_rate
        )
        / 4.0
    )
    attack_pressure = _clamp(
        (
            attack.risk_score
            + (1.0 if security.gateway_seen else 0.0)
            + (1.0 if security.worker_seen else 0.0)
            + (1.0 if security.cloud_seen else 0.0)
            + (1.0 if security.attack_effect_success else 0.0)
        )
        / 5.0
    )
    controller_overhead = _clamp(
        (
            (controller.ryu_apply_duration_ms / 100.0)
            + (controller.flow_rules_installed / 10.0)
            + (controller.meters_added / 5.0)
            + (controller.active_policy_actions / 5.0)
        )
        / 4.0
    )
    service_degradation = _clamp((qos_stress + controller_overhead) / 2.0)

    return {
        "qos_stress_score": round(qos_stress, 4),
        "attack_pressure_score": round(attack_pressure, 4),
        "controller_overhead_score": round(controller_overhead, 4),
        "service_degradation_score": round(service_degradation, 4),
    }
