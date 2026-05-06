import argparse
import json
import os
from collections import deque

import numpy as np

from src.config import load_config
from src.game.attacker_controller import AttackerController
from src.game.evolutionary import apply_active_pool_control, defender_update
from src.game.payoffs import build_payoff_matrices
from src.game.strategies import ATTACKERS, DEFENDERS, DEFENDER_KEYS, INITIAL_ACTIVE, INITIAL_POOL
from src.llm.controller import LLMController
from src.logging.logger import SimLogger
from src.logging.plotter import plot_all
from src.system.hospital_env import HospitalEnv
from src.utils import choice_from_probs, normalize


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    return parser.parse_args()


def main():
    args = parse_args()
    root = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(root, config_path)

    cfg = load_config(config_path)

    rng = np.random.default_rng(cfg["simulation"]["seed"])

    results_dir = os.path.join(root, "results")
    figs_dir = os.path.join(results_dir, "figs")
    os.makedirs(figs_dir, exist_ok=True)

    env = HospitalEnv(cfg, rng)
    llm = LLMController(cfg)
    logger = SimLogger(DEFENDER_KEYS)
    attacker_ctrl = AttackerController(cfg, rng)

    attackers = list(ATTACKERS)
    defenders_by_key = {d.key: d for d in DEFENDERS}

    active_keys = list(INITIAL_ACTIVE)
    pool_keys = list(INITIAL_POOL)
    active_defenders = [defenders_by_key[k] for k in active_keys]

    q = normalize(np.ones(len(active_keys)))
    last_sigma_llm = q.copy()

    low_q_streak = {k: 0 for k in active_keys}
    dc_history = {k: deque(maxlen=cfg["active_pool"]["dc_window"]) for k in active_keys}
    last_promo_episode = -cfg["active_pool"]["promote_every"]
    no_demotion_episodes = 0

    recent_promotions = deque(maxlen=3)
    recent_demotions = deque(maxlen=3)

    episodes = cfg["simulation"]["episodes"]
    steps_per_episode = cfg["simulation"]["steps_per_episode"]

    for ep in range(1, episodes + 1):
        if ep > 1:
            assert attacker_ctrl.episode_counter == ep - 1, (
                "attacker distributions were reset per episode"
            )
        attacker_ctrl.start_episode(ep)

        episode_ctx = env.reset_episode(ep)
        states = episode_ctx["states"]

        fD_hist = []
        xi_list = []
        ac_list = []
        dc_list = []
        assc_list = []
        nc_list = []
        aic_list = []
        ea_mean_list = []
        ed_mean_list = []

        last_llm_suggestion = {"promote_key": "NONE", "demote_keys": []}
        summary_latency = ""

        llm_lambda = cfg["evolutionary"]["llm_lambda"]
        if last_sigma_llm is None or len(last_sigma_llm) != len(q):
            sigma_eval = q
        else:
            sigma_eval = np.array(last_sigma_llm, dtype=float)
        qbar_eval = normalize((1 - llm_lambda) * q + llm_lambda * sigma_eval)

        def env_preview(path, state_idx, defender_bar_q, p_path):
            return env.preview_payoffs(
                path,
                state_idx,
                defender_bar_q,
                p_path,
                active_defenders,
                attackers,
                build_payoff_matrices,
            )

        g_P1, g_P2, path_eval = attacker_ctrl.evaluate_paths(ep, qbar_eval, env_preview)
        chosen_path, path_policy = attacker_ctrl.choose_path(ep, g_P1, g_P2)

        var_p1 = float(np.var(path_eval["f_P1"]))
        var_p2 = float(np.var(path_eval["f_P2"]))
        if var_p1 < 1e-6 or var_p2 < 1e-6:
            print("attacker payoff diversity nearly zero -- evolution may stall.")

        fit_p1 = attacker_ctrl.tactic_fitness(path_eval["f_P1"], "P1")
        fit_p2 = attacker_ctrl.tactic_fitness(path_eval["f_P2"], "P2")
        if float(np.std(fit_p1)) < 1e-8 or float(np.std(fit_p2)) < 1e-8:
            print("fitness nearly equal -- check SAL/AC differences or scaling tau_A.")

        for step_idx in range(1, steps_per_episode + 1):
            q_prev = q.copy()
            p_marg = attacker_ctrl.marginal_p()

            step_info = env.step(
                chosen_path,
                step_idx,
                active_defenders=active_defenders,
                attackers=attackers,
                payoff_builder=build_payoff_matrices,
            )

            A = step_info["A"]
            B = step_info["B"]
            aux = step_info["aux"]

            macro_context = {
                "active_keys": list(active_keys),
                "q": {k: float(q_prev[i]) for i, k in enumerate(active_keys)},
                "pool_keys": list(pool_keys),
                "p": {a.key: float(p_marg[i]) for i, a in enumerate(attackers)},
                "xi": float(step_info["xi_base"]),
                "xi_by_hop": step_info["xi_by_hop_base"],
                "sal_mean": aux["summary"]["sal_mean"],
                "sap_mean": aux["summary"]["sap_mean"],
                "dc_breakdown": aux["summary"]["dc_mean"],
                "ac_breakdown": aux["summary"]["ac_mean"],
                "recent_promotions": list(recent_promotions),
                "recent_demotions": list(recent_demotions),
                "constraints": {
                    "max_active": cfg["active_pool"]["max_active"],
                    "demote_q_min": cfg["active_pool"]["demote_q_min"],
                    "demote_patience": cfg["active_pool"]["demote_patience"],
                },
            }

            llm_result = llm.macro_decision(macro_context, active_keys, pool_keys, q_prev)
            sigma_llm = np.array(llm_result["sigma"], dtype=float)
            mutation = llm_result["mutation"]
            llm_latency = llm_result["latency_s"]
            last_sigma_llm = sigma_llm

            qbar = normalize((1 - llm_lambda) * q_prev + llm_lambda * sigma_llm)
            fA_state = A @ qbar

            chosen_ga, tactic_debug = attacker_ctrl.choose_tactic(ep, chosen_path, fA_state)

            chosen_gd_idx, chosen_gd_key = choice_from_probs(rng, active_keys, qbar)

            fD = B.T @ p_marg
            fD_hist.append(fD)

            q, _ = defender_update(
                q_prev,
                fD,
                cfg["evolutionary"]["eta"],
                cfg["evolutionary"]["omega_D"],
                llm_lambda,
                sigma_llm,
                mutation,
            )

            for def_key in active_keys:
                dc_by_attacker = aux["defender_dc"][def_key]["dc_by_attacker"]
                expected_dc = float(np.dot(p_marg, dc_by_attacker))
                dc_history.setdefault(
                    def_key, deque(maxlen=cfg["active_pool"]["dc_window"])
                ).append(expected_dc)

            pair = aux["pair_details"][chosen_ga][chosen_gd_key]
            xi_total = pair["xi_total"]
            xi_by_hop = pair["xi_by_hop"]

            ea_mean = float(np.mean(A))
            ea_max = float(np.max(A))
            ed_mean = float(np.mean(B))
            ed_max = float(np.max(B))

            ea_mean_list.append(ea_mean)
            ed_mean_list.append(ed_mean)
            xi_list.append(xi_total)
            ac_list.append(pair["AC_total"])
            dc_list.append(pair["DC_total"])
            assc_list.append(pair["DC_components"]["ASSC"])
            nc_list.append(pair["DC_components"]["NC"])
            aic_list.append(pair["DC_components"]["AIC"])

            q_all = {k: 0.0 for k in DEFENDER_KEYS}
            qbar_all = {k: 0.0 for k in DEFENDER_KEYS}
            for i, k in enumerate(active_keys):
                q_all[k] = float(q_prev[i])
                qbar_all[k] = float(qbar[i])

            best_def_key = active_keys[int(np.argmax(qbar))]

            br_path = path_policy["BRpath"]
            pi_path = path_policy["piPath"]

            row = {
                "episode": ep,
                "step": step_idx,
                "path": chosen_path,
                "chosen_path": chosen_path,
                "state": states[step_idx - 1],
                "active_keys": json.dumps(active_keys),
                "pool_keys": json.dumps(pool_keys),
                "promoted_key": "",
                "demoted_keys": json.dumps([]),
                "chosen_GA": chosen_ga,
                "chosen_GD": chosen_gd_key,
                "xi_total": xi_total,
                "xi_1": xi_by_hop[0] if len(xi_by_hop) > 0 else 0.0,
                "xi_2": xi_by_hop[1] if len(xi_by_hop) > 1 else 0.0,
                "xi_3": xi_by_hop[2] if len(xi_by_hop) > 2 else 0.0,
                "EA_payoff_mean": ea_mean,
                "EA_payoff_max": ea_max,
                "ED_payoff_mean": ed_mean,
                "ED_payoff_max": ed_max,
                "AC_total": pair["AC_total"],
                "AC_T": pair["AC_components"]["T"],
                "AC_H": pair["AC_components"]["H"],
                "AC_K": pair["AC_components"]["K"],
                "AC_R": pair["AC_components"]["R"],
                "AC_D": pair["AC_components"]["D"],
                "DC_total": pair["DC_total"],
                "DC_ASSC": pair["DC_components"]["ASSC"],
                "DC_NC": pair["DC_components"]["NC"],
                "DC_AIC": pair["DC_components"]["AIC"],
                "llm_latency_s": llm_latency,
                "s_P1": float(attacker_ctrl.s[0]),
                "s_P2": float(attacker_ctrl.s[1]),
                "g_P1": float(g_P1),
                "g_P2": float(g_P2),
                "BRpath_P1": float(br_path[0]),
                "BRpath_P2": float(br_path[1]),
                "piPath_P1": float(pi_path[0]),
                "piPath_P2": float(pi_path[1]),
                "pP1_GA1": float(attacker_ctrl.p_P1[0]),
                "pP1_GA2": float(attacker_ctrl.p_P1[1]),
                "pP1_GA3": float(attacker_ctrl.p_P1[2]),
                "pP2_GA1": float(attacker_ctrl.p_P2[0]),
                "pP2_GA2": float(attacker_ctrl.p_P2[1]),
                "pP2_GA3": float(attacker_ctrl.p_P2[2]),
                "BR_GA1": float(tactic_debug["BR"][0]),
                "BR_GA2": float(tactic_debug["BR"][1]),
                "BR_GA3": float(tactic_debug["BR"][2]),
                "piA_GA1": float(tactic_debug["pi"][0]),
                "piA_GA2": float(tactic_debug["pi"][1]),
                "piA_GA3": float(tactic_debug["pi"][2]),
                "p_GA1": float(p_marg[0]),
                "p_GA2": float(p_marg[1]),
                "p_GA3": float(p_marg[2]),
                "best_def_key": best_def_key,
                "episode_summary_latency_s": "",
            }
            for k in DEFENDER_KEYS:
                row[f"q_{k}"] = q_all[k]
            for k in DEFENDER_KEYS:
                row[f"qbar_{k}"] = qbar_all[k]

            logger.log_step(row)

            last_llm_suggestion = {
                "promote_key": llm_result["promote_key"],
                "demote_keys": llm_result["demote_keys"],
            }

        top_defs = sorted(
            [(k, float(q[i])) for i, k in enumerate(active_keys)],
            key=lambda x: x[1],
            reverse=True,
        )[:3]
        chosen_defender = top_defs[0][0] if top_defs else ""

        attacker_p = {attackers[i].key: float(attacker_ctrl.marginal_p()[i]) for i in range(len(attackers))}

        summary_ctx = {
            "episode": ep,
            "path": chosen_path,
            "state_sequence": states,
            "top_defenders": top_defs,
            "chosen_defender": chosen_defender,
            "attacker_p": attacker_p,
            "xi_values": xi_list,
            "costs": {
                "DC_total": float(np.mean(dc_list)) if dc_list else 0.0,
                "ASSC": float(np.mean(assc_list)) if assc_list else 0.0,
                "NC": float(np.mean(nc_list)) if nc_list else 0.0,
                "AIC": float(np.mean(aic_list)) if aic_list else 0.0,
                "AC_total": float(np.mean(ac_list)) if ac_list else 0.0,
            },
            "payoffs": {
                "EA_mean": float(np.mean(ea_mean_list)) if ea_mean_list else 0.0,
                "ED_mean": float(np.mean(ed_mean_list)) if ed_mean_list else 0.0,
            },
        }
        summary_text, summary_latency = llm.episode_summary(summary_ctx)

        logger.log_summary(
            {
                "episode": ep,
                "path": chosen_path,
                "top_defenders": top_defs,
                "chosen_defender": chosen_defender,
                "attacker_p": attacker_p,
                "xi_avg": float(np.mean(xi_list)) if xi_list else 0.0,
                "costs": summary_ctx["costs"],
                "llm_summary_latency_s": summary_latency,
                "text": summary_text,
            }
        )

        attacker_ctrl.update_tactics(ep, path_eval["f_P1"], path_eval["f_P2"])
        attacker_ctrl.update_paths(ep, g_P1, g_P2)

        fD_episode = np.mean(np.vstack(fD_hist), axis=0) if fD_hist else np.zeros(len(active_keys))
        demote_q_min = cfg["active_pool"]["demote_q_min"]
        for i, key in enumerate(active_keys):
            if q[i] < demote_q_min:
                low_q_streak[key] = low_q_streak.get(key, 0) + 1
            else:
                low_q_streak[key] = 0

        (
            active_keys,
            pool_keys,
            q,
            promoted_key,
            demoted_keys,
            low_q_streak,
            dc_history,
            last_promo_episode,
            no_demotion_episodes,
        ) = apply_active_pool_control(
            active_keys,
            pool_keys,
            q,
            fD_episode,
            cfg,
            low_q_streak,
            dc_history,
            last_llm_suggestion,
            last_promo_episode,
            ep,
            no_demotion_episodes,
        )

        active_defenders = [defenders_by_key[k] for k in active_keys]

        if promoted_key:
            recent_promotions.append(f"{promoted_key}@E{ep}")
        if demoted_keys:
            for k in demoted_keys:
                recent_demotions.append(f"{k}@E{ep}")

        logger.update_last_step(
            {
                "promoted_key": promoted_key,
                "demoted_keys": json.dumps(demoted_keys),
                "episode_summary_latency_s": summary_latency,
            }
        )

        print(
            f"Episode {ep}/{episodes} | active={len(active_keys)} pool={len(pool_keys)}",
            flush=True,
        )

    logger.write_csv(os.path.join(results_dir, "sim_log.csv"))
    logger.write_jsonl(os.path.join(results_dir, "episode_summaries.jsonl"))
    plot_all(os.path.join(results_dir, "sim_log.csv"), results_dir, steps_per_episode)


if __name__ == "__main__":
    main()
