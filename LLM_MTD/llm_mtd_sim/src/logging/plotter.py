import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def plot_all(csv_path, results_dir, steps_per_episode):
    if not os.path.exists(csv_path):
        return
    df = pd.read_csv(csv_path)
    if df.empty:
        return

    df = df.sort_values(["episode", "step"])
    last_steps = df.groupby("episode").tail(1)

    figs_dir = os.path.join(results_dir, "figs")
    os.makedirs(figs_dir, exist_ok=True)

    # Active vs pool size
    active_sizes = last_steps["active_keys"].apply(lambda x: len(json.loads(x)))
    pool_sizes = last_steps["pool_keys"].apply(lambda x: len(json.loads(x)))
    plt.figure()
    plt.plot(last_steps["episode"], active_sizes, label="active")
    plt.plot(last_steps["episode"], pool_sizes, label="pool")
    plt.title("Active vs Pool size")
    plt.legend()
    plt.savefig(os.path.join(figs_dir, "fig_active_pool.png"))
    plt.close()

    # Defender q trajectories
    plt.figure()
    q_cols = [c for c in last_steps.columns if c.startswith("q_GD")]
    for col in q_cols:
        plt.plot(last_steps["episode"], last_steps[col], label=col)
    plt.title("Defender q trajectories")
    plt.legend(ncol=2, fontsize=8)
    plt.savefig(os.path.join(figs_dir, "fig_defender_q.png"))
    plt.close()

    # Attacker path and tactics
    plt.figure()
    for col in ["s_P1", "s_P2"]:
        if col in last_steps:
            plt.plot(last_steps["episode"], last_steps[col], label=col)
    for col in ["pP1_GA1", "pP1_GA2", "pP1_GA3"]:
        if col in last_steps:
            plt.plot(last_steps["episode"], last_steps[col], label=col)
    for col in ["pP2_GA1", "pP2_GA2", "pP2_GA3"]:
        if col in last_steps:
            plt.plot(last_steps["episode"], last_steps[col], label=col)
    for col in ["p_GA1", "p_GA2", "p_GA3"]:
        if col in last_steps:
            plt.plot(last_steps["episode"], last_steps[col], label=col, linestyle="--")
    plt.title("Attacker paths and tactics")
    plt.legend(ncol=2, fontsize=8)
    plt.savefig(os.path.join(figs_dir, "fig_attacker_p.png"))
    plt.close()

    # Costs
    cost_df = df.groupby("episode").mean(numeric_only=True)
    plt.figure()
    plt.plot(cost_df.index, cost_df["DC_ASSC"], label="ASSC")
    plt.plot(cost_df.index, cost_df["DC_NC"], label="NC")
    plt.plot(cost_df.index, cost_df["DC_AIC"], label="AIC")
    plt.plot(cost_df.index, cost_df["AC_total"], label="AC")
    plt.title("Costs")
    plt.legend()
    plt.savefig(os.path.join(figs_dir, "fig_costs.png"))
    plt.close()

    # LLM latency
    plt.figure()
    latency_mean = df.groupby("episode")["llm_latency_s"].mean()
    summary_latency = pd.to_numeric(last_steps["episode_summary_latency_s"], errors="coerce")
    plt.plot(latency_mean.index, latency_mean.values, label="macro")
    plt.plot(last_steps["episode"], summary_latency.values, label="summary")
    plt.title("LLM latency")
    plt.legend()
    plt.savefig(os.path.join(figs_dir, "fig_llm_latency.png"))
    plt.close()

    # Security payoff
    plt.figure()
    xi_mean = df.groupby("episode")["xi_total"].mean()
    ea_mean = df.groupby("episode")["EA_payoff_mean"].mean()
    ed_mean = df.groupby("episode")["ED_payoff_mean"].mean()
    plt.plot(xi_mean.index, xi_mean.values, label="xi_total")
    plt.plot(ea_mean.index, ea_mean.values, label="EA_mean")
    plt.plot(ed_mean.index, ed_mean.values, label="ED_mean")
    plt.title("Security and payoffs")
    plt.legend()
    plt.savefig(os.path.join(figs_dir, "fig_security_payoff.png"))
    plt.close()
