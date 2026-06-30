#!/usr/bin/env python3
"""Idempotently load this project's abilities and adversaries into Caldera."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def request_json(
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json", "KEY": api_key},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body or "{}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Caldera {method} {path} returned HTTP {error.code}: {body}") from error


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def ability_payload(path: Path) -> dict[str, Any]:
    records = load_yaml(path)
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError(f"Expected exactly one ability in {path}")
    return {"index": "abilities", **records[0]}


def adversary_payload(path: Path) -> dict[str, Any]:
    record = load_yaml(path)
    if not isinstance(record, dict):
        raise ValueError(f"Expected one adversary object in {path}")
    phases = record.pop("phases", {})
    ordering: list[str] = []
    if isinstance(phases, dict):
        for phase in sorted(phases, key=lambda value: int(value)):
            ordering.extend(str(item) for item in (phases.get(phase) or []))
    adversary_id = str(record.pop("id", ""))
    if not adversary_id or not ordering:
        raise ValueError(f"Adversary {path} needs an id and at least one ability")
    return {
        "index": "adversaries",
        "id": adversary_id,
        "atomic_ordering": [{"id": ability_id} for ability_id in ordering],
        **record,
    }


def sync(base_url: str, api_key: str) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Set CALDERA_API_KEY or pass --api-key")

    imported_abilities: list[str] = []
    for path in sorted((PROJECT_ROOT / "attacker" / "actions" / "linux").glob("*.yml")):
        payload = ability_payload(path)
        request_json(base_url, api_key, "PUT", "/api/rest", payload)
        imported_abilities.append(str(payload["id"]))

    imported_adversaries: list[str] = []
    for path in sorted((PROJECT_ROOT / "attacker" / "adversaries").glob("*.yml")):
        payload = adversary_payload(path)
        request_json(base_url, api_key, "PUT", "/api/rest", payload)
        imported_adversaries.append(str(payload["id"]))

    verified: list[str] = []
    for adversary_id in imported_adversaries:
        status, record = request_json(
            base_url, api_key, "GET", f"/api/v2/adversaries/{adversary_id}"
        )
        if status != 200 or not isinstance(record, dict) or not record.get("atomic_ordering"):
            raise RuntimeError(f"Adversary verification failed: {adversary_id}")
        verified.append(adversary_id)

    return {
        "status": "synchronized",
        "caldera_url": base_url,
        "abilities": imported_abilities,
        "adversaries": imported_adversaries,
        "verified_adversaries": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--caldera-url",
        default=os.environ.get("CALDERA_BASE_URL", "http://127.0.0.1:8888"),
    )
    parser.add_argument("--api-key", default=os.environ.get("CALDERA_API_KEY", ""))
    args = parser.parse_args()
    print(json.dumps(sync(args.caldera_url, args.api_key), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
