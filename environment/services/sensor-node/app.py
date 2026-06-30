import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mtd_common import (
    env,
    env_csv,
    env_float,
    env_int,
    metric_line,
    now_iso,
    post_json,
    rate_per_second,
    route_path,
    send_json,
    send_text,
)


SENSOR_ID = env("SENSOR_ID", "sen-unknown")
PAYLOAD_TYPE = env("PAYLOAD_TYPE", "telemetry")
DESTINATION_URLS = env_csv("DESTINATION_URLS") or env_csv("DESTINATION_URL")
METRICS_URL = env("METRICS_URL")
SEND_INTERVAL_SECONDS = env_float("SEND_INTERVAL_SECONDS", 2.0)
REPORT_INTERVAL_SECONDS = env_float("REPORT_INTERVAL_SECONDS", 5.0)
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)

started_at = time.monotonic()
state_lock = threading.Lock()
state = {
    "sequence": 0,
    "generated_total": 0,
    "sent_total": 0,
    "failed_total": 0,
    "sent_bytes_total": 0,
    "failed_bytes_total": 0,
    "last_payload_timestamp": "",
    "last_errors": [],
}


def build_payload(sequence):
    return {
        "sensor_id": SENSOR_ID,
        "timestamp": now_iso(),
        "payload_type": PAYLOAD_TYPE,
        "sequence": sequence,
        "value": round(random.uniform(0.0, 100.0), 3),
        "quality": random.choice(["nominal", "nominal", "nominal", "noisy"]),
    }


def record_result(payload, result):
    with state_lock:
        if result["ok"]:
            state["sent_total"] += 1
            state["sent_bytes_total"] += result.get("request_bytes", 0)
        else:
            state["failed_total"] += 1
            state["failed_bytes_total"] += result.get("request_bytes", 0)
            state["last_errors"].append(
                {
                    "timestamp": now_iso(),
                    "sequence": payload["sequence"],
                    "status": result["status"],
                    "error": result["error"],
                }
            )
            state["last_errors"] = state["last_errors"][-10:]
        state["last_payload_timestamp"] = payload["timestamp"]


def snapshot():
    with state_lock:
        return {
            **state,
            "uptime_seconds": time.monotonic() - started_at,
            "generated_per_second": rate_per_second(state["generated_total"], started_at),
            "sent_per_second": rate_per_second(state["sent_total"], started_at),
            "sent_bytes_per_second": rate_per_second(state["sent_bytes_total"], started_at),
        }


def metrics_report_loop():
    while True:
        time.sleep(REPORT_INTERVAL_SECONDS)
        if not METRICS_URL:
            continue
        post_json(
            METRICS_URL,
            {
                "source": SENSOR_ID,
                "role": "sensor",
                "timestamp": now_iso(),
                "metrics": snapshot(),
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )


def telemetry_loop():
    while True:
        with state_lock:
            state["sequence"] += 1
            state["generated_total"] += 1
            sequence = state["sequence"]

        payload = build_payload(sequence)
        if not DESTINATION_URLS:
            record_result(payload, {"ok": False, "status": 0, "error": "no destination"})
        else:
            for destination_url in DESTINATION_URLS:
                outbound_payload = {
                    **payload,
                    "sensor_sent_at": now_iso(),
                    "sensor_destination_url": destination_url,
                }
                result = post_json(
                    destination_url,
                    outbound_payload,
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
                record_result(outbound_payload, result)

        time.sleep(SEND_INTERVAL_SECONDS)


class SensorHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            payload = {
                "service": "sensor-node",
                "sensor_id": SENSOR_ID,
                "payload_type": PAYLOAD_TYPE,
                "destinations": DESTINATION_URLS,
                "metrics_url": METRICS_URL,
                **snapshot(),
            }
            send_json(self, 200, payload)
            return

        if path == "/metrics":
            with state_lock:
                lines = [
                    metric_line(
                        "sensor_messages_generated_total",
                        state["generated_total"],
                        {"sensor_id": SENSOR_ID},
                    ),
                    metric_line(
                        "sensor_packets_total",
                        state["sent_total"],
                        {"sensor_id": SENSOR_ID, "outcome": "sent"},
                    ),
                    metric_line(
                        "sensor_packets_total",
                        state["failed_total"],
                        {"sensor_id": SENSOR_ID, "outcome": "failed"},
                    ),
                    metric_line(
                        "sensor_sequence",
                        state["sequence"],
                        {"sensor_id": SENSOR_ID},
                    ),
                    metric_line(
                        "sensor_sent_bytes_total",
                        state["sent_bytes_total"],
                        {"sensor_id": SENSOR_ID},
                    ),
                    metric_line(
                        "sensor_sent_per_second",
                        rate_per_second(state["sent_total"], started_at),
                        {"sensor_id": SENSOR_ID},
                    ),
                    metric_line(
                        "sensor_sent_bytes_per_second",
                        rate_per_second(state["sent_bytes_total"], started_at),
                        {"sensor_id": SENSOR_ID},
                    ),
                ]
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})


def main():
    print(
        f"sensor-node starting sensor_id={SENSOR_ID} destinations={DESTINATION_URLS} port={PORT}",
        flush=True,
    )
    sender_thread = threading.Thread(target=telemetry_loop, daemon=True)
    sender_thread.start()
    reporter_thread = threading.Thread(target=metrics_report_loop, daemon=True)
    reporter_thread.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), SensorHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
