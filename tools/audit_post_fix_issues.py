#!/usr/bin/env python3
"""Audit reported post-fix issues against current code and generated artifacts."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "audit" / "post_fix_issue_audit.json"
VALID_STATUSES = {
    "confirmed",
    "partially_confirmed",
    "not_reproduced",
    "obsolete_already_fixed",
    "cannot_verify_missing_artifact",
}


def read_jsonl(relative: str) -> list[dict[str, Any]]:
    path = ROOT / relative
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_csv(relative: str) -> list[dict[str, str]]:
    path = ROOT / relative
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def source(relative: str) -> str:
    path = ROOT / relative
    return path.read_text(encoding="utf-8") if path.exists() else ""


def truth(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def issue(
    issue_id: str,
    title: str,
    status: str,
    evidence: list[str],
    affected_files: list[str],
    recommended_fix: str,
    safe_to_fix: bool = True,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid audit status: {status}")
    return {
        "issue_id": issue_id,
        "title": title,
        "status": status,
        "evidence": evidence,
        "affected_files": affected_files,
        "recommended_fix": recommended_fix,
        "safe_to_fix": safe_to_fix,
    }


def audit() -> list[dict[str, Any]]:
    summaries = read_jsonl("outputs/raw/stage_summaries.jsonl")
    baseline = read_csv("outputs/figures/tables/stage_case_study.csv")
    comparison = read_csv("outputs/figures/tables/baseline_vs_llm_summary.csv")
    trials = read_csv("outputs/summaries/trial_summaries.csv")
    live_population = read_json("outputs/raw/live_population_state.json")
    game_population = read_json("game/population_state.json")

    runtime_source = source("game/strategy_runtime.py")
    eval_source = source("eval/runners/run_stage.py")
    state_source = source("game/state_builder.py")
    game_source = source("game/game_model.py")
    llm_source = source("defender/decision/llm_client.py")

    stage3 = [row for row in baseline if number(row.get("path_stage")) == 3.0]
    cloud_fix_present = all(
        token in runtime_source + eval_source + state_source
        for token in ("cloud_storage_baseline", "cloud_storage_delta", "caldera_abilities_ran")
    )
    a_status = "partially_confirmed" if stage3 and cloud_fix_present else (
        "confirmed" if stage3 else "obsolete_already_fixed"
    )

    executed_baseline = [row for row in baseline if truth(row.get("defense_executed"))]
    unconfirmed_baseline = [row for row in executed_baseline if not truth(row.get("defense_confirmed"))]
    b_status = "confirmed" if unconfirmed_baseline else (
        "not_reproduced" if baseline else "cannot_verify_missing_artifact"
    )

    methods = {row.get("method", "") for row in comparison}
    two_sources = len(methods) >= 2 and any("baseline" in item.lower() for item in methods) and any(
        "llm" in item.lower() for item in methods
    )
    c_status = "not_reproduced" if two_sources else (
        "confirmed" if comparison else "cannot_verify_missing_artifact"
    )

    figure_names = (
        "llm_baseline_alignment.csv",
        "llm_latency_distribution.csv",
        "llm_decision_timing_vs_path_stage.csv",
        "llm_candidate_tradeoff_scatter.csv",
    )
    placeholder_figures = []
    for name in figure_names:
        path = ROOT / "outputs" / "figures" / "figures" / name
        if path.exists() and "No " in path.read_text(encoding="utf-8"):
            placeholder_figures.append(name)
    llm_fields_exist = any((row.get("llm") or {}).get("baseline_alignment") for row in summaries)
    d_status = "confirmed" if placeholder_figures and llm_fields_exist else (
        "not_reproduced" if not placeholder_figures else "cannot_verify_missing_artifact"
    )

    kinds = Counter((row.get("stage_validation") or {}).get("stage_kind", "unknown") for row in summaries)
    paper_valid = sum(truth((row.get("stage_validation") or {}).get("paper_valid_stage")) for row in summaries)
    paper_rate = paper_valid / len(summaries) if summaries else 0.0
    validity_controls_present = all(
        token in eval_source for token in ("low_paper_valid_rate_warning", "effective_execute_attacker", "consecutive_warmup_count")
    )
    e_status = "partially_confirmed" if summaries and paper_rate < 0.5 and validity_controls_present else ("confirmed" if summaries and paper_rate < 0.5 else (
        "not_reproduced" if summaries else "cannot_verify_missing_artifact"
    ))

    inconsistent = [
        row for row in summaries
        if "inconsistent_outcome" in ((row.get("stage_validation") or {}).get("invalidity_reasons") or [])
    ]
    f_status = "partially_confirmed" if inconsistent and "containment_evidence" in eval_source else ("confirmed" if inconsistent else (
        "not_reproduced" if summaries else "cannot_verify_missing_artifact"
    ))

    attacker_ids = set((live_population.get("attacker") or {})) | set((game_population.get("attacker") or {}))
    defender_ids = set((live_population.get("defender") or {})) | set((game_population.get("defender") or {}))
    expected_missing = not {"A1_sensor_probe_sen4", "A2_sensor_http_abuse_sen4", "A6_sen4_edge_to_cloud_probe"}.issubset(attacker_ids)
    global_population_absent = "global_population" not in live_population and "global_population" not in game_population
    population_fix_present = "global_population" in runtime_source and "population_floor" in game_source
    g_status = "partially_confirmed" if expected_missing and global_population_absent and population_fix_present else (
        "confirmed" if expected_missing and global_population_absent else "not_reproduced"
    )

    paper_rows = [row for row in summaries if truth((row.get("stage_validation") or {}).get("paper_valid_stage"))]
    qos_keys = ("sensor_to_edge_latency_ms", "edge_to_cloud_latency_ms", "throughput_bytes_per_second")
    qos_missing_or_zero = [
        row for row in paper_rows
        if all(number((row.get("qos_delta") or {}).get(key)) in (None, 0.0) for key in qos_keys)
    ]
    qos_fix_present = "_normalize_qos_delta_payload" in source("defender/decision/stage_summarizer.py") and "qos_snapshot_after_collected" in eval_source
    h_status = "partially_confirmed" if paper_rows and len(qos_missing_or_zero) == len(paper_rows) and qos_fix_present else ("confirmed" if paper_rows and len(qos_missing_or_zero) == len(paper_rows) else (
        "partially_confirmed" if qos_missing_or_zero else "not_reproduced"
    ))

    longest_warmup = 0
    current_warmup = 0
    for row in summaries:
        if (row.get("stage_validation") or {}).get("stage_kind") == "warmup":
            current_warmup += 1
            longest_warmup = max(longest_warmup, current_warmup)
        else:
            current_warmup = 0
    i_status = "partially_confirmed" if longest_warmup >= 3 and "consecutive_warmup_count" in eval_source else ("confirmed" if longest_warmup >= 3 else (
        "not_reproduced" if summaries else "cannot_verify_missing_artifact"
    ))

    incorrect_narratives = [
        row for row in baseline
        if "attack pressure reduced" in row.get("summary_text", "").lower()
        and not truth(row.get("defense_confirmed"))
    ]
    j_status = "confirmed" if incorrect_narratives else (
        "not_reproduced" if baseline else "cannot_verify_missing_artifact"
    )

    latencies = [number(row.get("decision_latency_ms")) for row in trials]
    latencies = [value for value in latencies if value is not None]
    summary_latency_missing = bool(summaries) and not any(
        number((row.get("llm") or {}).get("latency_ms")) is not None for row in summaries
    )
    keep_alive_missing = "keep_alive" not in llm_source
    latency_fix_present = "keep_alive" in llm_source and '"latency_ms": llm_metadata' in eval_source
    k_status = "partially_confirmed" if latencies and max(latencies) >= 30_000 and latency_fix_present else ("confirmed" if latencies and max(latencies) >= 30_000 and summary_latency_missing else (
        "partially_confirmed" if latencies and max(latencies) >= 30_000 else "not_reproduced"
    ))

    logs_path = ROOT / "outputs" / "logs.txt"
    logs_references = []
    for relative in ("DEPLOYMENT.md", "README.md"):
        text = source(relative)
        if "logs.txt" in text:
            logs_references.append(relative)
    l_status = "not_reproduced" if not logs_references else (
        "confirmed" if not logs_path.exists() else "not_reproduced"
    )

    return [
        issue("A", "path_stage jumps to 3 too early", a_status,
              [f"baseline rows at path_stage=3: {len(stage3)}", f"baseline-delta source fix present: {cloud_fix_present}"],
              ["outputs/figures/tables/stage_case_study.csv", "game/state_builder.py", "game/strategy_runtime.py", "eval/runners/run_stage.py"],
              "Regenerate outputs with the current baseline-delta cloud evidence logic."),
        issue("B", "baseline defense confirmation remains false", b_status,
              [f"executed baseline rows: {len(executed_baseline)}", f"executed but unconfirmed: {len(unconfirmed_baseline)}"],
              ["outputs/figures/tables/stage_case_study.csv", "game/stage_transition.py", "eval/reports/load_results.py"],
              "Persist baseline Ryu confirmation and semantic effects in transition records."),
        issue("C", "baseline versus LLM summary has one source", c_status,
              [f"summary methods: {sorted(methods)}", f"summary rows: {len(comparison)}"],
              ["outputs/figures/tables/baseline_vs_llm_summary.csv", "eval/reports/report_cli.py", "eval/reports/build_tables.py"],
              "Build separate baseline and LLM rows from their respective frames."),
        issue("D", "LLM figures contain no-data placeholders", d_status,
              [f"placeholder figures: {placeholder_figures}", f"stage summaries contain LLM alignment: {llm_fields_exist}"],
              ["outputs/figures/figures", "eval/reports/load_results.py", "eval/reports/build_figures.py"],
              "Map nested stage-summary LLM fields and candidate rankings into the report frame."),
        issue("E", "low paper-valid LLM stage rate", e_status,
              [f"paper-valid stages: {paper_valid}/{len(summaries)} ({paper_rate:.1%})", f"stage kinds: {dict(kinds)}"],
              ["outputs/raw/stage_summaries.jsonl", "eval/runners/run_stage.py", "eval/configs/models/hybrid_game_llm.yaml"],
              "Warn on low validity and stop unhealthy warmup loops; retain explicit dry-run semantics."),
        issue("F", "contained stages marked inconsistent", f_status,
              [f"inconsistent_outcome stages: {len(inconsistent)}"],
              ["outputs/raw/stage_summaries.jsonl", "eval/runners/run_stage.py"],
              "Treat confirmed containment with path regression/deactivation as consistent."),
        issue("G", "population extinction or active/global confusion", g_status,
              [f"stored attacker IDs: {sorted(attacker_ids)}", f"stored defender IDs: {sorted(defender_ids)}", f"global population absent: {global_population_absent}", f"epsilon floor marker present: {'population_floor' in game_source}"],
              ["outputs/raw/live_population_state.json", "game/population_state.json", "game/game_model.py"],
              "Apply an explicit floor and persist global and active populations separately."),
        issue("H", "paper-valid QoS deltas are absent or zero", h_status,
              [f"paper-valid rows: {len(paper_rows)}", f"paper-valid rows with all QoS deltas absent/zero: {len(qos_missing_or_zero)}"],
              ["outputs/raw/stage_summaries.jsonl", "defender/decision/stage_summarizer.py", "eval/runners/run_stage.py"],
              "Merge measured QoS deltas into summaries and record snapshot collection flags."),
        issue("I", "fallback followed by warmup loop", i_status,
              [f"longest consecutive warmup sequence: {longest_warmup}"],
              ["outputs/raw/stage_summaries.jsonl", "eval/runners/run_stage.py", "attacker/engine/caldera_dispatch_bridge.py"],
              "Track consecutive warmups and emit health/re-dispatch diagnostics after three."),
        issue("J", "stage narrative contradicts evidence", j_status,
              [f"unconfirmed rows saying attack pressure reduced: {len(incorrect_narratives)}"],
              ["outputs/figures/tables/stage_case_study.csv", "eval/reports/build_tables.py", "defender/decision/stage_summarizer.py"],
              "Generate outcome text deterministically from confirmation and progression fields."),
        issue("K", "LLM latency is high and under-reported", k_status,
              [f"latency samples: {len(latencies)}", f"maximum latency ms: {max(latencies) if latencies else None}", f"stage-summary latency missing: {summary_latency_missing}", f"Ollama keep_alive missing: {keep_alive_missing}"],
              ["outputs/summaries/trial_summaries.csv", "outputs/raw/stage_summaries.jsonl", "defender/decision/llm_client.py"],
              "Add keep_alive and propagate timeout/model/latency/warning fields into summaries."),
        issue("L", "human-readable logs.txt is missing", l_status,
              [f"outputs/logs.txt exists: {logs_path.exists()}", f"documented consumers: {logs_references}"],
              ["outputs/logs.txt"],
              "Keep JSONL canonical; provide an optional converter for human-readable output.", safe_to_fix=True),
    ]


def print_table(rows: list[dict[str, Any]]) -> None:
    widths = {
        "id": 3,
        "status": max(len("status"), *(len(row["status"]) for row in rows)),
        "title": max(len("title"), *(len(row["title"]) for row in rows)),
    }
    print(f"{'ID':<{widths['id']}}  {'status':<{widths['status']}}  title")
    print(f"{'-' * widths['id']}  {'-' * widths['status']}  {'-' * widths['title']}")
    for row in rows:
        print(f"{row['issue_id']:<{widths['id']}}  {row['status']:<{widths['status']}}  {row['title']}")


def main() -> int:
    rows = audit()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "llm-mtd-post-fix-audit-v1",
        "project_root": str(ROOT),
        "issues": rows,
        "status_counts": dict(Counter(row["status"] for row in rows)),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print_table(rows)
    print(f"\nWrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
