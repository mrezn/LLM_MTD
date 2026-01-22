import json

import pandas as pd


class SimLogger:
    def __init__(self, defender_keys):
        self.defender_keys = list(defender_keys)
        self.rows = []
        self.summaries = []
        self.columns = [
            "episode",
            "step",
            "path",
            "chosen_path",
            "state",
            "active_keys",
            "pool_keys",
            "promoted_key",
            "demoted_keys",
            "chosen_GA",
            "chosen_GD",
            "xi_total",
            "xi_1",
            "xi_2",
            "xi_3",
            "EA_payoff_mean",
            "EA_payoff_max",
            "ED_payoff_mean",
            "ED_payoff_max",
            "AC_total",
            "AC_T",
            "AC_H",
            "AC_K",
            "AC_R",
            "AC_D",
            "DC_total",
            "DC_ASSC",
            "DC_NC",
            "DC_AIC",
            "llm_latency_s",
            "s_P1",
            "s_P2",
            "g_P1",
            "g_P2",
            "BRpath_P1",
            "BRpath_P2",
            "piPath_P1",
            "piPath_P2",
            "pP1_GA1",
            "pP1_GA2",
            "pP1_GA3",
            "pP2_GA1",
            "pP2_GA2",
            "pP2_GA3",
            "BR_GA1",
            "BR_GA2",
            "BR_GA3",
            "piA_GA1",
            "piA_GA2",
            "piA_GA3",
            "p_GA1",
            "p_GA2",
            "p_GA3",
        ]
        self.columns += [f"q_{k}" for k in self.defender_keys]
        self.columns += [f"qbar_{k}" for k in self.defender_keys]
        self.columns += ["best_def_key", "episode_summary_latency_s"]

    def log_step(self, row):
        self.rows.append(row)

    def update_last_step(self, updates):
        if self.rows:
            self.rows[-1].update(updates)

    def log_summary(self, record):
        self.summaries.append(record)

    def write_csv(self, path):
        df = pd.DataFrame(self.rows)
        df = df.reindex(columns=self.columns)
        df.to_csv(path, index=False)

    def write_jsonl(self, path):
        with open(path, "w", encoding="utf-8") as f:
            for record in self.summaries:
                f.write(json.dumps(record) + "\n")
