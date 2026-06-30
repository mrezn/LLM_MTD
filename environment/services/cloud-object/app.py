import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mtd_common import env, env_int, read_json, read_request_body, route_path, send_json


OBJECT_ROOT = os.path.abspath(env("OBJECT_ROOT", "/data/objects"))
PORT = env_int("PORT", 8000)

state_lock = threading.Lock()
state = {"objects_stored": 0, "objects_read": 0}
os.makedirs(OBJECT_ROOT, exist_ok=True)


def object_path(name):
    cleaned_name = name.replace("\\", "/").lstrip("/")
    if not cleaned_name or ".." in cleaned_name.split("/"):
        raise ValueError("invalid object name")

    resolved_path = os.path.abspath(os.path.join(OBJECT_ROOT, cleaned_name))
    if not resolved_path.startswith(OBJECT_ROOT + os.sep):
        raise ValueError("invalid object path")
    return resolved_path


def list_objects():
    result = []
    for root, _, files in os.walk(OBJECT_ROOT):
        for file_name in files:
            full_path = os.path.join(root, file_name)
            result.append(os.path.relpath(full_path, OBJECT_ROOT).replace("\\", "/"))
    return sorted(result)


class ObjectHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = route_path(self)

        if path == "/health":
            with state_lock:
                payload = {
                    "service": "cloud-object",
                    "object_root": OBJECT_ROOT,
                    "object_count": len(list_objects()),
                    **state,
                }
            send_json(self, 200, payload)
            return

        if path == "/objects":
            send_json(self, 200, {"objects": list_objects()})
            return

        if path.startswith("/objects/"):
            name = path.removeprefix("/objects/")
            try:
                full_path = object_path(name)
            except ValueError as error:
                send_json(self, 400, {"error": str(error)})
                return

            if not os.path.exists(full_path):
                send_json(self, 404, {"error": "object not found", "name": name})
                return

            with open(full_path, "rb") as object_file:
                data = object_file.read()
            with state_lock:
                state["objects_read"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = route_path(self)

        if path == "/object":
            payload = read_json(self)
            name = payload.get("name", "")
            content = payload.get("content", {})
            data = json.dumps(content, sort_keys=True).encode("utf-8")
        elif path.startswith("/objects/"):
            name = path.removeprefix("/objects/")
            data = read_request_body(self)
        else:
            send_json(self, 404, {"error": "not found"})
            return

        try:
            full_path = object_path(name)
        except ValueError as error:
            send_json(self, 400, {"error": str(error)})
            return

        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as object_file:
            object_file.write(data)

        with state_lock:
            state["objects_stored"] += 1
        send_json(self, 201, {"status": "stored", "name": name, "bytes": len(data)})


def main():
    print(f"cloud-object starting object_root={OBJECT_ROOT} port={PORT}", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ObjectHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
