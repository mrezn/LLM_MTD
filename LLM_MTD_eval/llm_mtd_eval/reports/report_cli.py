from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .build_figures import build_figures
from .build_tables import write_tables
from .load_results import concat_frames, load_result_frames


def add_build_report_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "build-report",
        help="Build paper-ready CSV summaries, figures, and reporting notes from evaluator and baseline outputs.",
    )
    parser.add_argument("--eval-stage-history", required=True)
    parser.add_argument("--eval-decision-trace", default=None)
    parser.add_argument("--eval-stage-summaries", default=None)
    parser.add_argument("--eval-population", default=None)
    parser.add_argument("--baseline-stage-history", default=None)
    parser.add_argument("--baseline-decision-trace", default=None)
    parser.add_argument("--baseline-population", default=None)
    parser.add_argument("--output-dir", default="outputs/reports")
    parser.add_argument("--scenario-filter", action="append", default=None)
    parser.add_argument("--format", choices=["png", "pdf"], default="png")
    parser.add_argument("--paper-mode", action="store_true")
    parser.add_argument("--include-debug-stages", action="store_true")


def build_report_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return build_report(
        eval_stage_history=Path(args.eval_stage_history),
        eval_decision_trace=_optional_path(args.eval_decision_trace),
        eval_stage_summaries=_optional_path(args.eval_stage_summaries),
        eval_population=_optional_path(args.eval_population),
        baseline_stage_history=_optional_path(args.baseline_stage_history),
        baseline_decision_trace=_optional_path(args.baseline_decision_trace),
        baseline_population=_optional_path(args.baseline_population),
        output_dir=Path(args.output_dir),
        scenario_filter=_parse_scenario_filter(args.scenario_filter),
        figure_format=args.format,
        paper_mode=args.paper_mode,
        include_debug_stages=args.include_debug_stages,
    )


def build_report(
    *,
    eval_stage_history: Path,
    eval_decision_trace: Path | None = None,
    eval_stage_summaries: Path | None = None,
    eval_population: Path | None = None,
    baseline_stage_history: Path | None = None,
    baseline_decision_trace: Path | None = None,
    baseline_population: Path | None = None,
    output_dir: Path,
    scenario_filter: Iterable[str] | None = None,
    figure_format: str = "png",
    paper_mode: bool = False,
    include_debug_stages: bool = True,
) -> dict[str, Any]:
    scenario_ids = list(scenario_filter or [])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary").mkdir(parents=True, exist_ok=True)

    eval_frames = load_result_frames(
        method="llm_defender",
        stage_history_path=eval_stage_history,
        decision_trace_path=eval_decision_trace,
        stage_summaries_path=eval_stage_summaries,
        population_path=eval_population,
        scenario_filter=scenario_ids,
    )
    baseline_frames = load_result_frames(
        method="baseline_game",
        stage_history_path=baseline_stage_history,
        decision_trace_path=baseline_decision_trace,
        population_path=baseline_population,
        scenario_filter=scenario_ids,
    )

    raw_combined_stage_df = concat_frames([baseline_frames.stage_df, eval_frames.stage_df])
    eval_stage_df_for_report = eval_frames.stage_df if include_debug_stages else _paper_valid_frame(eval_frames.stage_df)
    combined_stage_df = concat_frames([baseline_frames.stage_df, eval_stage_df_for_report])
    combined_population_evolution_df = concat_frames(
        [baseline_frames.population_evolution_df, eval_frames.population_evolution_df if include_debug_stages else _paper_valid_population(eval_frames.population_evolution_df, eval_stage_df_for_report)]
    )

    emo_root = _infer_emo_root(baseline_stage_history, eval_stage_history)
    table_paths = write_tables(
        output_dir=output_dir,
        emo_root=emo_root,
        eval_stage_df=eval_stage_df_for_report,
        combined_stage_df=combined_stage_df,
        eval_decision_df=eval_frames.decision_df,
        raw_stage_df=raw_combined_stage_df,
        scenario_filter=scenario_ids,
    )
    figure_paths = build_figures(
        stage_df=combined_stage_df,
        population_evolution_df=combined_population_evolution_df,
        output_dir=output_dir,
        figure_format=figure_format,
        paper_mode=paper_mode,
        scenario_filter=scenario_ids,
    )
    figure_csv_paths = {name: path.with_suffix(".csv") for name, path in figure_paths.items()}
    guide_path = _write_reporting_guide(output_dir, table_paths, figure_paths)

    manifest = {
        "output_dir": str(output_dir),
        "scenario_filter": scenario_ids,
        "tables": {name: str(path) for name, path in table_paths.items()},
        "figures": {name: str(path) for name, path in figure_paths.items()},
        "figure_data": {name: str(path) for name, path in figure_csv_paths.items()},
        "summary_files": {"reporting_guide": str(guide_path)},
        "row_counts": {
            "eval_stage_rows": int(len(eval_frames.stage_df)),
            "eval_report_stage_rows": int(len(eval_stage_df_for_report)),
            "baseline_stage_rows": int(len(baseline_frames.stage_df)),
            "combined_stage_rows": int(len(combined_stage_df)),
        },
        "include_debug_stages": True ,
    }
    manifest_path = output_dir / "summary" / "report_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest["summary_files"]["report_manifest"] = str(manifest_path)
    return manifest


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value else None


def _parse_scenario_filter(values: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        for item in str(value).split(","):
            stripped = item.strip()
            if stripped and stripped not in normalized:
                normalized.append(stripped)
    return normalized


def _paper_valid_frame(frame):
    if frame.empty or "paper_valid_stage" not in frame.columns:
        return frame.copy()
    mask = _bool_mask(frame["paper_valid_stage"], default=False)
    if "llm_fallback_used" in frame.columns:
        mask = mask & ~_bool_mask(frame["llm_fallback_used"], default=False)
    if "llm_request_success" in frame.columns:
        mask = mask & _bool_mask(frame["llm_request_success"], default=True)
    if "llm_parse_success" in frame.columns:
        mask = mask & _bool_mask(frame["llm_parse_success"], default=True)
    filtered = frame.loc[mask].copy()
    return filtered


def _bool_mask(series, *, default: bool):
    def coerce(value):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n", ""}:
            return False
        return default

    return series.map(coerce).astype(bool)


def _paper_valid_population(population_frame, paper_stage_frame):
    if population_frame.empty or paper_stage_frame.empty:
        return population_frame.iloc[0:0].copy()
    if "stage_id" not in population_frame.columns or "stage_id" not in paper_stage_frame.columns:
        return population_frame.copy()
    valid_stage_ids = set(paper_stage_frame["stage_id"].dropna().tolist())
    return population_frame.loc[population_frame["stage_id"].isin(valid_stage_ids)].copy()


def _infer_emo_root(baseline_stage_history: Path | None, eval_stage_history: Path) -> Path:
    if baseline_stage_history is not None:
        return baseline_stage_history.resolve().parents[2]
    sibling = eval_stage_history.resolve().parents[3] / "LLM_MTD_emo"
    if sibling.exists():
        return sibling
    raise RuntimeError("Unable to infer LLM_MTD_emo root. Pass --baseline-stage-history from the emulator repo.")


def _write_reporting_guide(
    output_dir: Path,
    table_paths: dict[str, Path],
    figure_paths: dict[str, Path],
) -> Path:
    guide_path = output_dir / "summary" / "reporting_guide.md"
    guide_text = f"""# Reporting Guide

This directory contains publication-oriented tables and figures generated from the live `LLM_MTD_eval` outputs and the baseline `LLM_MTD_emo` strategy logs.

## Tables

- `{table_paths['environment_setup'].name}`: summarizes the emulated deployment layers, representative nodes, and resource profiles so the paper can describe realism and compute constraints clearly.
- `{table_paths['scenario_attack_setup'].name}`: lists each scenario, the attacker entry point, the MulVAL path under study, the live attack family, and the defender action candidates evaluated for that path.
- `{table_paths['formal_to_observable_mapping'].name}`: maps the formal game-model terms used in the paper to the live metrics actually recorded in the evaluator and emulator logs.
- `{table_paths['baseline_vs_llm_summary'].name}`: provides the main baseline-versus-LLM comparison across attack success, defense success, defense confirmation, QoS tradeoffs, controller overhead, and LLM reliability.
- `{table_paths['stage_validity_summary'].name}`: counts total, paper-valid, fallback-only, timeout-failed, and defense-applied-but-ineffective stages so invalid live attempts are visible without entering the main paper comparison.
- `{table_paths['stage_case_study'].name}`: captures stage-by-stage records for a selected live evaluation trace, including selected strategies, observed state, and the narrative summary text.
- `{table_paths['llm_vs_baseline_decision_alignment'].name}`: records whether the LLM followed or overrode the baseline top-utility defender on each live stage and what outcome followed.
- `{table_paths['llm_candidate_ranking_case_study'].name}`: expands the LLM candidate ranking into one row per active defender candidate, preserving candidate-level tradeoff estimates.

## Figures

- `{figure_paths['attack_defense_outcomes_by_method'].name}`: grouped comparison of attack success, defense success, and defense confirmation rates.
- `{figure_paths['qos_tradeoff_by_method_sensor_to_edge_latency_delta_ms'].name}`: sensor-to-edge latency tradeoff by method.
- `{figure_paths['qos_tradeoff_by_method_edge_to_cloud_latency_delta_ms'].name}`: edge-to-cloud latency tradeoff by method.
- `{figure_paths['qos_tradeoff_by_method_throughput_delta_bps'].name}`: throughput tradeoff by method.
- `{figure_paths['controller_overhead_by_method'].name}`: controller overhead comparison using rules installed, meters added, and apply latency.
- `{figure_paths['defender_action_distribution'].name}`: stacked distribution of defender actions selected by each method.
- `{figure_paths['population_evolution'].name}`: attacker and defender population-share trajectories across stages for the selected scenario.
- `{figure_paths['llm_decision_quality'].name}`: LLM parse, fallback, recovery, and stage-success rates for quality auditing.
- `{figure_paths['llm_baseline_alignment'].name}`: compares how often the LLM followed versus overrode the baseline defender ranking and what defense success rate each case achieved.
- `{figure_paths['llm_candidate_tradeoff_scatter'].name}`: plots candidate-specific expected security gain against expected QoS impact, highlighting the chosen defense per stage.
- `{figure_paths['llm_latency_distribution'].name}`: distribution of LLM inference latency across recorded stages.
- `{figure_paths['llm_decision_timing_vs_path_stage'].name}`: relates defender choice timing to attack-path stage so the paper can discuss late versus early interventions.
- `{figure_paths['stage_trace_case_study'].name}`: stage-level attack or defense progression trace with defender strategy annotations.

Each figure also writes a sibling CSV with the same basename inside `outputs/reports/figures/`. For example, `llm_baseline_alignment.png` is accompanied by `llm_baseline_alignment.csv`, which contains the exact plotting data used to generate the chart.

## How To Use These In The Paper

### Experimental Setup

Use `environment_setup.csv` together with `scenario_attack_setup.csv` to describe the layered edge-cloud deployment and the concrete attack paths tested. These tables are the clearest place to separate analytical validation assumptions from live emulation behavior.

Suggested text:

> We evaluated the policy layer on a resource-constrained edge-cloud emulation that models sensor, edge gateway, edge worker, and cloud service roles under explicit CPU and memory limits. Each scenario specifies an attacker entry node, a MulVAL-derived path toward the protected cloud asset, and a constrained defender action set that can be executed through the Ryu-based control plane.

### Attack Generation

Use `scenario_attack_setup.csv` and `stage_case_study.csv` to explain how attacks are derived from formal paths but realized with concrete Caldera-backed live attack types.

Suggested text:

> Attack generation remained grounded in the same scenario registry and MulVAL path structure used by the baseline strategy layer. The live evaluator dispatches the selected attacker plan into the emulation and then measures observable progression signals such as gateway visibility, worker activity, and cloud reachability rather than relying on abstract win labels alone.

### LLM Role And Inference-Time Usage

Use `baseline_vs_llm_summary.csv`, `llm_decision_quality`, and `llm_latency_distribution` to explain what the LLM actually contributes and what its inference cost looks like.

Suggested text:

> The LLM was used as a defender-policy reasoner over the currently active candidate set. It received the live attack-path stage, recent stage-memory signals, candidate-specific tradeoff features, and the baseline game-model priors, then produced a ranked candidate list and a final defender selection. We therefore report not only defense outcomes but also when the LLM followed or overrode the baseline ranking, what tradeoff it cited, and what latency or fallback cost was incurred online.

### Limitations

Use `formal_to_observable_mapping.csv`, the QoS figures, and the controller-overhead figure to discuss where live observables remain proxies for the formal game quantities.

Suggested text:

> Several formal terms in the game model, such as attack value and defense cost, are operationalized through observable proxies rather than directly measured latent variables. We therefore report the mapping between formal terms and recorded metrics, and we separate controller confirmation, observable containment, and end-to-end defense success to avoid overstating mitigation effectiveness.
"""
    guide_path.write_text(guide_text, encoding="utf-8")
    return guide_path
