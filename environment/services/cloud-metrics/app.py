import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mtd_common import (
    env_int,
    metric_line,
    now_iso,
    read_json,
    route_path,
    send_json,
    send_text,
)


PORT = env_int("PORT", 8000)

state_lock = threading.RLock()
samples = []
latest_values = {}
attack_events = []
attack_event_counts = {}
latest_attack_results = {}
latest_defense_results = {}


def flatten_metrics(prefix, value):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten_metrics(nested_prefix, nested_value)
    elif isinstance(value, (int, float)):
        yield prefix, value


def record_sample(payload):
    source = payload.get("source", "unknown")
    role = payload.get("role", "unknown")
    timestamp = payload.get("timestamp", now_iso())
    metrics = payload.get("metrics", {})

    with state_lock:
        samples.append({"source": source, "role": role, "timestamp": timestamp, "metrics": metrics})
        del samples[:-500:]
        for metric_name, value in flatten_metrics("", metrics):
            latest_values[(source, role, metric_name)] = value


def record_attack_event(event):
    scenario_id = str(event.get("scenario_id", "unknown") or "unknown")
    event_type = str(event.get("event_type", "attack_event") or "attack_event")
    tool = str(event.get("tool", "unknown") or "unknown")
    timestamp = event.get("timestamp", now_iso())
    entry_node = str(event.get("entry_node", "") or "")
    role = "defender" if event_type == "defense_result" else "attacker"

    with state_lock:
        attack_events.append({**event, "timestamp": timestamp})
        del attack_events[:-500:]

        count_key = (scenario_id, event_type, tool)
        attack_event_counts[count_key] = attack_event_counts.get(count_key, 0) + 1

        metrics = {
            "events_total": attack_event_counts[count_key],
            "attack_active": 1 if event_type == "attack_start" else 0,
        }
        if entry_node:
            metrics["entry_node_present"] = 1

        signals = event.get("signals", {})
        if isinstance(signals, dict):
            for metric_name, value in flatten_metrics("", signals):
                metrics[metric_name] = value

        for field in (
            "success",
            "gateway_seen",
            "worker_seen",
            "worker_requests_increase",
            "cloud_seen",
            "cloud_summary_rate_changes",
            "gateway_queue_spike",
            "attack_effect_success",
            "defense_success",
            "drop_rules_active",
            "counters_stopped",
        ):
            if field in event:
                metrics[field] = bool_metric(event[field])

        if event_type == "attack_result":
            latest_attack_results[scenario_id] = dict(event)
            metrics["attack_active"] = 0
        if event_type == "defense_result":
            latest_defense_results[scenario_id] = dict(event)
            metrics["attack_active"] = 0

        samples.append(
            {
                "source": scenario_id,
                "role": role,
                "timestamp": timestamp,
                "event_type": event_type,
                "metrics": metrics,
            }
        )
        del samples[:-500:]

        for metric_name, value in flatten_metrics("", metrics):
            latest_values[(scenario_id, role, metric_name)] = value


def matching_latest_values(predicate):
    rows = []
    for (source, role, metric_name), value in sorted(latest_values.items()):
        if predicate(source, role, metric_name):
            rows.append(
                {
                    "source": source,
                    "role": role,
                    "metric": metric_name,
                    "value": value,
                }
            )
    return rows


def core_measurements():
    with state_lock:
        return {
            "generated_at": now_iso(),
            "sample_count": len(samples),
            "sensor_to_edge_latency_ms": matching_latest_values(
                lambda _source, role, metric: role == "edge_gateway"
                and metric.endswith("last_ingestion_latency_ms")
            ),
            "edge_to_cloud_latency_ms": matching_latest_values(
                lambda _source, role, metric: role == "cloud_db"
                and metric.endswith("last_edge_to_cloud_latency_ms")
            ),
            "message_loss_counters": matching_latest_values(
                lambda _source, _role, metric: any(
                    token in metric
                    for token in (
                        "generated_total",
                        "received",
                        "forwarded",
                        "dropped",
                        "requests_total",
                        "summaries_total",
                        "summary_errors_total",
                        "records_total",
                        "storage_confirmations_total",
                    )
                )
            ),
            "throughput": matching_latest_values(
                lambda _source, _role, metric: "per_second" in metric
                or metric.endswith("bytes_total")
            ),
            "service_downtime_ms": matching_latest_values(
                lambda _source, role, metric: role == "edge_worker"
                and "success_gap_ms" in metric
            ),
            "resource_use": matching_latest_values(
                lambda _source, _role, metric: "cpu_seconds" in metric
                or "memory_kb" in metric
            ),
            "controller_overhead": matching_latest_values(
                lambda _source, role, metric: role == "ryu_controller"
                or metric.startswith("controller.")
            ),
            "attack_events": matching_latest_values(
                lambda _source, role, _metric: role == "attacker"
            ),
            "defense_events": matching_latest_values(
                lambda _source, role, _metric: role == "defender"
            ),
        }


def bool_metric(value):
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "ok", "success") else 0
    return 0


class MetricsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            with state_lock:
                payload = {
                    "service": "cloud-metrics",
                    "sample_count": len(samples),
                    "latest_value_count": len(latest_values),
                    "attack_event_count": len(attack_events),
                    "defense_result_count": len(latest_defense_results),
                }
            send_json(self, 200, payload)
            return

        if path == "/samples":
            with state_lock:
                payload = {"samples": list(samples[-50:])}
            send_json(self, 200, payload)
            return

        if path in ("/core", "/experiment/summary"):
            send_json(self, 200, core_measurements())
            return

        if path == "/metrics":
            lines = []
            with state_lock:
                lines.append(metric_line("cloud_metrics_samples_total", len(samples), {}))
                for (source, role, metric_name), value in sorted(latest_values.items()):
                    labels = {"source": source, "role": role, "metric": metric_name}
                    lines.append(metric_line("cloud_metrics_latest_value", value, labels))
                for (scenario_id, event_type, tool), value in sorted(attack_event_counts.items()):
                    labels = {
                        "scenario_id": scenario_id,
                        "event_type": event_type,
                        "tool": tool,
                    }
                    lines.append(metric_line("attack_events_total", value, labels))
                for scenario_id, event in sorted(latest_attack_results.items()):
                    labels = {"scenario_id": scenario_id}
                    for field in (
                        "success",
                        "gateway_seen",
                        "worker_seen",
                        "worker_requests_increase",
                        "cloud_seen",
                        "cloud_summary_rate_changes",
                        "gateway_queue_spike",
                        "attack_effect_success",
                        "defense_success",
                    ):
                        if field in event:
                            lines.append(
                                metric_line(
                                    f"attack_latest_{field}",
                                    bool_metric(event[field]),
                                    labels,
                                )
                            )
                for scenario_id, event in sorted(latest_defense_results.items()):
                    labels = {"scenario_id": scenario_id}
                    if "defense_success" in event:
                        lines.append(
                            metric_line(
                                "defense_latest_success",
                                bool_metric(event["defense_success"]),
                                labels,
                            )
                        )
                    signals = event.get("signals", {})
                    if isinstance(signals, dict):
                        for field in (
                            "controller_action_duration_ms",
                            "ovs_drop_counter_delta",
                            "drop_rules_active",
                            "counters_stopped",
                        ):
                            if field in signals:
                                value = (
                                    bool_metric(signals[field])
                                    if isinstance(signals[field], bool)
                                    else signals[field]
                                )
                                lines.append(
                                    metric_line(f"defense_latest_{field}", value, labels)
                                )
                core = core_measurements()
                for group_name, rows in core.items():
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        labels = {
                            "group": group_name,
                            "source": row["source"],
                            "role": row["role"],
                            "metric": row["metric"],
                        }
                        lines.append(metric_line("cloud_metrics_core_value", row["value"], labels))
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        if path in ("/attack/event", "/defense/event", "/experiment/event"):
            payload = read_json(self)
            record_attack_event(payload)
            send_json(self, 202, {"status": "recorded", "event_type": payload.get("event_type")})
            return

        if path != "/metrics":
            send_json(self, 404, {"error": "not found"})
            return

        payload = read_json(self)
        record_sample(payload)
        send_json(self, 202, {"status": "recorded"})


def main():
    print(f"cloud-metrics starting port={PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
