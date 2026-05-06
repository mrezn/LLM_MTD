"""
Export CSV data used by evaluate.py and main.py figures.

Usage:
  python export_figure_data.py --results_dir results_eval --method LLM-Full
  python export_figure_data.py --results_dir results_eval --method LLM-Full --sim_results_dir results
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def parse_json(text, default):
    if text is None or text == "" or (isinstance(text, float) and np.isnan(text)):
        return default
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except Exception:
        return default


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def require_file(path, label):
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def export_active_pool_size(episodes_df, out_path):
    grouped = episodes_df.groupby("episode_k")[["active_size", "pool_size"]].mean().reset_index()
    grouped.to_csv(out_path, index=False)


def export_defender_barq_trends(episodes_df, out_path):
    records = []
    for _, row in episodes_df.iterrows():
        bar_q = parse_json(row.get("bar_q_json"), {})
        for key, val in bar_q.items():
            records.append({"episode_k": row["episode_k"], "key": key, "val": val})
    if not records:
        pd.DataFrame(columns=["episode_k"]).to_csv(out_path, index=False)
        return
    df = pd.DataFrame(records)
    pivot = (
        df.pivot_table(index="episode_k", columns="key", values="val", aggfunc="mean")
        .fillna(0.0)
        .reset_index()
    )
    pivot.to_csv(out_path, index=False)


def export_attacker_strategy_trends(episodes_df, out_path):
    records = []
    for _, row in episodes_df.iterrows():
        state = parse_json(row.get("attacker_state_json"), {})
        p_marg = state.get("p_marg")
        s = state.get("s")
        if p_marg is not None:
            for i, val in enumerate(p_marg):
                records.append(
                    {"episode_k": row["episode_k"], "key": f"p_GA{i+1}", "val": val}
                )
        if s is not None and len(s) >= 2:
            records.append({"episode_k": row["episode_k"], "key": "s_P1", "val": s[0]})
            records.append({"episode_k": row["episode_k"], "key": "s_P2", "val": s[1]})
    if not records:
        pd.DataFrame(columns=["episode_k"]).to_csv(out_path, index=False)
        return
    df = pd.DataFrame(records)
    pivot = (
        df.pivot_table(index="episode_k", columns="key", values="val", aggfunc="mean")
        .fillna(0.0)
        .reset_index()
    )
    pivot.to_csv(out_path, index=False)


def export_security_payoff(episodes_df, out_path):
    grouped = episodes_df.groupby("episode_k")[["SAL", "SAP", "UA", "UD"]]
    mean_df = grouped.mean()
    std_df = grouped.std()
    combined = pd.concat(
        [
            mean_df.add_suffix("_mean"),
            std_df.add_suffix("_std"),
        ],
        axis=1,
    ).reset_index()
    combined.to_csv(out_path, index=False)


def export_defender_costs(episodes_df, out_path):
    grouped = episodes_df.groupby("episode_k")[["ASSC", "NC", "AIC", "DC"]].mean().reset_index()
    grouped.to_csv(out_path, index=False)


def export_attacker_costs(episodes_df, out_path):
    grouped = episodes_df.groupby("episode_k")[["AC"]].mean().reset_index()
    grouped.to_csv(out_path, index=False)


def export_llm_latency(episodes_df, out_dir):
    grouped = episodes_df.groupby("episode_k")[["t_macro", "t_mut", "t_summary"]].mean()
    grouped["t_total"] = grouped.sum(axis=1)
    grouped.reset_index().to_csv(out_dir / "data_llm_latency.csv", index=False)

    total = (
        episodes_df["t_macro"].fillna(0)
        + episodes_df["t_mut"].fillna(0)
        + episodes_df["t_summary"].fillna(0)
    )
    counts, bins = np.histogram(total.values, bins=10)
    hist_df = pd.DataFrame(
        {
            "bin_left": bins[:-1],
            "bin_right": bins[1:],
            "count": counts,
        }
    )
    hist_df.to_csv(out_dir / "data_llm_latency_hist.csv", index=False)


def export_robustness_boxplots(runs_df, out_dir):
    data = runs_df[["attacker_class_id", "SAL_cum"]].copy()
    data.to_csv(out_dir / "data_robustness_boxplots.csv", index=False)

    wc = runs_df.groupby(["scenario_id", "trial_id"])["SAL_cum"].max().mean()
    pd.DataFrame([{"worst_case_mean": wc}]).to_csv(
        out_dir / "data_robustness_boxplots_worst_case.csv", index=False
    )


def export_success_rate_bars(summary_df, out_path):
    cols = ["method", "SR", "SR_wc"]
    summary_df[cols].to_csv(out_path, index=False)


def export_coverage_bars(summary_df, out_path):
    cols = ["method", "Coverage_mean", "Cov_ge_0_5"]
    summary_df[cols].to_csv(out_path, index=False)


def export_sim_fig_data(sim_log_path, figs_dir):
    df = pd.read_csv(sim_log_path)
    if df.empty:
        return

    df = df.sort_values(["episode", "step"])
    last_steps = df.groupby("episode").tail(1)

    # fig_active_pool.png
    active_sizes = last_steps["active_keys"].apply(lambda x: len(parse_json(x, [])))
    pool_sizes = last_steps["pool_keys"].apply(lambda x: len(parse_json(x, [])))
    pd.DataFrame(
        {
            "episode": last_steps["episode"].values,
            "active_size": active_sizes.values,
            "pool_size": pool_sizes.values,
        }
    ).to_csv(figs_dir / "data_fig_active_pool.csv", index=False)

    # fig_defender_q.png
    q_cols = [c for c in last_steps.columns if c.startswith("q_GD")]
    if q_cols:
        last_steps[["episode"] + q_cols].to_csv(figs_dir / "data_fig_defender_q.csv", index=False)

    # fig_attacker_p.png
    attacker_cols = []
    for col in ["s_P1", "s_P2"]:
        if col in last_steps:
            attacker_cols.append(col)
    for col in ["pP1_GA1", "pP1_GA2", "pP1_GA3"]:
        if col in last_steps:
            attacker_cols.append(col)
    for col in ["pP2_GA1", "pP2_GA2", "pP2_GA3"]:
        if col in last_steps:
            attacker_cols.append(col)
    for col in ["p_GA1", "p_GA2", "p_GA3"]:
        if col in last_steps:
            attacker_cols.append(col)
    if attacker_cols:
        last_steps[["episode"] + attacker_cols].to_csv(
            figs_dir / "data_fig_attacker_p.csv", index=False
        )

    # fig_costs.png
    cost_cols = [c for c in ["DC_ASSC", "DC_NC", "DC_AIC", "AC_total"] if c in df.columns]
    if cost_cols:
        cost_df = df.groupby("episode")[cost_cols].mean().reset_index()
        cost_df.to_csv(figs_dir / "data_fig_costs.csv", index=False)

    # fig_llm_latency.png
    if "llm_latency_s" in df.columns:
        latency_mean = df.groupby("episode")["llm_latency_s"].mean()
        if "episode_summary_latency_s" in last_steps:
            summary_latency = pd.to_numeric(
                last_steps["episode_summary_latency_s"], errors="coerce"
            )
        else:
            summary_latency = pd.Series([np.nan] * len(last_steps), index=last_steps.index)
        pd.DataFrame(
            {
                "episode": latency_mean.index.values,
                "macro_latency_mean": latency_mean.values,
                "summary_latency": summary_latency.values,
            }
        ).to_csv(figs_dir / "data_fig_llm_latency.csv", index=False)

    # fig_security_payoff.png
    sec_cols = [c for c in ["xi_total", "EA_payoff_mean", "ED_payoff_mean"] if c in df.columns]
    if sec_cols:
        sec_df = df.groupby("episode")[sec_cols].mean().reset_index()
        sec_df.to_csv(figs_dir / "data_fig_security_payoff.csv", index=False)


def main():
    parser = argparse.ArgumentParser(description="Export CSVs backing evaluate.py and main.py figures.")
    parser.add_argument("--results_dir", default="results_eval")
    parser.add_argument("--method", default="LLM-Full")
    parser.add_argument("--out_dir", default=None)
    parser.add_argument("--sim_results_dir", default="results")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    method = args.method
    episodes_path = require_file(results_dir / f"episodes_{method}.csv", "Episodes CSV")
    runs_path = require_file(results_dir / f"runs_{method}.csv", "Runs CSV")
    summary_path = require_file(results_dir / "summary.csv", "Summary CSV")

    episodes_df = pd.read_csv(episodes_path)
    runs_df = pd.read_csv(runs_path)
    summary_df = pd.read_csv(summary_path)

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = results_dir / "figures" / method
    ensure_dir(out_dir)

    export_active_pool_size(episodes_df, out_dir / "data_active_pool_size.csv")
    export_defender_barq_trends(episodes_df, out_dir / "data_defender_barq_trends.csv")
    export_attacker_strategy_trends(episodes_df, out_dir / "data_attacker_strategy_trends.csv")
    export_security_payoff(episodes_df, out_dir / "data_security_payoff.csv")
    export_defender_costs(episodes_df, out_dir / "data_defender_costs.csv")
    export_attacker_costs(episodes_df, out_dir / "data_attacker_costs.csv")
    export_llm_latency(episodes_df, out_dir)
    export_robustness_boxplots(runs_df, out_dir)
    export_success_rate_bars(summary_df, out_dir / "data_success_rate_bars.csv")
    export_coverage_bars(summary_df, out_dir / "data_coverage_bars.csv")

    sim_results_dir = Path(args.sim_results_dir)
    sim_log_path = sim_results_dir / "sim_log.csv"
    figs_dir = sim_results_dir / "figs"
    if sim_log_path.exists():
        ensure_dir(figs_dir)
        export_sim_fig_data(sim_log_path, figs_dir)


if __name__ == "__main__":
    main()
