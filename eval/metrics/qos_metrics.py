from __future__ import annotations

from eval.types import NormalizedState


def compute_qos_metrics(before: NormalizedState, after: NormalizedState | None = None) -> dict[str, float]:
    after_state = after or before
    return {
        "sensor_to_gateway_latency_ms_before": before.qos_context.sensor_to_gateway_latency_ms,
        "sensor_to_gateway_latency_ms_after": after_state.qos_context.sensor_to_gateway_latency_ms,
        "gateway_to_worker_latency_ms_before": before.qos_context.gateway_to_worker_latency_ms,
        "gateway_to_worker_latency_ms_after": after_state.qos_context.gateway_to_worker_latency_ms,
        "edge_to_cloud_latency_ms_before": before.qos_context.edge_to_cloud_latency_ms,
        "edge_to_cloud_latency_ms_after": after_state.qos_context.edge_to_cloud_latency_ms,
        "queue_length_before": float(before.qos_context.queue_length),
        "queue_length_after": float(after_state.qos_context.queue_length),
        "throughput_bps_before": before.qos_context.throughput_bps,
        "throughput_bps_after": after_state.qos_context.throughput_bps,
        "message_loss_rate_before": before.qos_context.message_loss_rate,
        "message_loss_rate_after": after_state.qos_context.message_loss_rate,
    }
