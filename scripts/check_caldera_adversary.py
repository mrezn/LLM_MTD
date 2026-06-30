#!/usr/bin/env python3
"""Check whether a Caldera adversary exists and has abilities."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def fetch_json(url: str, api_key: str, timeout: float) -> object:
    headers = {"KEY": api_key} if api_key else {}
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace") or "[]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether a Caldera adversary is imported and populated.")
    parser.add_argument("--caldera-url", default=os.environ.get("CALDERA_BASE_URL", "http://127.0.0.1:8888"))
    parser.add_argument("--api-key", default=os.environ.get("CALDERA_API_KEY", ""))
    parser.add_argument("--adversary-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    adversaries = fetch_json(
        f"{args.caldera_url.rstrip('/')}/api/v2/adversaries",
        args.api_key,
        args.timeout_seconds,
    )
    match = {}
    if isinstance(adversaries, list):
        for item in adversaries:
            if not isinstance(item, dict):
                continue
            if args.adversary_id in {
                str(item.get("adversary_id", "")),
                str(item.get("id", "")),
                str(item.get("name", "")),
            }:
                match = item
                break

    payload = {
        "exists": bool(match),
        "adversary_id": match.get("adversary_id") or match.get("id") or args.adversary_id,
        "name": match.get("name", ""),
        "has_abilities": bool(match.get("atomic_ordering")),
        "ability_count": len(match.get("atomic_ordering") or []),
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if payload["exists"] and payload["has_abilities"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
