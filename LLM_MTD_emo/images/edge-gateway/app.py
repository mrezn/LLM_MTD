import queue
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
    env_json,
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


GATEWAY_ID = env("GATEWAY_ID", "edge-unknown-gw")
SENSOR_WORKER_MAP = env_json("SENSOR_WORKER_MAP", {})
METRICS_URL = env("METRICS_URL")
LOGGER_URL = env("LOGGER_URL")
MAX_QUEUE_SIZE = env_int("MAX_QUEUE_SIZE", 100)
REPORT_INTERVAL_SECONDS = env_float("REPORT_INTERVAL_SECONDS", 5.0)
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)

started_at = time.monotonic()

def normalize_destinations(value):
    if isinstance(value, str):
        return [value]
    return list(value)


worker_routes = {
    sensor_id: normalize_destinations(destinations)
    for sensor_id, destinations in SENSOR_WORKER_MAP.items()
}
sensor_queues = {
    sensor_id: queue.Queue(maxsize=MAX_QUEUE_SIZE) for sensor_id in worker_routes
}

state_lock = threading.Lock()
stats = {
    sensor_id: {
        "received": 0,
        "forwarded": 0,
        "dropped": 0,
        "forward_errors": 0,
        "received_bytes_total": 0,
        "forwarded_bytes_total": 0,
        "last_ingestion_latency_ms": 0.0,
        "total_ingestion_latency_ms": 0.0,
        "last_forwarding_delay_ms": 0.0,
        "total_forwarding_delay_ms": 0.0,
        "last_receive_timestamp": "",
    }
    for sensor_id in worker_routes
}
stats["unmapped"] = {
    "received": 0,
    "forwarded": 0,
    "dropped": 0,
    "forward_errors": 0,
    "received_bytes_total": 0,
    "forwarded_bytes_total": 0,
    "last_ingestion_latency_ms": 0.0,
    "total_ingestion_latency_ms": 0.0,
    "last_forwarding_delay_ms": 0.0,
    "total_forwarding_delay_ms": 0.0,
    "last_receive_timestamp": "",
}


def snapshot():
    with state_lock:
        return {
            sensor_id: {
                **values,
                "queue_length": sensor_queues[sensor_id].qsize()
                if sensor_id in sensor_queues
                else 0,
                "received_per_second": rate_per_second(values["received"], started_at),
                "forwarded_per_second": rate_per_second(values["forwarded"], started_at),
                "received_bytes_per_second": rate_per_second(
                    values["received_bytes_total"],
                    started_at,
                ),
                "forwarded_bytes_per_second": rate_per_second(
                    values["forwarded_bytes_total"],
                    started_at,
                ),
            }
            for sensor_id, values in stats.items()
        }


def memory_kb():
    if resource is None:
        return 0
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def service_metrics():
    return {
        "uptime_seconds": time.monotonic() - started_at,
        "cpu_seconds": time.process_time(),
        "memory_kb": memory_kb(),
    }


def forward_loop(sensor_id):
    destinations = worker_routes[sensor_id]
    sensor_queue = sensor_queues[sensor_id]

    while True:
        payload = sensor_queue.get()
        payload["gateway_id"] = GATEWAY_ID
        payload["gateway_forwarded_at"] = now_iso()

        for destination_url in destinations:
            result = post_json(destination_url, payload, timeout=HTTP_TIMEOUT_SECONDS)
            with state_lock:
                if result["ok"]:
                    stats[sensor_id]["forwarded"] += 1
                    stats[sensor_id]["forwarded_bytes_total"] += result.get(
                        "request_bytes",
                        0,
                    )
                    stats[sensor_id]["last_forwarding_delay_ms"] = result["elapsed_ms"]
                    stats[sensor_id]["total_forwarding_delay_ms"] += result["elapsed_ms"]
                else:
                    stats[sensor_id]["forward_errors"] += 1

        sensor_queue.task_done()


def metrics_report_loop():
    while True:
        time.sleep(REPORT_INTERVAL_SECONDS)
        metrics_snapshot = snapshot()

        if METRICS_URL:
            post_json(
                METRICS_URL,
                {
                    "source": GATEWAY_ID,
                    "role": "edge_gateway",
                    "timestamp": now_iso(),
                    "metrics": {
                        "sensors": metrics_snapshot,
                        "service": service_metrics(),
                    },
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )

        if LOGGER_URL:
            post_json(
                LOGGER_URL,
                {
                    "event_type": "edge_gateway_metrics",
                    "source": GATEWAY_ID,
                    "timestamp": now_iso(),
                    "metrics": metrics_snapshot,
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "edge-gateway",
                    "gateway_id": GATEWAY_ID,
                    "routes": worker_routes,
                    "metrics_url": METRICS_URL,
                    "logger_url": LOGGER_URL,
                    "service_stats": service_metrics(),
                    "stats": snapshot(),
                },
            )
            return

        if path == "/routes":
            send_json(self, 200, {"gateway_id": GATEWAY_ID, "routes": worker_routes})
            return

        if path == "/metrics":
            lines = []
            metrics_snapshot = snapshot()
            service_labels = {"gateway": GATEWAY_ID}
            lines.extend(
                [
                    metric_line("edge_gateway_cpu_seconds", service_metrics()["cpu_seconds"], service_labels),
                    metric_line("edge_gateway_memory_kb", service_metrics()["memory_kb"], service_labels),
                ]
            )
            for sensor_id, values in metrics_snapshot.items():
                labels = {"gateway": GATEWAY_ID, "sensor_id": sensor_id}
                lines.extend(
                    [
                        metric_line("edge_gateway_packets_total", values["received"], {**labels, "state": "received"}),
                        metric_line("edge_gateway_packets_total", values["forwarded"], {**labels, "state": "forwarded"}),
                        metric_line("edge_gateway_packets_total", values["dropped"], {**labels, "state": "dropped"}),
                        metric_line("edge_gateway_forward_errors_total", values["forward_errors"], labels),
                        metric_line("edge_gateway_queue_length", values["queue_length"], labels),
                        metric_line("edge_gateway_received_bytes_total", values["received_bytes_total"], labels),
                        metric_line("edge_gateway_forwarded_bytes_total", values["forwarded_bytes_total"], labels),
                        metric_line("edge_gateway_received_per_second", values["received_per_second"], labels),
                        metric_line("edge_gateway_forwarded_per_second", values["forwarded_per_second"], labels),
                        metric_line("edge_gateway_last_ingestion_latency_ms", values["last_ingestion_latency_ms"], labels),
                        metric_line("edge_gateway_last_forwarding_delay_ms", values["last_forwarding_delay_ms"], labels),
                    ]
                )
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        if path != "/telemetry":
            send_json(self, 404, {"error": "not found"})
            return

        payload, request_bytes = read_json_with_size(self)
        sensor_id = payload.get("sensor_id")
        gateway_received_at = now_iso()
        ingestion_latency_ms = elapsed_ms_between(
            payload.get("sensor_sent_at") or payload.get("timestamp"),
            gateway_received_at,
        )
        if ingestion_latency_ms is None:
            ingestion_latency_ms = 0.0
        payload["gateway_received_at"] = gateway_received_at
        payload["sensor_to_gateway_latency_ms"] = ingestion_latency_ms

        if sensor_id not in sensor_queues:
            with state_lock:
                stats["unmapped"]["received"] += 1
                stats["unmapped"]["dropped"] += 1
                stats["unmapped"]["received_bytes_total"] += request_bytes
                stats["unmapped"]["last_ingestion_latency_ms"] = ingestion_latency_ms
                stats["unmapped"]["total_ingestion_latency_ms"] += ingestion_latency_ms
                stats["unmapped"]["last_receive_timestamp"] = gateway_received_at
            send_json(self, 404, {"error": "sensor has no worker route", "sensor_id": sensor_id})
            return

        try:
            sensor_queues[sensor_id].put_nowait(payload)
        except queue.Full:
            with state_lock:
                stats[sensor_id]["received"] += 1
                stats[sensor_id]["dropped"] += 1
                stats[sensor_id]["received_bytes_total"] += request_bytes
                stats[sensor_id]["last_ingestion_latency_ms"] = ingestion_latency_ms
                stats[sensor_id]["total_ingestion_latency_ms"] += ingestion_latency_ms
                stats[sensor_id]["last_receive_timestamp"] = gateway_received_at
            send_json(self, 503, {"error": "gateway queue is full", "sensor_id": sensor_id})
            return

        with state_lock:
            stats[sensor_id]["received"] += 1
            stats[sensor_id]["received_bytes_total"] += request_bytes
            stats[sensor_id]["last_ingestion_latency_ms"] = ingestion_latency_ms
            stats[sensor_id]["total_ingestion_latency_ms"] += ingestion_latency_ms
            stats[sensor_id]["last_receive_timestamp"] = gateway_received_at
        send_json(
            self,
            202,
            {
                "status": "queued",
                "sensor_id": sensor_id,
                "gateway_id": GATEWAY_ID,
                "sensor_to_gateway_latency_ms": ingestion_latency_ms,
            },
        )


def main():
    print(
        f"edge-gateway starting gateway_id={GATEWAY_ID} routes={list(worker_routes)} port={PORT}",
        flush=True,
    )
    for sensor_id in worker_routes:
        worker_thread = threading.Thread(target=forward_loop, args=(sensor_id,), daemon=True)
        worker_thread.start()

    reporter_thread = threading.Thread(target=metrics_report_loop, daemon=True)
    reporter_thread.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
