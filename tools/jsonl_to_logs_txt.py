#!/usr/bin/env python3
"""Convert canonical stage JSONL records into a concise human-readable log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def nested(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def format_stage(row: dict[str, Any]) -> str:
    validation = row.get("stage_validation") or {}
    execution = row.get("execution") or {}
    security = row.get("security_outcome") or {}
    qos = row.get("qos_delta") or {}
    return (
        f"stage={row.get('stage_id', '?')} "
        f"scenario={row.get('scenario_id', '')} "
        f"attacker={row.get('attacker_strategy_id') or nested(row, 'selection', 'attacker', 'id', default='')} "
        f"defender={row.get('defender_strategy_id') or nested(row, 'selection', 'defender', 'id', default='')} "
        f"path_stage={validation.get('path_stage_after', nested(row, 'state_summary', 'path_stage', default=''))} "
        f"defense_confirmed={execution.get('defense_confirmed', validation.get('defense_confirmed', False))} "
        f"defense_success={security.get('defense_success', False)} "
        f"attack_success={security.get('attack_effect_success', False)} "
        f"qos_sensor_delta={qos.get('sensor_to_edge_latency_ms', '')} "
        f"qos_cloud_delta={qos.get('edge_to_cloud_latency_ms', '')} "
        f"qos_throughput_delta={qos.get('throughput_bytes_per_second', '')}"
    )


def convert(source: Path, output: Path) -> int:
    lines = []
    for raw_line in source.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            lines.append(format_stage(row))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("outputs/raw/stage_summaries.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("outputs/logs.txt"))
    args = parser.parse_args()
    count = convert(args.input, args.output)
    print(f"wrote {count} stages to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
