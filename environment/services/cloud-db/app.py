import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    route_query,
    send_json,
    send_text,
)


DB_PATH = env("DB_PATH", "/data/telemetry.db")
METRICS_URL = env("METRICS_URL")
LOGGER_URL = env("LOGGER_URL")
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 2.0)
PORT = env_int("PORT", 8000)

db_lock = threading.Lock()
stats_lock = threading.Lock()
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
connection = sqlite3.connect(DB_PATH, check_same_thread=False)
service_started_at = time.monotonic()
stats = {
    "telemetry_records_total": 0,
    "summary_records_total": 0,
    "telemetry_bytes_total": 0,
    "summary_bytes_total": 0,
    "storage_confirmations_total": 0,
    "last_edge_to_cloud_latency_ms": 0.0,
    "total_edge_to_cloud_latency_ms": 0.0,
    "last_summary_received_at": "",
    "last_telemetry_received_at": "",
}


def init_db():
    with db_lock:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                sequence INTEGER,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT,
                edge_id TEXT,
                sensor_id TEXT NOT NULL,
                sequence INTEGER,
                timestamp TEXT NOT NULL,
                summary_json TEXT NOT NULL
            )
            """
        )
        connection.commit()


def insert_telemetry(payload, received_at):
    payload["cloud_db_received_at"] = received_at
    with db_lock:
        connection.execute(
            """
            INSERT INTO telemetry(sensor_id, sequence, timestamp, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                payload.get("sensor_id", "unknown"),
                payload.get("sequence"),
                payload.get("timestamp", now_iso()),
                json_dumps(payload),
            ),
        )
        connection.commit()


def insert_summary(payload, received_at):
    payload["cloud_db_received_at"] = received_at
    edge_to_cloud_latency_ms = elapsed_ms_between(
        payload.get("worker_summary_sent_at") or payload.get("timestamp"),
        received_at,
    )
    if edge_to_cloud_latency_ms is None:
        edge_to_cloud_latency_ms = 0.0
    payload["edge_to_cloud_latency_ms"] = edge_to_cloud_latency_ms

    with db_lock:
        connection.execute(
            """
            INSERT INTO summaries(worker_id, edge_id, sensor_id, sequence, timestamp, summary_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("worker_id", "unknown"),
                payload.get("edge_id", "unknown"),
                payload.get("sensor_id", "unknown"),
                payload.get("sequence"),
                payload.get("timestamp", now_iso()),
                json_dumps(payload),
            ),
        )
        connection.commit()
    return edge_to_cloud_latency_ms


def json_dumps(payload):
    import json

    return json.dumps(payload, sort_keys=True)


def count_table(table_name):
    with db_lock:
        cursor = connection.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cursor.fetchone()[0]


def snapshot():
    with stats_lock:
        uptime_start = service_started_at or 0.0
        return {
            **stats,
            "telemetry_records_per_second": rate_per_second(
                stats["telemetry_records_total"],
                uptime_start,
            ),
            "summary_records_per_second": rate_per_second(
                stats["summary_records_total"],
                uptime_start,
            ),
            "telemetry_bytes_per_second": rate_per_second(
                stats["telemetry_bytes_total"],
                uptime_start,
            ),
            "summary_bytes_per_second": rate_per_second(
                stats["summary_bytes_total"],
                uptime_start,
            ),
        }


def report_metrics():
    if not METRICS_URL:
        return
    post_json(
        METRICS_URL,
        {
            "source": "cloud_db",
            "role": "cloud_db",
            "timestamp": now_iso(),
            "metrics": snapshot(),
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    )


def log_event(event):
    if not LOGGER_URL:
        return
    post_json(LOGGER_URL, event, timeout=HTTP_TIMEOUT_SECONDS)


def latest_records(table_name, sensor_id, limit):
    query = f"SELECT payload_json FROM {table_name}"
    params = []
    if sensor_id:
        query += " WHERE sensor_id = ?"
        params.append(sensor_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    if table_name == "summaries":
        query = "SELECT summary_json FROM summaries"
        params = []
        if sensor_id:
            query += " WHERE sensor_id = ?"
            params.append(sensor_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

    with db_lock:
        cursor = connection.execute(query, params)
        return [row[0] for row in cursor.fetchall()]


class CloudDbHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            send_json(
                self,
                200,
                {
                    "service": "cloud-db",
                    "db_path": DB_PATH,
                    "telemetry_records": count_table("telemetry"),
                    "summary_records": count_table("summaries"),
                    "metrics_url": METRICS_URL,
                    "logger_url": LOGGER_URL,
                    "stats": snapshot(),
                },
            )
            return

        if path == "/records":
            query = route_query(self)
            table_name = query.get("table", ["telemetry"])[0]
            sensor_id = query.get("sensor_id", [""])[0]
            limit = int(query.get("limit", ["20"])[0])
            if table_name not in ("telemetry", "summaries"):
                send_json(self, 400, {"error": "table must be telemetry or summaries"})
                return
            send_json(
                self,
                200,
                {
                    "table": table_name,
                    "sensor_id": sensor_id,
                    "records": latest_records(table_name, sensor_id, limit),
                },
            )
            return

        if path == "/metrics":
            values = snapshot()
            lines = [
                metric_line("cloud_db_records_total", values["telemetry_records_total"], {"table": "telemetry"}),
                metric_line("cloud_db_records_total", values["summary_records_total"], {"table": "summaries"}),
                metric_line("cloud_db_received_bytes_total", values["telemetry_bytes_total"], {"table": "telemetry"}),
                metric_line("cloud_db_received_bytes_total", values["summary_bytes_total"], {"table": "summaries"}),
                metric_line("cloud_db_records_per_second", values["telemetry_records_per_second"], {"table": "telemetry"}),
                metric_line("cloud_db_records_per_second", values["summary_records_per_second"], {"table": "summaries"}),
                metric_line("cloud_db_last_edge_to_cloud_latency_ms", values["last_edge_to_cloud_latency_ms"], {}),
                metric_line("cloud_db_storage_confirmations_total", values["storage_confirmations_total"], {}),
            ]
            send_text(self, 200, "\n".join(lines) + "\n")
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)
        payload, request_bytes = read_json_with_size(self)
        received_at = now_iso()

        if path == "/telemetry":
            insert_telemetry(payload, received_at)
            with stats_lock:
                stats["telemetry_records_total"] += 1
                stats["telemetry_bytes_total"] += request_bytes
                stats["storage_confirmations_total"] += 1
                stats["last_telemetry_received_at"] = received_at
            report_metrics()
            send_json(
                self,
                201,
                {
                    "status": "stored",
                    "table": "telemetry",
                    "cloud_db_received_at": received_at,
                },
            )
            return

        if path == "/summary":
            edge_to_cloud_latency_ms = insert_summary(payload, received_at)
            with stats_lock:
                stats["summary_records_total"] += 1
                stats["summary_bytes_total"] += request_bytes
                stats["storage_confirmations_total"] += 1
                stats["last_edge_to_cloud_latency_ms"] = edge_to_cloud_latency_ms
                stats["total_edge_to_cloud_latency_ms"] += edge_to_cloud_latency_ms
                stats["last_summary_received_at"] = received_at
            report_metrics()
            log_event(
                {
                    "event_type": "cloud_db_summary_stored",
                    "source": "cloud_db",
                    "timestamp": received_at,
                    "sensor_id": payload.get("sensor_id"),
                    "worker_id": payload.get("worker_id"),
                    "sequence": payload.get("sequence"),
                    "edge_to_cloud_latency_ms": edge_to_cloud_latency_ms,
                }
            )
            send_json(
                self,
                201,
                {
                    "status": "stored",
                    "table": "summaries",
                    "cloud_db_received_at": received_at,
                    "edge_to_cloud_latency_ms": edge_to_cloud_latency_ms,
                },
            )
            return

        send_json(self, 404, {"error": "not found"})


def main():
    init_db()
    print(f"cloud-db starting db_path={DB_PATH} port={PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), CloudDbHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
