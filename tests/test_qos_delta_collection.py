from defender.decision.stage_summarizer import _normalize_qos_delta_payload


def test_partial_llm_qos_payload_preserves_measured_deltas():
    measured = {"sensor_to_edge_latency_ms": 1.2, "edge_to_cloud_latency_ms": 2.3, "throughput_bytes_per_second": 4.5}
    result = _normalize_qos_delta_payload({"sensor_to_edge_latency_ms": 9.0}, measured)
    assert result["sensor_to_edge_latency_ms"] == 9.0
    assert result["edge_to_cloud_latency_ms"] == 2.3
    assert result["throughput_bytes_per_second"] == 4.5


def test_llm_qos_payload_accepts_legacy_delta_fallback_keys():
    measured = {
        "sensor_to_edge_latency_ms_delta": 1.2,
        "edge_to_cloud_latency_ms_delta": 2.3,
        "throughput_bytes_per_second_delta": 4.5,
        "loss_rate_delta": 0.1,
    }
    result = _normalize_qos_delta_payload({"sensor_to_edge_latency_ms": 9.0}, measured)
    assert result == {
        "sensor_to_edge_latency_ms": 9.0,
        "edge_to_cloud_latency_ms": 2.3,
        "throughput_bytes_per_second": 4.5,
        "loss_rate": 0.1,
    }
