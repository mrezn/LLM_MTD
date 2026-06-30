from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .metric_mapping import DEFENDER_ACTION_ORDER, pretty_method_label


def build_figures(
    *,
    stage_df: pd.DataFrame,
    population_evolution_df: pd.DataFrame,
    output_dir: Path,
    figure_format: str = "png",
    paper_mode: bool = False,
    scenario_filter: Iterable[str] | None = None,
) -> dict[str, Path]:
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    _apply_plot_style(paper_mode)

    selected_scenario = _select_scenario(stage_df, scenario_filter)
    written: dict[str, Path] = {}
    written["attack_defense_outcomes_by_method"] = _plot_attack_defense_outcomes(
        stage_df, figures_dir / f"attack_defense_outcomes_by_method.{figure_format}"
    )
    for metric_name in (
        "sensor_to_edge_latency_delta_ms",
        "edge_to_cloud_latency_delta_ms",
        "throughput_delta_bps",
    ):
        written[f"qos_tradeoff_by_method_{metric_name}"] = _plot_qos_tradeoff(
            stage_df,
            metric_name=metric_name,
            path=figures_dir / f"qos_tradeoff_by_method_{metric_name}.{figure_format}",
        )
    written["controller_overhead_by_method"] = _plot_controller_overhead(
        stage_df, figures_dir / f"controller_overhead_by_method.{figure_format}"
    )
    written["defender_action_distribution"] = _plot_defender_action_distribution(
        stage_df, figures_dir / f"defender_action_distribution.{figure_format}"
    )
    written["population_evolution"] = _plot_population_evolution(
        population_evolution_df,
        path=figures_dir / f"population_evolution.{figure_format}",
        scenario_id=selected_scenario,
    )
    written["llm_decision_quality"] = _plot_llm_decision_quality(
        stage_df, figures_dir / f"llm_decision_quality.{figure_format}"
    )
    written["llm_baseline_alignment"] = _plot_llm_baseline_alignment(
        stage_df, figures_dir / f"llm_baseline_alignment.{figure_format}"
    )
    written["defense_effect_confirmation_breakdown"] = _plot_defense_effect_confirmation_breakdown(
        stage_df, figures_dir / f"defense_effect_confirmation_breakdown.{figure_format}"
    )
    written["llm_candidate_tradeoff_scatter"] = _plot_llm_candidate_tradeoff_scatter(
        stage_df, figures_dir / f"llm_candidate_tradeoff_scatter.{figure_format}"
    )
    written["llm_latency_distribution"] = _plot_llm_latency_distribution(
        stage_df, figures_dir / f"llm_latency_distribution.{figure_format}"
    )
    written["llm_decision_timing_vs_path_stage"] = _plot_llm_decision_timing_vs_path_stage(
        stage_df, figures_dir / f"llm_decision_timing_vs_path_stage.{figure_format}"
    )
    written["stage_trace_case_study"] = _plot_stage_trace_case_study(
        stage_df,
        path=figures_dir / f"stage_trace_case_study.{figure_format}",
        scenario_id=selected_scenario,
    )
    return written


def _apply_plot_style(paper_mode: bool) -> None:
    if paper_mode:
        plt.rcParams.update(
            {
                "font.size": 12,
                "axes.titlesize": 14,
                "axes.labelsize": 12,
                "xtick.labelsize": 11,
                "ytick.labelsize": 11,
                "legend.fontsize": 10,
                "figure.titlesize": 14,
            }
        )
    else:
        plt.rcParams.update(
            {
                "font.size": 10,
                "axes.titlesize": 12,
                "axes.labelsize": 10,
                "xtick.labelsize": 9,
                "ytick.labelsize": 9,
                "legend.fontsize": 9,
            }
        )


def _plot_attack_defense_outcomes(stage_df: pd.DataFrame, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty:
        return _placeholder_figure(path, "No comparable stage data available for attack/defense outcome rates.")
    summary = (
        comparable.groupby("method", dropna=False)[
            ["attack_effect_success", "defense_success", "defense_confirmed"]
        ]
        .mean(numeric_only=True)
        .reset_index()
    )
    _write_figure_csv(path, summary.assign(method_label=summary["method"].map(lambda value: pretty_method_label(str(value)))))
    x = np.arange(len(summary))
    metrics = [
        ("attack_effect_success", "Attack success rate"),
        ("defense_success", "Defense success rate"),
        ("defense_confirmed", "Defense confirmed rate"),
    ]
    width = 0.24
    fig, ax = plt.subplots(figsize=(8, 5))
    for index, (column, label) in enumerate(metrics):
        values = summary[column].to_numpy(dtype=float)
        ax.bar(x + (index - 1) * width, values, width=width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels([pretty_method_label(str(item)) for item in summary["method"]])
    ax.set_ylim(0.0, 1.05)
    ax.set_ylabel("Rate")
    ax.set_title("Attack and defense outcomes by method")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_qos_tradeoff(stage_df: pd.DataFrame, *, metric_name: str, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty or metric_name not in comparable.columns:
        return _placeholder_figure(path, f"No QoS delta data available for {metric_name}.")
    export_frame = comparable[["method", "scenario_id", "stage_id", metric_name]].copy()
    export_frame["method_label"] = export_frame["method"].map(lambda value: pretty_method_label(str(value)))
    export_frame["metric_name"] = metric_name
    export_frame["metric_label"] = _pretty_metric_title(metric_name)
    export_frame = export_frame.rename(columns={metric_name: "metric_value"})
    _write_figure_csv(path, export_frame)
    labels: list[str] = []
    series_list: list[np.ndarray] = []
    for method, group in comparable.groupby("method", dropna=False):
        series = pd.to_numeric(group[metric_name], errors="coerce").dropna()
        if series.empty:
            continue
        labels.append(pretty_method_label(str(method)))
        series_list.append(series.to_numpy(dtype=float))
    if not series_list:
        return _placeholder_figure(path, f"No QoS delta samples available for {metric_name}.")
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.boxplot(series_list, tick_labels=labels, patch_artist=False)
    ax.set_title(_pretty_metric_title(metric_name) + " by method")
    ax.set_ylabel(_pretty_metric_ylabel(metric_name))
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_controller_overhead(stage_df: pd.DataFrame, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty:
        return _placeholder_figure(path, "No comparable stage data available for controller overhead.")
    summary = (
        comparable.groupby("method", dropna=False)[
            ["flow_rules_installed", "meters_added", "controller_apply_ms"]
        ]
        .mean(numeric_only=True)
        .reset_index()
    )
    _write_figure_csv(path, summary.assign(method_label=summary["method"].map(lambda value: pretty_method_label(str(value)))))
    x = np.arange(len(summary))
    metrics = [
        ("flow_rules_installed", "Flow rules installed"),
        ("meters_added", "Meters added"),
        ("controller_apply_ms", "Controller apply ms"),
    ]
    width = 0.24
    fig, ax = plt.subplots(figsize=(8, 5))
    for index, (column, label) in enumerate(metrics):
        values = summary[column].to_numpy(dtype=float)
        ax.bar(x + (index - 1) * width, values, width=width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels([pretty_method_label(str(item)) for item in summary["method"]])
    ax.set_ylabel("Mean per comparable stage")
    ax.set_title("Controller overhead by method")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_defender_action_distribution(stage_df: pd.DataFrame, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty or "defender_action" not in comparable.columns:
        return _placeholder_figure(path, "No defender action data available for action distribution.")
    pivot = (
        comparable.assign(defender_action=comparable["defender_action"].fillna("observe"))
        .groupby(["method", "defender_action"])
        .size()
        .unstack(fill_value=0)
    )
    if pivot.empty:
        return _placeholder_figure(path, "No defender action samples available.")
    pivot = pivot.reindex(columns=DEFENDER_ACTION_ORDER, fill_value=0)
    shares = pivot.div(pivot.sum(axis=1), axis=0).fillna(0.0)
    export_rows: list[dict[str, object]] = []
    for method, counts in pivot.iterrows():
        total = float(counts.sum()) or 1.0
        for action in DEFENDER_ACTION_ORDER:
            export_rows.append(
                {
                    "method": method,
                    "method_label": pretty_method_label(str(method)),
                    "defender_action": action,
                    "count": int(counts[action]),
                    "share": float(counts[action] / total),
                }
            )
    _write_figure_csv(path, pd.DataFrame(export_rows))
    x = np.arange(len(shares))
    fig, ax = plt.subplots(figsize=(9, 5))
    bottom = np.zeros(len(shares))
    for action in DEFENDER_ACTION_ORDER:
        values = shares[action].to_numpy(dtype=float)
        ax.bar(x, values, bottom=bottom, label=action)
        bottom += values
    ax.set_xticks(x)
    ax.set_xticklabels([pretty_method_label(str(item)) for item in shares.index])
    ax.set_ylim(0.0, 1.0)
    ax.set_ylabel("Share of comparable stages")
    ax.set_title("Defender action distribution by method")
    ax.legend(loc="upper right")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_population_evolution(population_df: pd.DataFrame, *, path: Path, scenario_id: str | None) -> Path:
    if population_df.empty:
        return _placeholder_figure(path, "No population history data available.")
    filtered = population_df
    if scenario_id:
        filtered = population_df.loc[population_df["scenario_id"] == scenario_id].copy()
    if filtered.empty:
        return _placeholder_figure(path, "No population history data available for the selected scenario.")
    _write_figure_csv(path, filtered)

    fig, ax = plt.subplots(figsize=(10, 5))
    filtered = filtered.sort_values(["method", "role", "strategy_id", "stage_id"])
    for (method, role, strategy_id), group in filtered.groupby(["method", "role", "strategy_id"], dropna=False):
        style = "--" if role == "attacker" else "-"
        ax.plot(
            group["stage_id"],
            group["population_share"],
            linestyle=style,
            marker="o",
            label=f"{pretty_method_label(str(method))} | {role} | {strategy_id}",
        )
    ax.set_xlabel("Stage ID")
    ax.set_ylabel("Population share")
    ax.set_ylim(0.0, 1.0)
    title = "Population evolution"
    if scenario_id:
        title += f" for {scenario_id}"
    ax.set_title(title)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5))
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_llm_decision_quality(stage_df: pd.DataFrame, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty:
        return _placeholder_figure(path, "No comparable stage data available for LLM decision quality.")
    grouped = comparable.groupby("method", dropna=False)
    methods = list(grouped.groups.keys())
    if not methods:
        return _placeholder_figure(path, "No method-level samples available for LLM decision quality.")
    metrics = [
        ("llm_parse_success", "Parse success rate"),
        ("llm_fallback_used", "Fallback rate"),
        ("llm_recovery_used", "Recovery rate"),
        ("stage_success", "Stage success rate"),
    ]
    summary_rows: list[dict[str, float | str]] = []
    for method, group in grouped:
        row: dict[str, float | str] = {"method": method}
        for column, _label in metrics:
            series = pd.to_numeric(group[column], errors="coerce").dropna() if column in group.columns else pd.Series(dtype=float)
            row[column] = float(series.mean()) if not series.empty else np.nan
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    _write_figure_csv(path, summary.assign(method_label=summary["method"].map(lambda value: pretty_method_label(str(value)))))
    x = np.arange(len(summary))
    width = 0.18
    fig, ax = plt.subplots(figsize=(9, 5))
    for index, (column, label) in enumerate(metrics):
        values = summary[column].to_numpy(dtype=float)
        ax.bar(x + (index - 1.5) * width, values, width=width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels([pretty_method_label(str(item)) for item in summary["method"]])
    ax.set_ylim(0.0, 1.05)
    ax.set_ylabel("Rate")
    ax.set_title("LLM decision quality and stage completion")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_llm_baseline_alignment(stage_df: pd.DataFrame, path: Path) -> Path:
    if stage_df.empty:
        return _placeholder_figure(path, "No LLM alignment data available.")
    llm_rows = stage_df.loc[stage_df["decision_source"].fillna("").eq("llm_defender")].copy()
    if llm_rows.empty:
        return _placeholder_figure(path, "No LLM alignment data available.")
    selected_column = (
        "raw_llm_selected_defender_strategy_id"
        if "raw_llm_selected_defender_strategy_id" in llm_rows.columns
        else "llm_selected_defender_strategy_id"
    )
    if "raw_llm_alignment" in llm_rows.columns:
        llm_rows["alignment"] = llm_rows["raw_llm_alignment"].fillna("unknown")
    elif {"baseline_top_defender_strategy_id", selected_column}.issubset(llm_rows.columns):
        llm_rows["alignment"] = np.where(
            llm_rows["baseline_top_defender_strategy_id"].fillna("").eq(
                llm_rows[selected_column].fillna("")
            ),
            "followed",
            "overrode",
        )
    elif "llm_baseline_alignment" in llm_rows.columns:
        llm_rows["alignment"] = llm_rows["llm_baseline_alignment"].fillna("unknown")
    else:
        llm_rows["alignment"] = "unknown"
    alignment_frame = (
        llm_rows.assign(
            defense_success_numeric=llm_rows["defense_success"].fillna(False).astype(float),
        )
        .groupby("alignment", dropna=False)
        .agg(stage_count=("alignment", "size"), defense_success_rate=("defense_success_numeric", "mean"))
        .reset_index()
    )
    total = float(alignment_frame["stage_count"].sum()) or 1.0
    alignment_frame["stage_share"] = alignment_frame["stage_count"] / total
    _write_figure_csv(path, alignment_frame)
    x = np.arange(len(alignment_frame))
    width = 0.35
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(x - width / 2, alignment_frame["stage_share"], width=width, label="Stage share")
    ax.bar(x + width / 2, alignment_frame["defense_success_rate"], width=width, label="Defense success rate")
    ax.set_xticks(x)
    ax.set_xticklabels(alignment_frame["alignment"].tolist())
    ax.set_ylim(0.0, 1.05)
    ax.set_ylabel("Rate")
    ax.set_title("LLM alignment with baseline defender ranking")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_defense_effect_confirmation_breakdown(stage_df: pd.DataFrame, path: Path) -> Path:
    comparable = _comparable_stage_frame(stage_df)
    if comparable.empty:
        return _placeholder_figure(path, "No comparable stage data available for defense effect confirmation.")
    metrics = [
        ("defense_executed", "Defense executed"),
        ("defense_confirmed", "Defense confirmed"),
        ("defense_effects_confirmed", "Expected effects confirmed"),
        ("defense_success", "Security success"),
    ]
    rows: list[dict[str, object]] = []
    for method, group in comparable.groupby("method", dropna=False):
        row: dict[str, object] = {"method": method}
        for column, _label in metrics:
            if column in group.columns:
                series = pd.to_numeric(group[column], errors="coerce").dropna()
                row[column] = float(series.mean()) if not series.empty else np.nan
            else:
                row[column] = np.nan
        rows.append(row)
    summary = pd.DataFrame(rows)
    if summary.empty:
        return _placeholder_figure(path, "No defense effect confirmation samples available.")
    _write_figure_csv(path, summary.assign(method_label=summary["method"].map(lambda value: pretty_method_label(str(value)))))
    x = np.arange(len(summary))
    width = 0.2
    fig, ax = plt.subplots(figsize=(9, 5))
    for index, (column, label) in enumerate(metrics):
        ax.bar(x + (index - 1.5) * width, summary[column].to_numpy(dtype=float), width=width, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels([pretty_method_label(str(item)) for item in summary["method"]])
    ax.set_ylim(0.0, 1.05)
    ax.set_ylabel("Rate")
    ax.set_title("Defense execution, effect confirmation, and security success")
    ax.legend()
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_llm_candidate_tradeoff_scatter(stage_df: pd.DataFrame, path: Path) -> Path:
    candidate_rows = _explode_ranked_candidates(stage_df)
    if candidate_rows.empty:
        return _placeholder_figure(path, "No LLM candidate-ranking tradeoff data available.")
    _write_figure_csv(path, candidate_rows)
    fig, ax = plt.subplots(figsize=(8, 5))
    non_selected = candidate_rows.loc[~candidate_rows["llm_selected"].fillna(False)].copy()
    selected = candidate_rows.loc[candidate_rows["llm_selected"].fillna(False)].copy()
    if not non_selected.empty:
        ax.scatter(
            non_selected["expected_qos_impact"],
            non_selected["expected_security_gain"],
            alpha=0.6,
            label="Candidate",
        )
    if not selected.empty:
        ax.scatter(
            selected["expected_qos_impact"],
            selected["expected_security_gain"],
            s=90,
            marker="*",
            label="Chosen candidate",
        )
        for row in selected.itertuples(index=False):
            ax.annotate(
                f"{getattr(row, 'scenario_id')}:{getattr(row, 'stage_id')}:{getattr(row, 'candidate_strategy_id')}",
                (getattr(row, "expected_qos_impact"), getattr(row, "expected_security_gain")),
                textcoords="offset points",
                xytext=(4, 4),
                fontsize=max(8, plt.rcParams["font.size"] - 2),
            )
    ax.set_xlabel("Expected QoS impact")
    ax.set_ylabel("Expected security gain")
    ax.set_title("LLM candidate tradeoff scatter")
    ax.grid(linestyle=":", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_llm_latency_distribution(stage_df: pd.DataFrame, path: Path) -> Path:
    if stage_df.empty or "llm_latency_ms" not in stage_df.columns:
        return _placeholder_figure(path, "No LLM latency data available.")
    latency_rows = stage_df[["method", "scenario_id", "stage_id", "llm_latency_ms"]].copy()
    latency_rows["method_label"] = latency_rows["method"].map(lambda value: pretty_method_label(str(value)))
    latencies = pd.to_numeric(latency_rows["llm_latency_ms"], errors="coerce").dropna()
    if latencies.empty:
        return _placeholder_figure(path, "No LLM latency data available.")
    _write_figure_csv(path, latency_rows)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(latencies.to_numpy(dtype=float), bins=min(10, max(3, len(latencies))), edgecolor="black")
    ax.set_xlabel("LLM decision latency (ms)")
    ax.set_ylabel("Stage count")
    ax.set_title("LLM latency distribution")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_llm_decision_timing_vs_path_stage(stage_df: pd.DataFrame, path: Path) -> Path:
    if stage_df.empty:
        return _placeholder_figure(path, "No stage data available for timing analysis.")
    llm_rows = stage_df.loc[stage_df["decision_source"].fillna("").eq("llm_defender")].copy()
    if llm_rows.empty:
        return _placeholder_figure(path, "No LLM stage data available for timing analysis.")
    export_columns = [
        "method",
        "scenario_id",
        "stage_id",
        "path_stage",
        "attack_effect_success",
        "defense_success",
        "defender_selected",
        "defender_strategy_id",
        "final_defender_strategy_id",
    ]
    export_frame = llm_rows[[column for column in export_columns if column in llm_rows.columns]].copy()
    export_frame["method_label"] = export_frame["method"].map(lambda value: pretty_method_label(str(value)))
    _write_figure_csv(path, export_frame)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(
        llm_rows["path_stage"],
        llm_rows["attack_effect_success"].fillna(False).astype(float),
        marker="o",
        label="Attack effect success",
    )
    ax.scatter(
        llm_rows["path_stage"],
        llm_rows["defense_success"].fillna(False).astype(float),
        marker="^",
        label="Defense success",
    )
    for row in llm_rows.itertuples(index=False):
        ax.annotate(
            str(getattr(row, "defender_selected", "") or getattr(row, "defender_strategy_id", "")),
            (getattr(row, "path_stage"), float(getattr(row, "defense_success"))),
            textcoords="offset points",
            xytext=(4, 4),
            fontsize=max(8, plt.rcParams["font.size"] - 2),
        )
    ax.set_xlabel("Path stage")
    ax.set_ylabel("Outcome flag")
    ax.set_yticks([0.0, 1.0])
    ax.set_title("LLM decision timing versus attack progression")
    ax.grid(linestyle=":", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _plot_stage_trace_case_study(stage_df: pd.DataFrame, *, path: Path, scenario_id: str | None) -> Path:
    if stage_df.empty:
        return _placeholder_figure(path, "No stage data available for the case-study trace.")
    filtered = stage_df
    if scenario_id:
        filtered = stage_df.loc[stage_df["scenario_id"] == scenario_id].copy()
    if filtered.empty:
        return _placeholder_figure(path, "No stage data available for the selected scenario.")
    filtered = filtered.sort_values("stage_id")
    export_columns = [
        "method",
        "scenario_id",
        "stage_id",
        "path_stage",
        "attack_effect_success",
        "defense_success",
        "defense_confirmed",
        "defender_strategy_id",
        "final_defender_strategy_id",
        "defender_action",
    ]
    _write_figure_csv(path, filtered[[column for column in export_columns if column in filtered.columns]].copy())
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(filtered["stage_id"], filtered["path_stage"], marker="o", label="Path stage")
    ax.plot(
        filtered["stage_id"],
        filtered["attack_effect_success"].astype(float),
        marker="s",
        label="Attack effect success",
    )
    ax.plot(
        filtered["stage_id"],
        filtered["defense_success"].astype(float),
        marker="^",
        label="Defense success",
    )
    ax.plot(
        filtered["stage_id"],
        filtered["defense_confirmed"].astype(float),
        marker="D",
        label="Defense confirmed",
    )
    for row in filtered.itertuples(index=False):
        ax.annotate(
            getattr(row, "defender_strategy_id", ""),
            (getattr(row, "stage_id"), getattr(row, "path_stage")),
            textcoords="offset points",
            xytext=(4, 4),
            fontsize=max(8, plt.rcParams["font.size"] - 2),
        )
    ax.set_xlabel("Stage ID")
    ax.set_ylabel("Path stage / outcome flag")
    ax.set_title(f"Stage trace case study for {scenario_id or 'selected scenario'}")
    ax.set_yticks([0, 1, 2, 3])
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _comparable_stage_frame(stage_df: pd.DataFrame) -> pd.DataFrame:
    if stage_df.empty or "comparable_stage" not in stage_df.columns:
        return stage_df.copy()
    comparable = stage_df.loc[stage_df["comparable_stage"].fillna(False)].copy()
    return comparable if not comparable.empty else stage_df.copy()


def _select_scenario(stage_df: pd.DataFrame, scenario_filter: Iterable[str] | None) -> str | None:
    requested = [item for item in (scenario_filter or []) if item]
    if requested:
        return requested[0]
    if stage_df.empty or "scenario_id" not in stage_df.columns:
        return None
    counts = stage_df["scenario_id"].dropna().value_counts()
    if counts.empty:
        return None
    return str(counts.index[0])


def _explode_ranked_candidates(stage_df: pd.DataFrame) -> pd.DataFrame:
    if stage_df.empty or "llm_ranked_candidates" not in stage_df.columns:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for row in stage_df.to_dict(orient="records"):
        ranked = row.get("llm_ranked_candidates") or []
        if not isinstance(ranked, list):
            continue
        selected_id = str(row.get("llm_selected_defender_strategy_id") or row.get("defender_selected") or "")
        for candidate in ranked:
            if not isinstance(candidate, dict):
                continue
            strategy_id = str(candidate.get("strategy_id") or candidate.get("id") or "").strip()
            if not strategy_id:
                continue
            rows.append(
                {
                    "scenario_id": row.get("scenario_id"),
                    "stage_id": row.get("stage_id"),
                    "candidate_strategy_id": strategy_id,
                    "expected_security_gain": float(candidate.get("expected_security_gain", candidate.get("estimated_security_gain_proxy", 0.0))),
                    "expected_qos_impact": float(candidate.get("expected_qos_impact", candidate.get("estimated_qos_cost_proxy", 0.0))),
                    "expected_controller_cost": float(candidate.get("expected_controller_cost", candidate.get("estimated_controller_cost_proxy", 0.0))),
                    "llm_selected": strategy_id == selected_id,
                }
            )
    return pd.DataFrame(rows)


def _pretty_metric_title(metric_name: str) -> str:
    mapping = {
        "sensor_to_edge_latency_delta_ms": "Sensor-to-edge latency delta",
        "edge_to_cloud_latency_delta_ms": "Edge-to-cloud latency delta",
        "throughput_delta_bps": "Throughput delta",
    }
    return mapping.get(metric_name, metric_name.replace("_", " ").title())


def _pretty_metric_ylabel(metric_name: str) -> str:
    mapping = {
        "sensor_to_edge_latency_delta_ms": "Latency delta (ms)",
        "edge_to_cloud_latency_delta_ms": "Latency delta (ms)",
        "throughput_delta_bps": "Throughput delta (bytes/s)",
    }
    return mapping.get(metric_name, metric_name.replace("_", " "))


def _placeholder_figure(path: Path, message: str) -> Path:
    _write_figure_csv(path, pd.DataFrame([{"message": message}]))
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.text(0.5, 0.5, message, ha="center", va="center", wrap=True)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _write_figure_csv(path: Path, frame: pd.DataFrame) -> Path:
    csv_path = path.with_suffix(".csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(csv_path, index=False)
    return csv_path
