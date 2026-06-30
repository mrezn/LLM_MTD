import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import resource
except ImportError:
    resource = None

from mtd_common import (
    env,
    env_float,
    env_int,
    elapsed_ms_between,
    metric_line,
    now_iso,
    post_json,
    rate_per_second,
    read_json_with_size,
    route_path,
    send_json,
    send_text,
)


WORKER_ID = env("WORKER_ID", "edge-worker-unknown")
EDGE_ID = env("EDGE_ID", "edge-unknown")
ASSIGNED_SENSOR = env("ASSIGNED_SENSOR", "")
CLOUD_SUMMARY_URL = env("CLOUD_SUMMARY_URL")
METRICS_URL = env("METRICS_URL")
LOGGER_URL = env("LOGGER_URL")
SUMMARY_EVERY = env_int("SUMMARY_EVERY", 5)
PROCESSING_DELAY_SECONDS = env_float("PROCESSING_DELAY_SECONDS", 0.0)
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)

started_at = time.monotonic()
state_lock = threading.Lock()
stats = {
    "requests_total": 0,
    "rejected_total": 0,
    "summaries_total": 0,
    "summary_errors_total": 0,
    "received_bytes_total": 0,
    "summary_bytes_total": 0,
    "last_latency_ms": 0.0,
    "total_latency_ms": 0.0,
    "last_gateway_to_worker_latency_ms": 0.0,
    "total_gateway_to_worker_latency_ms": 0.0,
    "last_sequence": 0,
    "last_successful_summary_at": "",
    "previous_successful_summary_at": "",
    "last_success_gap_ms": 0.0,
    "max_success_gap_ms": 0.0,
}
recent_values = []


def snapshot():
    with state_lock:
        return {
            **stats,
            "cpu_seconds": time.process_time(),
            "memory_kb": memory_kb(),
            "requests_per_second": rate_per_second(stats["requests_total"], started_at),
            "summaries_per_second": rate_per_second(stats["summaries_total"], started_at),
            "received_bytes_per_second": rate_per_second(
                stats["received_bytes_total"],
                started_at,
            ),
            "summary_bytes_per_second": rate_per_second(
                stats["summary_bytes_total"],
                started_at,
            ),
        }


def process_payload(payload, worker_received_at):
    value = float(payload.get("value", 0.0))
    recent_values.append(value)
    del recent_values[:-20]

    features = {
        "value": value,
        "rolling_count": len(recent_values),
        "rolling_avg": statistics.fmean(recent_values),
        "rolling_min": min(recent_values),
        "rolling_max": max(recent_values),
    }
    return {
        "worker_id": WORKER_ID,
        "edge_id": EDGE_ID,
        "sensor_id": payload.get("sensor_id"),
        "sequence": payload.get("sequence"),
        "timestamp": now_iso(),
        "sensor_sent_at": payload.get("sensor_sent_at") or payload.get("timestamp"),
        "gateway_received_at": payload.get("gateway_received_at"),
        "gateway_forwarded_at": payload.get("gateway_forwarded_at"),
        "worker_received_at": worker_received_at,
        "sensor_to_gateway_latency_ms": payload.get("sensor_to_gateway_latency_ms", 0.0),
        "features": features,
    }


def maybe_forward_summary(summary):
    summary["worker_summary_sent_at"] = now_iso()
    if not CLOUD_SUMMARY_URL:
        return {
            "ok": True,
            "status": 0,
            "elapsed_ms": 0.0,
            "request_bytes": 0,
            "error": "",
        }
    result = post_json(CLOUD_SUMMARY_URL, summary, timeout=HTTP_TIMEOUT_SECONDS)
    summary["cloud_post_latency_ms"] = result["elapsed_ms"]
    summary["cloud_post_status"] = result["status"]
    summary["cloud_post_error"] = result["error"]
    return result


def report_metrics():
    if not METRICS_URL:
        return
    payload = {
        "source": WORKER_ID,
        "role": "edge_worker",
        "timestamp": now_iso(),
        "metrics": snapshot(),
    }
    post_json(METRICS_URL, payload, timeout=HTTP_TIMEOUT_SECONDS)


def memory_kb():
    if resource is None:
        return 0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


class WorkerHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            payload = {
                "service": "edge-worker",
                "worker_id": WORKER_ID,
                "edge_id": EDGE_ID,
                "assigned_sensor": ASSIGNED_SENSOR,
                "summary_url": CLOUD_SUMMARY_URL,
                "metrics_url": METRICS_URL,
                "logger_url": LOGGER_URL,
                "stats": snapshot(),
            }
            send_json(self, 200, payload)
            return

        if path == "/metrics":
            values = snapshot()
            labels = {"worker": WORKER_ID, "edge": EDGE_ID, "sensor_id": ASSIGNED_SENSOR}
            lines = [
                metric_line("edge_worker_requests_total", values["requests_total"], labels),
                metric_line("edge_worker_rejected_total", values["rejected_total"], labels),
                metric_line("edge_worker_summaries_total", values["summaries_total"], labels),
                metric_line("edge_worker_summary_errors_total", values["summary_errors_total"], labels),
                metric_line("edge_worker_received_bytes_total", values["received_bytes_total"], labels),
                metric_line("edge_worker_summary_bytes_total", values["summary_bytes_total"], labels),
                metric_line("edge_worker_requests_per_second", values["requests_per_second"], labels),
                metric_line("edge_worker_summaries_per_second", values["summaries_per_second"], labels),
                metric_line("edge_worker_last_latency_ms", values["last_latency_ms"], labels),
                metric_line("edge_worker_last_gateway_to_worker_latency_ms", values["last_gateway_to_worker_latency_ms"], labels),
                metric_line("edge_worker_last_success_gap_ms", values["last_success_gap_ms"], labels),
                metric_line("edge_worker_max_success_gap_ms", values["max_success_gap_ms"], labels),
                metric_line("edge_worker_cpu_seconds", values["cpu_seconds"], labels),
                metric_line("edge_worker_memory_kb", values["memory_kb"], labels),
            ]
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        if path not in ("/process", "/telemetry"):
            send_json(self, 404, {"error": "not found"})
            return

        processing_started_at = time.monotonic()
        worker_received_at = now_iso()
        payload, request_bytes = read_json_with_size(self)
        sensor_id = payload.get("sensor_id")
        gateway_to_worker_latency_ms = elapsed_ms_between(
            payload.get("gateway_forwarded_at"),
            worker_received_at,
        )
        if gateway_to_worker_latency_ms is None:
            gateway_to_worker_latency_ms = 0.0

        if ASSIGNED_SENSOR and sensor_id != ASSIGNED_SENSOR:
            with state_lock:
                stats["rejected_total"] += 1
                stats["received_bytes_total"] += request_bytes
            send_json(
                self,
                409,
                {
                    "error": "worker assigned to a different sensor",
                    "assigned_sensor": ASSIGNED_SENSOR,
                    "received_sensor": sensor_id,
                },
            )
            return

        if PROCESSING_DELAY_SECONDS > 0:
            time.sleep(PROCESSING_DELAY_SECONDS)

        summary = process_payload(payload, worker_received_at)
        summary["gateway_to_worker_latency_ms"] = gateway_to_worker_latency_ms
        latency_ms = (time.monotonic() - processing_started_at) * 1000
        summary["worker_processing_latency_ms"] = latency_ms

        sequence = summary["sequence"] or 0
        should_forward = SUMMARY_EVERY > 0 and sequence % SUMMARY_EVERY == 0
        summary_post_result = {
            "ok": True,
            "status": 0,
            "elapsed_ms": 0.0,
            "request_bytes": 0,
            "error": "",
        }
        if should_forward:
            summary_post_result = maybe_forward_summary(summary)
        forwarded = summary_post_result["ok"]

        with state_lock:
            stats["requests_total"] += 1
            stats["received_bytes_total"] += request_bytes
            stats["last_latency_ms"] = latency_ms
            stats["total_latency_ms"] += latency_ms
            stats["last_gateway_to_worker_latency_ms"] = gateway_to_worker_latency_ms
            stats["total_gateway_to_worker_latency_ms"] += gateway_to_worker_latency_ms
            stats["last_sequence"] = summary["sequence"]
            if should_forward and forwarded:
                stats["summaries_total"] += 1
                stats["summary_bytes_total"] += summary_post_result.get("request_bytes", 0)
                previous_success = stats["last_successful_summary_at"]
                stats["previous_successful_summary_at"] = previous_success
                stats["last_successful_summary_at"] = summary["worker_summary_sent_at"]
                success_gap_ms = elapsed_ms_between(
                    previous_success,
                    summary["worker_summary_sent_at"],
                ) if previous_success else 0.0
                if success_gap_ms is None:
                    success_gap_ms = 0.0
                stats["last_success_gap_ms"] = success_gap_ms
                stats["max_success_gap_ms"] = max(stats["max_success_gap_ms"], success_gap_ms)
            elif should_forward:
                stats["summary_errors_total"] += 1

        if LOGGER_URL:
            post_json(
                LOGGER_URL,
                {
                    "event_type": "edge_worker_processed",
                    "source": WORKER_ID,
                    "timestamp": now_iso(),
                    "summary": summary,
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )

        report_metrics()
        send_json(self, 200, {"status": "processed", "summary": summary, "latency_ms": latency_ms})


def main():
    print(
        f"edge-worker starting worker_id={WORKER_ID} assigned_sensor={ASSIGNED_SENSOR} port={PORT}",
        flush=True,
    )
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WorkerHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
