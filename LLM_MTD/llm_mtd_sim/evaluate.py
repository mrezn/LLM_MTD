"""
Evaluation pipeline for LLM-MTD in a hospital edge-cloud simulation.

This module can be executed as `python evaluate.py` or imported with
`from evaluate import run_evaluation`.
"""

import argparse
import copy
import json
import math
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from src.system.hospital_env import HospitalEnv
from src.llm.controller import LLMController
from src.game.attacker_controller import AttackerController
from src.game.evolutionary import apply_active_pool_control, defender_update
from src.game.payoffs import apply_defender_to_target, beta_y, build_payoff_matrices, theta_xy
from src.game.strategies import ATTACKERS, DEFENDERS, INITIAL_ACTIVE, INITIAL_POOL


DEFAULT_METHODS = ["Static", "Evo", "LLM-Macro", "LLM-Mut", "LLM-Full"]
DEFAULT_DELTA = 0.98
DEFAULT_THRESHOLDS = {
    "tau_SAL": 40.0,
    "tau_theta": 0.6,
    "tau_safe": 15.0,
    "tau_xi": 1.0,
}
DEFAULT_SEED = 42
DEFAULT_HORIZON = 40
DEFAULT_NUM_TRIALS = 5
DEFAULT_NUM_SCENARIOS = 3
DEFAULT_DEFENDER_KEYS = ["GD1", "GD2", "GD3", "GD4", "GD5", "GD6", "GD7", "GD8"]
DEFAULT_CRITICAL_ASSETS = ["edge_vm_medical", "cloud_store"]

ATTACKER_CLASSES = [
    {"id": "A1", "pi": 0.80, "omega_A": 0.70, "rho_A": 0.20, "tau_BR_A": 3.0, "C_switch": 1.0},
    {"id": "A2", "pi": 0.70, "omega_A": 0.60, "rho_A": 0.10, "tau_BR_A": 4.0, "C_switch": 1.5},
    {"id": "A3", "pi": 0.90, "omega_A": 0.90, "rho_A": 0.40, "tau_BR_A": 2.5, "C_switch": 0.5},
    {"id": "A4", "pi": 0.85, "omega_A": 0.80, "rho_A": 0.50, "tau_BR_A": 2.0, "C_switch": 0.8},
]

REQUIRED_METRIC_KEYS = [
    "SAL",
    "SAP",
    "UA",
    "UD",
    "xi_z",
    "theta_xy",
    "beta_y",
    "AC",
    "DC",
    "ASSC",
    "NC",
    "AIC",
    "Gp",
    "R_alpha",
]

METRIC_ALIASES = {
    "SAL": ["sal"],
    "SAP": ["sap"],
    "UA": ["U_A", "ua"],
    "UD": ["U_D", "ud"],
    "xi_z": ["xi_total", "xi", "xi_base"],
    "theta_xy": ["theta", "theta_xy_mean"],
    "beta_y": ["beta", "beta_y_mean"],
    "AC": ["AC_total", "ac"],
    "DC": ["DC_total", "dc"],
    "ASSC": ["DC_ASSC", "assc"],
    "NC": ["DC_NC", "nc"],
    "AIC": ["DC_AIC", "aic"],
    "Gp": ["G_p", "gp"],
    "R_alpha": ["Ralpha", "r_alpha"],
}


def stable_hash(*parts):
    text = "|".join(str(p) for p in parts)
    h = 2166136261
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def derive_seed(base_seed, *parts):
    return (int(base_seed) + stable_hash(*parts)) & 0xFFFFFFFF


def normalize_probs(values):
    arr = np.array(values, dtype=float)
    if arr.size == 0:
        return arr
    arr = np.clip(arr, 0.0, None)
    total = float(np.sum(arr))
    if total <= 0:
        return np.ones_like(arr) / float(arr.size)
    return arr / total


def scalarize(value):
    if isinstance(value, dict):
        values = list(value.values())
    elif isinstance(value, (list, tuple, np.ndarray)):
        values = value
    else:
        try:
            return float(value)
        except Exception:
            return float("nan")
    if len(values) == 0:
        return float("nan")
    return float(np.mean(np.array(values, dtype=float)))


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def _json_default(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def to_json(value):
    return json.dumps(
        value, separators=(",", ":"), sort_keys=True, default=_json_default
    )


def parse_json(text, default):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def get_git_hash(repo_root):
    git_dir = Path(repo_root) / ".git"
    head = git_dir / "HEAD"
    if not head.exists():
        return None
    ref = head.read_text(encoding="utf-8").strip()
    if ref.startswith("ref:"):
        ref_path = git_dir / ref.split(" ", 1)[1].strip()
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        return None
    return ref


def load_component(module_path, class_name):
    try:
        module = __import__(module_path, fromlist=[class_name])
    except Exception as exc:
        raise RuntimeError(
            f"Failed to import {class_name} from {module_path}: {exc}"
        ) from exc
    if not hasattr(module, class_name):
        raise RuntimeError(f"Missing {class_name} in {module_path}")
    return getattr(module, class_name)


def get_attr_any(obj, names, label):
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise RuntimeError(f"{label} missing attribute(s): {', '.join(names)}")


def coerce_distribution(dist, keys, label):
    if isinstance(dist, dict):
        vec = np.array([dist.get(k, 0.0) for k in keys], dtype=float)
        return normalize_probs(vec)
    arr = np.array(dist, dtype=float)
    if arr.ndim != 1 or arr.size != len(keys):
        raise RuntimeError(
            f"{label} length mismatch (expected {len(keys)}, got {arr.size})"
        )
    return normalize_probs(arr)


def call_optional(obj, method_names, *args, **kwargs):
    for name in method_names:
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except TypeError:
                if args or kwargs:
                    return fn()
                raise
    return None


def validate_env(env):
    if not callable(getattr(env, "reset", None)) and not callable(
        getattr(env, "reset_episode", None)
    ):
        raise RuntimeError("HospitalEdgeCloudEnv missing method 'reset'")
    for name in ["preview_payoffs", "step"]:
        if not callable(getattr(env, name, None)):
            raise RuntimeError(f"HospitalEdgeCloudEnv missing method '{name}'")


def validate_attacker(attacker):
    required = [
        "evaluate_paths",
        "choose_path",
        "choose_tactic",
        "update_tactics",
        "update_paths",
    ]
    for name in required:
        if not callable(getattr(attacker, name, None)):
            raise RuntimeError(f"AttackerController missing method '{name}'")


def build_base_config(seed, horizon):
    return {
        "simulation": {
            "seed": int(seed),
            "episodes": int(horizon),
            "steps_per_episode": 2,
            "z_max": 3,
            "gamma": 0.6,
        },
        "system": {
            "Z": 25,
            "gamma": 0.6,
            "z_max": 3,
            "R_cia": {"c": 4, "i": 6, "a": 5},
            "lambdas": {"c": 0.70, "i": 0.65, "a": 0.60},
        },
        "costs": {
            "attacker": {
                "w_T": 1.0,
                "w_H": 0.8,
                "w_K": 0.9,
                "w_R": 0.6,
                "w_D": 0.7,
            },
            "mu_y": 0.2,
            "beta_reg": 0.5,
            "third_party": {"alpha_I": 0.3, "I": 1.0},
            "defender": {"SQ": 10, "k_s": 0.30, "alpha_ass": 0.8, "alpha_aic": 4.0},
        },
        "attacker": {
            "eta_A": 0.20,
            "omega_A": 0.70,
            "tau_A": 8.0,
            "eps_A": 0.05,
            "rho_A": 0.30,
            "tau_BR_A": 3.0,
            "eta_P": 0.10,
            "omega_P": 0.70,
            "tau_P": 8.0,
            "eps_P": 0.03,
            "rho_P": 0.20,
            "tau_BR_P": 3.0,
            "C_switch": 1.0,
            "fitness_transform": "exp",
            "fitness_clip_min": 1e-12,
            "pi": 0.8,
        },
        "evolutionary": {"eta": 0.25, "omega_D": 0.8, "llm_lambda": 0.25},
        "active_pool": {
            "max_active": 5,
            "promote_every": 6,
            "q_new_init": 0.01,
            "demote_q_min": 0.05,
            "demote_patience": 4,
            "dc_max": 9.0,
            "dc_window": 5,
        },
        "llm": {
            "enabled": True,
            "ollama_host": "http://127.0.0.1:11434",
            "llm_macro_model": "llama3.2",
            "llm_summary_model": "olmo-3:7b-think",
            "llm_timeout_s": 60,
        },
    }


def make_scenarios(num_scenarios):
    scenarios = []
    for i in range(num_scenarios):
        scenarios.append(
            {
                "scenario_id": f"S{i + 1}",
                "path": "P1" if i % 2 == 0 else "P2",
                "states": ["S1", "S2"],
                "topology_template": f"T{(i % 3) + 1}",
                "asset_map": f"A{(i % 2) + 1}",
                "noise": round(0.02 * (i % 5), 3),
            }
        )
    return scenarios


def compute_comp_cont(sal, theta_xy, xi_z, thresholds):
    """
    Comp(k) = 1[SAL^k >= tau_SAL or theta_xy^k >= tau_theta]
    Cont(k) = 1[SAL^k <= tau_safe and xi_z^k <= tau_xi]
    """
    comp = 1 if (sal >= thresholds["tau_SAL"] or theta_xy >= thresholds["tau_theta"]) else 0
    cont = 1 if (sal <= thresholds["tau_safe"] and xi_z <= thresholds["tau_xi"]) else 0
    return comp, cont


def extract_metric(metrics, key):
    if key in metrics:
        return scalarize(metrics[key])
    for alias in METRIC_ALIASES.get(key, []):
        if alias in metrics:
            return scalarize(metrics[alias])
    raise RuntimeError(f"Missing metric '{key}' in env.step output")


def dist_to_dict(keys, values):
    return {str(k): float(v) for k, v in zip(keys, values)}


def parse_step_output(step_out):
    if isinstance(step_out, tuple) and len(step_out) >= 2:
        return step_out[1]
    if isinstance(step_out, dict):
        return step_out
    raise RuntimeError("env.step must return (obs, metrics) or a metrics dict")


def extract_compromised(metrics):
    if "compromised_assets" in metrics:
        return set(metrics["compromised_assets"])
    if "asset_compromise" in metrics and isinstance(metrics["asset_compromise"], dict):
        return {k for k, v in metrics["asset_compromise"].items() if v}
    return set()


def metrics_from_step_info(step_info, chosen_ga, chosen_gd, cfg, attackers_by_key, defenders_by_key):
    aux = step_info.get("aux", {})
    pair = aux.get("pair_details", {}).get(chosen_ga, {}).get(chosen_gd)
    if not pair:
        raise RuntimeError("Missing payoff details for chosen actions.")

    sal = float(pair["SAL"])
    sap = float(pair["SAP"])
    ac_total = float(pair["AC_total"])
    dc_total = float(pair["DC_total"])
    assc = float(pair["DC_components"]["ASSC"])
    nc = float(pair["DC_components"]["NC"])
    aic = float(pair["DC_components"]["AIC"])
    xi_total = float(pair["xi_total"])

    state_context = step_info.get("state_context", {})
    attacker = attackers_by_key[chosen_ga]
    defender = defenders_by_key[chosen_gd]
    params = apply_defender_to_target(attacker, defender, state_context)
    c_star = defender.effects.get("c_star", 1.0)

    betas = []
    thetas = []
    for attr in ["c", "i", "a"]:
        beta_val = beta_y(c_star, params["a_r"], params["pi"][attr])
        theta_val = theta_xy(params["lambdas"][attr], beta_val)
        betas.append(beta_val)
        thetas.append(theta_val)

    beta_mean = float(np.mean(betas))
    theta_mean = float(np.mean(thetas))

    gp = float(cfg["costs"]["beta_reg"]) * theta_mean
    r_alpha = (
        float(cfg["costs"]["third_party"]["alpha_I"]) if defender.sharing_enabled else 0.0
    )
    ua = sal - ac_total - gp
    ud = sap - dc_total - r_alpha

    metrics = {
        "SAL": sal,
        "SAP": sap,
        "UA": ua,
        "UD": ud,
        "xi_z": xi_total,
        "theta_xy": theta_mean,
        "beta_y": beta_mean,
        "AC": ac_total,
        "DC": dc_total,
        "ASSC": assc,
        "NC": nc,
        "AIC": aic,
        "Gp": gp,
        "R_alpha": r_alpha,
        "A_matrix": step_info.get("A"),
        "B_matrix": step_info.get("B"),
        "aux": aux,
        "xi_base": step_info.get("xi_base", xi_total),
        "xi_by_hop_base": step_info.get("xi_by_hop_base", pair.get("xi_by_hop", [])),
        "state_context": state_context,
    }
    return metrics


def get_attacker_state(attacker):
    s = getattr(attacker, "s", None)
    p_p1 = getattr(attacker, "p_P1", None)
    p_p2 = getattr(attacker, "p_P2", None)
    p_marg = None
    if hasattr(attacker, "marginal_p"):
        try:
            p_marg = attacker.marginal_p()
        except Exception:
            p_marg = None
    elif s is not None and p_p1 is not None and p_p2 is not None:
        p_marg = normalize_probs(s[0] * p_p1 + s[1] * p_p2)

    return {
        "s": [float(x) for x in s] if s is not None else None,
        "p_P1": [float(x) for x in p_p1] if p_p1 is not None else None,
        "p_P2": [float(x) for x in p_p2] if p_p2 is not None else None,
        "p_marg": [float(x) for x in p_marg] if p_marg is not None else None,
    }


def compute_state_payoff(env, path, state_index, defender_bar_q, attacker_policy):
    preview = env.preview_payoffs(path, state_index, defender_bar_q, attacker_policy)
    if not isinstance(preview, tuple) or len(preview) < 1:
        raise RuntimeError("env.preview_payoffs must return (A, B, aux)")
    A = np.array(preview[0], dtype=float)
    f_A = A @ np.array(defender_bar_q, dtype=float)
    return f_A


def choose_defender_action(defender, active_keys, bar_q_vec, rng):
    for name in ["select_action", "choose_action", "act"]:
        fn = getattr(defender, name, None)
        if callable(fn):
            try:
                return fn(bar_q_vec)
            except TypeError:
                return fn()
    gd_action = getattr(defender, "GD_action", None)
    if gd_action:
        return gd_action
    idx = int(rng.choice(len(bar_q_vec), p=bar_q_vec))
    return active_keys[idx]


class HospitalEdgeCloudEnvAdapter:
    def __init__(self, cfg, seed):
        if isinstance(seed, np.random.Generator):
            rng = seed
        else:
            rng = np.random.default_rng(int(seed))
        self.cfg = cfg
        self.env = HospitalEnv(cfg, rng)
        self.attackers = list(ATTACKERS)
        self.attackers_by_key = {a.key: a for a in self.attackers}
        self.defenders_by_key = {d.key: d for d in DEFENDERS}
        self.active_keys = list(INITIAL_ACTIVE)
        self.pool_keys = list(INITIAL_POOL)

    def set_active_keys(self, active_keys):
        self.active_keys = list(active_keys)

    def set_pool_keys(self, pool_keys):
        self.pool_keys = list(pool_keys)

    def reset(self, scenario):
        episode_id = 0
        if isinstance(scenario, dict):
            sid = scenario.get("episode") or scenario.get("scenario_id")
            if isinstance(sid, str) and sid.startswith("S"):
                sid = sid[1:]
            try:
                episode_id = int(sid)
            except Exception:
                episode_id = 0
        return self.env.reset_episode(episode_id)

    def preview_payoffs(self, path, state_index, defender_bar_q, attacker_policy):
        active_defenders = [self.defenders_by_key[k] for k in self.active_keys]
        return self.env.preview_payoffs(
            path,
            state_index,
            defender_bar_q,
            attacker_policy,
            active_defenders,
            self.attackers,
            build_payoff_matrices,
        )

    def step(self, attacker_action, defender_action, path, state_index):
        active_defenders = [self.defenders_by_key[k] for k in self.active_keys]
        step_info = self.env.step(
            path,
            state_index,
            active_defenders=active_defenders,
            attackers=self.attackers,
            payoff_builder=build_payoff_matrices,
        )
        metrics = metrics_from_step_info(
            step_info,
            attacker_action,
            defender_action,
            self.cfg,
            self.attackers_by_key,
            self.defenders_by_key,
        )
        return metrics


class DefenderLLMMTDAdapter:
    def __init__(self, cfg, ollama_client=None):
        self.cfg = cfg
        self.llm = LLMController(cfg)
        self.attackers = list(ATTACKERS)
        self.defenders_by_key = {d.key: d for d in DEFENDERS}
        self.A_k = list(INITIAL_ACTIVE)
        self.P_k = list(INITIAL_POOL)
        self.q = normalize_probs(np.ones(len(self.A_k)))
        self.sigma_LLM = np.array(self.q, dtype=float)
        self.bar_q = np.array(self.q, dtype=float)
        self.M_k = np.eye(len(self.A_k)).tolist()
        self.t_macro = 0.0
        self.t_mut = 0.0
        self.t_summary = 0.0
        self.low_q_streak = {k: 0 for k in self.A_k}
        self.dc_history = {
            k: deque(maxlen=cfg["active_pool"]["dc_window"]) for k in self.A_k
        }
        self.last_promo_episode = -cfg["active_pool"]["promote_every"]
        self.no_demotion_episodes = 0
        self.recent_promotions = deque(maxlen=3)
        self.recent_demotions = deque(maxlen=3)
        self.last_llm_suggestion = {"promote_key": "NONE", "demote_keys": []}
        self.fD_hist = []
        self.attacker_p = None
        self.episode_id = 0
        self.rng = np.random.default_rng(cfg["simulation"]["seed"] + 11)

    def set_attacker_p(self, p):
        self.attacker_p = np.array(p, dtype=float)

    def start_episode(self, episode_id, scenario=None):
        self.episode_id = episode_id
        self.fD_hist = []
        self.last_llm_suggestion = {"promote_key": "NONE", "demote_keys": []}

    def select_action(self, bar_q_vec):
        idx = int(self.rng.choice(len(bar_q_vec), p=bar_q_vec))
        return self.A_k[idx]

    def observe_step(self, metrics):
        if self.attacker_p is None:
            return
        B = metrics.get("B_matrix")
        aux = metrics.get("aux")
        if B is None or aux is None:
            return

        q_prev = np.array(self.q, dtype=float)
        active_keys = list(self.A_k)
        pool_keys = list(self.P_k)
        p_marg = np.array(self.attacker_p, dtype=float)

        summary = aux.get("summary", {})
        macro_context = {
            "active_keys": list(active_keys),
            "q": {k: float(q_prev[i]) for i, k in enumerate(active_keys)},
            "pool_keys": list(pool_keys),
            "p": {a.key: float(p_marg[i]) for i, a in enumerate(self.attackers)},
            "xi": float(metrics.get("xi_base", metrics.get("xi_z", 0.0))),
            "xi_by_hop": metrics.get("xi_by_hop_base", []),
            "sal_mean": summary.get("sal_mean", 0.0),
            "sap_mean": summary.get("sap_mean", 0.0),
            "dc_breakdown": summary.get("dc_mean", {}),
            "ac_breakdown": summary.get("ac_mean", {}),
            "recent_promotions": list(self.recent_promotions),
            "recent_demotions": list(self.recent_demotions),
            "constraints": {
                "max_active": self.cfg["active_pool"]["max_active"],
                "demote_q_min": self.cfg["active_pool"]["demote_q_min"],
                "demote_patience": self.cfg["active_pool"]["demote_patience"],
            },
        }

        llm_result = self.llm.macro_decision(macro_context, active_keys, pool_keys, q_prev)
        self.sigma_LLM = np.array(llm_result["sigma"], dtype=float)
        self.M_k = llm_result["mutation"]
        self.t_macro = llm_result["latency_s"]
        self.last_llm_suggestion = {
            "promote_key": llm_result.get("promote_key", "NONE"),
            "demote_keys": llm_result.get("demote_keys", []),
        }

        llm_lambda = self.cfg["evolutionary"]["llm_lambda"]
        self.bar_q = normalize_probs((1 - llm_lambda) * q_prev + llm_lambda * self.sigma_LLM)

        B = np.array(B, dtype=float)
        fD = B.T @ p_marg
        self.fD_hist.append(fD)

        q_next, _ = defender_update(
            q_prev,
            fD,
            self.cfg["evolutionary"]["eta"],
            self.cfg["evolutionary"]["omega_D"],
            llm_lambda,
            self.sigma_LLM,
            self.M_k,
        )
        self.q = q_next

        if "defender_dc" in aux:
            for def_key in active_keys:
                dc_by_attacker = aux["defender_dc"][def_key]["dc_by_attacker"]
                expected_dc = float(np.dot(p_marg, dc_by_attacker))
                history = self.dc_history.setdefault(
                    def_key, deque(maxlen=self.cfg["active_pool"]["dc_window"])
                )
                history.append(expected_dc)

    def end_episode(self, episode_info):
        llm_lambda = self.cfg["evolutionary"]["llm_lambda"]
        if self.fD_hist:
            fD_episode = np.mean(np.vstack(self.fD_hist), axis=0)
        else:
            fD_episode = np.zeros(len(self.A_k))

        demote_q_min = self.cfg["active_pool"]["demote_q_min"]
        for i, key in enumerate(self.A_k):
            if self.q[i] < demote_q_min:
                self.low_q_streak[key] = self.low_q_streak.get(key, 0) + 1
            else:
                self.low_q_streak[key] = 0

        (
            self.A_k,
            self.P_k,
            self.q,
            promoted_key,
            demoted_keys,
            self.low_q_streak,
            self.dc_history,
            self.last_promo_episode,
            self.no_demotion_episodes,
        ) = apply_active_pool_control(
            self.A_k,
            self.P_k,
            self.q,
            fD_episode,
            self.cfg,
            self.low_q_streak,
            self.dc_history,
            self.last_llm_suggestion,
            self.last_promo_episode,
            self.episode_id,
            self.no_demotion_episodes,
        )

        if promoted_key:
            self.recent_promotions.append(f"{promoted_key}@E{self.episode_id}")
        if demoted_keys:
            for key in demoted_keys:
                self.recent_demotions.append(f"{key}@E{self.episode_id}")

        if len(self.sigma_LLM) != len(self.q):
            self.sigma_LLM = np.array(self.q, dtype=float)
        self.bar_q = normalize_probs((1 - llm_lambda) * self.q + llm_lambda * self.sigma_LLM)


def get_defender_state(defender, config, method):
    if method == "Static":
        active_keys = ["GD1"]
        pool_keys = []
        q_vec = np.array([1.0], dtype=float)
        sigma_vec = np.array([1.0], dtype=float)
        bar_q_vec = np.array([1.0], dtype=float)
        M = np.eye(1)
        return {
            "active_keys": active_keys,
            "pool_keys": pool_keys,
            "q_vec": q_vec,
            "sigma_vec": sigma_vec,
            "bar_q_vec": bar_q_vec,
            "M": M,
            "t_macro": 0.0,
            "t_mut": 0.0,
            "t_summary": 0.0,
        }

    if not (
        hasattr(defender, "A_k")
        or hasattr(defender, "active_set")
        or hasattr(defender, "active_keys")
    ):
        name = defender.__class__.__name__
        raise RuntimeError(
            f"{name} does not expose active/pool state required for evaluation. "
            "Provide a DefenderLLMMTD-compatible wrapper."
        )

    active_keys = list(
        get_attr_any(defender, ["A_k", "active_set", "active_keys"], "Defender")
    )
    pool_keys = list(
        get_attr_any(defender, ["P_k", "pool_set", "pool_keys"], "Defender")
    )
    q = get_attr_any(defender, ["q", "q_k"], "Defender")
    sigma = get_attr_any(defender, ["sigma_LLM", "sigma_llm"], "Defender")
    bar_q = getattr(defender, "bar_q", None)
    M = getattr(defender, "M_k", None) or getattr(defender, "mutation", None)

    t_macro = float(getattr(defender, "t_macro", 0.0) or 0.0)
    t_mut = float(getattr(defender, "t_mut", 0.0) or 0.0)
    t_summary = float(getattr(defender, "t_summary", 0.0) or 0.0)

    q_vec = coerce_distribution(q, active_keys, "q")
    sigma_vec = coerce_distribution(sigma, active_keys, "sigma_LLM")
    llm_lambda = float(config.get("evolutionary", {}).get("llm_lambda", 0.0))
    if bar_q is None:
        bar_q_vec = normalize_probs((1 - llm_lambda) * q_vec + llm_lambda * sigma_vec)
    else:
        bar_q_vec = coerce_distribution(bar_q, active_keys, "bar_q")

    if M is None:
        M = np.eye(len(active_keys))
    else:
        M = np.array(M, dtype=float)

    if method == "Evo":
        bar_q_vec = q_vec.copy()
        sigma_vec = q_vec.copy()
        M = np.eye(len(active_keys))
    elif method == "LLM-Macro":
        M = np.eye(len(active_keys))
    elif method == "LLM-Mut":
        bar_q_vec = q_vec.copy()
        sigma_vec = q_vec.copy()

    return {
        "active_keys": active_keys,
        "pool_keys": pool_keys,
        "q_vec": q_vec,
        "sigma_vec": sigma_vec,
        "bar_q_vec": bar_q_vec,
        "M": M,
        "t_macro": t_macro,
        "t_mut": t_mut,
        "t_summary": t_summary,
    }


def run_single_trial(
    method,
    scenario,
    attacker_class,
    trial_id,
    base_config,
    horizon,
    delta,
    thresholds,
    seed,
    env_cls,
    defender_cls,
    attacker_cls,
):
    """Run a single (scenario, attacker class, trial) for a method."""
    np.random.seed(int(seed))
    rng = np.random.default_rng(int(seed))

    config = copy.deepcopy(base_config)
    config["simulation"]["seed"] = int(seed)
    config["simulation"]["episodes"] = int(horizon)
    config["attacker"]["pi"] = attacker_class["pi"]
    config["attacker"]["omega_A"] = attacker_class["omega_A"]
    config["attacker"]["rho_A"] = attacker_class["rho_A"]
    config["attacker"]["tau_BR_A"] = attacker_class["tau_BR_A"]
    config["attacker"]["C_switch"] = attacker_class["C_switch"]

    try:
        env = env_cls(config, int(seed))
    except TypeError:
        rng_local = np.random.default_rng(int(seed))
        env = env_cls(config, rng_local)

    try:
        defender = defender_cls(config, ollama_client=None)
    except TypeError:
        defender = defender_cls(config)

    try:
        attacker = attacker_cls(config)
    except TypeError:
        attacker = attacker_cls(config, rng)

    validate_env(env)
    validate_attacker(attacker)

    attackers = list(ATTACKERS)
    attackers_by_key = {a.key: a for a in attackers}
    defenders_by_key = {d.key: d for d in DEFENDERS}

    if callable(getattr(env, "reset", None)):
        env.reset(scenario)
    elif callable(getattr(env, "reset_episode", None)):
        env.reset_episode(0)
    call_optional(defender, ["reset", "reset_scenario"], scenario)

    episode_rows = []
    active_sizes = []
    attacker_policy_series = []
    coverage_approx_used = False

    critical_assets = set(scenario.get("critical_assets", DEFAULT_CRITICAL_ASSETS))

    for ep in range(1, horizon + 1):
        call_optional(attacker, ["start_episode", "begin_episode"], ep)
        call_optional(defender, ["start_episode", "begin_episode", "on_episode_start"], ep, scenario)

        def_state = get_defender_state(defender, config, method)
        active_keys = def_state["active_keys"]
        pool_keys = def_state["pool_keys"]
        q_vec = def_state["q_vec"]
        sigma_vec = def_state["sigma_vec"]
        bar_q_vec = def_state["bar_q_vec"]
        if hasattr(env, "set_active_keys"):
            env.set_active_keys(active_keys)
        if hasattr(env, "set_pool_keys"):
            env.set_pool_keys(pool_keys)

        g_p1 = g_p2 = 0.0
        path_debug = {}

        def env_preview(path, state_index, defender_bar_q, attacker_policy):
            try:
                return env.preview_payoffs(path, state_index, defender_bar_q, attacker_policy)
            except TypeError:
                active_defenders = [defenders_by_key[k] for k in active_keys]
                return env.preview_payoffs(
                    path,
                    state_index,
                    defender_bar_q,
                    attacker_policy,
                    active_defenders,
                    attackers,
                    build_payoff_matrices,
                )

        eval_result = attacker.evaluate_paths(ep, bar_q_vec, env_preview)
        if isinstance(eval_result, tuple) and len(eval_result) == 3:
            g_p1, g_p2, path_debug = eval_result
        elif isinstance(eval_result, tuple) and len(eval_result) == 2:
            g_p1, g_p2 = eval_result
        else:
            raise RuntimeError("evaluate_paths must return (g_P1, g_P2, debug)")

        path_choice = attacker.choose_path(ep, g_p1, g_p2)
        if isinstance(path_choice, tuple):
            chosen_path = path_choice[0]
            path_choice_debug = path_choice[1] if len(path_choice) > 1 else {}
        else:
            chosen_path = path_choice
            path_choice_debug = {}

        p_p1 = getattr(attacker, "p_P1", None)
        p_p2 = getattr(attacker, "p_P2", None)

        f_p1_s1 = compute_state_payoff(env, "P1", 1, bar_q_vec, p_p1)
        f_p1_s2 = compute_state_payoff(env, "P1", 2, bar_q_vec, p_p1)
        f_p2_s1 = compute_state_payoff(env, "P2", 1, bar_q_vec, p_p2)
        f_p2_s2 = compute_state_payoff(env, "P2", 2, bar_q_vec, p_p2)
        f_p1 = 0.5 * (f_p1_s1 + f_p1_s2)
        f_p2 = 0.5 * (f_p2_s1 + f_p2_s2)

        sal_list = []
        sap_list = []
        ua_list = []
        ud_list = []
        xi_list = []
        theta_list = []
        beta_list = []
        ac_list = []
        dc_list = []
        assc_list = []
        nc_list = []
        aic_list = []
        gp_list = []
        ralpha_list = []

        episode_compromised = set()
        chosen_ga_last = ""
        chosen_gd_last = ""

        for state_index in [1, 2]:
            p_marg = getattr(attacker, "marginal_p", lambda: None)()
            if p_marg is not None:
                if hasattr(defender, "set_attacker_p"):
                    defender.set_attacker_p(p_marg)
                else:
                    setattr(defender, "attacker_p", p_marg)
            f_state = compute_state_payoff(
                env,
                chosen_path,
                state_index,
                bar_q_vec,
                p_p1 if chosen_path == "P1" else p_p2,
            )

            tactic_choice = attacker.choose_tactic(ep, chosen_path, f_state)
            if isinstance(tactic_choice, tuple):
                chosen_ga = tactic_choice[0]
            else:
                chosen_ga = tactic_choice

            if method == "Static":
                chosen_gd = active_keys[0]
            else:
                chosen_gd = choose_defender_action(defender, active_keys, bar_q_vec, rng)

            step_out = env.step(chosen_ga, chosen_gd, chosen_path, state_index)
            if isinstance(step_out, dict) and "aux" in step_out and "A" in step_out:
                metrics = metrics_from_step_info(
                    step_out,
                    chosen_ga,
                    chosen_gd,
                    config,
                    attackers_by_key,
                    defenders_by_key,
                )
            else:
                metrics = parse_step_output(step_out)

            sal_list.append(extract_metric(metrics, "SAL"))
            sap_list.append(extract_metric(metrics, "SAP"))
            ua_list.append(extract_metric(metrics, "UA"))
            ud_list.append(extract_metric(metrics, "UD"))
            xi_list.append(extract_metric(metrics, "xi_z"))
            theta_list.append(extract_metric(metrics, "theta_xy"))
            beta_list.append(extract_metric(metrics, "beta_y"))
            ac_list.append(extract_metric(metrics, "AC"))
            dc_list.append(extract_metric(metrics, "DC"))
            assc_list.append(extract_metric(metrics, "ASSC"))
            nc_list.append(extract_metric(metrics, "NC"))
            aic_list.append(extract_metric(metrics, "AIC"))
            gp_list.append(extract_metric(metrics, "Gp"))
            ralpha_list.append(extract_metric(metrics, "R_alpha"))

            episode_compromised.update(extract_compromised(metrics))
            chosen_ga_last = chosen_ga
            chosen_gd_last = chosen_gd

            call_optional(defender, ["observe_step", "update_step", "step"], metrics)
            if state_index < 2:
                def_state = get_defender_state(defender, config, method)
                active_keys = def_state["active_keys"]
                pool_keys = def_state["pool_keys"]
                q_vec = def_state["q_vec"]
                sigma_vec = def_state["sigma_vec"]
                bar_q_vec = def_state["bar_q_vec"]
                if hasattr(env, "set_active_keys"):
                    env.set_active_keys(active_keys)
                if hasattr(env, "set_pool_keys"):
                    env.set_pool_keys(pool_keys)

        sal_k = float(np.mean(sal_list)) if sal_list else float("nan")
        sap_k = float(np.mean(sap_list)) if sap_list else float("nan")
        ua_k = float(np.mean(ua_list)) if ua_list else float("nan")
        ud_k = float(np.mean(ud_list)) if ud_list else float("nan")
        xi_k = float(np.mean(xi_list)) if xi_list else float("nan")
        theta_k = float(np.mean(theta_list)) if theta_list else float("nan")
        beta_k = float(np.mean(beta_list)) if beta_list else float("nan")
        ac_k = float(np.mean(ac_list)) if ac_list else float("nan")
        dc_k = float(np.mean(dc_list)) if dc_list else float("nan")
        assc_k = float(np.mean(assc_list)) if assc_list else float("nan")
        nc_k = float(np.mean(nc_list)) if nc_list else float("nan")
        aic_k = float(np.mean(aic_list)) if aic_list else float("nan")
        gp_k = float(np.mean(gp_list)) if gp_list else float("nan")
        ralpha_k = float(np.mean(ralpha_list)) if ralpha_list else float("nan")

        comp, cont = compute_comp_cont(sal_k, theta_k, xi_k, thresholds)

        if critical_assets and episode_compromised:
            coverage = 1.0 - len(episode_compromised & critical_assets) / float(
                len(critical_assets)
            )
            coverage_approx = False
        elif critical_assets:
            coverage = 1.0 if comp == 0 else 0.0
            coverage_approx = True
        else:
            coverage = float("nan")
            coverage_approx = True

        if coverage_approx:
            coverage_approx_used = True

        attacker_state = get_attacker_state(attacker)
        attacker_state.update(
            {
                "g_P1": float(g_p1),
                "g_P2": float(g_p2),
                "BRpath": path_choice_debug.get("BRpath"),
                "piPath": path_choice_debug.get("piPath"),
            }
        )

        row = {
            "method": method,
            "scenario_id": scenario["scenario_id"],
            "attacker_class_id": attacker_class["id"],
            "trial_id": trial_id,
            "episode_k": ep,
            "path": chosen_path,
            "state_index": to_json([1, 2]),
            "active_size": len(active_keys),
            "pool_size": len(pool_keys),
            "active_set_json": to_json(active_keys),
            "pool_set_json": to_json(pool_keys),
            "q_json": to_json(dist_to_dict(active_keys, q_vec)),
            "sigma_llm_json": to_json(dist_to_dict(active_keys, sigma_vec)),
            "bar_q_json": to_json(dist_to_dict(active_keys, bar_q_vec)),
            "chosen_GD": chosen_gd_last,
            "chosen_GA": chosen_ga_last,
            "SAL": sal_k,
            "SAP": sap_k,
            "UA": ua_k,
            "UD": ud_k,
            "xi_z": xi_k,
            "theta_xy": theta_k,
            "beta_y": beta_k,
            "AC": ac_k,
            "DC": dc_k,
            "ASSC": assc_k,
            "NC": nc_k,
            "AIC": aic_k,
            "Gp": gp_k,
            "R_alpha": ralpha_k,
            "t_macro": def_state["t_macro"],
            "t_mut": def_state["t_mut"],
            "t_summary": def_state["t_summary"],
            "Comp": comp,
            "Cont": cont,
            "coverage": coverage,
            "compromised_assets_json": to_json(sorted(episode_compromised)),
            "attacker_state_json": to_json(attacker_state),
        }

        episode_rows.append(row)

        episode_info = {
            "episode": ep,
            "path": chosen_path,
            "metrics": {
                "SAL": sal_k,
                "SAP": sap_k,
                "UA": ua_k,
                "UD": ud_k,
                "xi_z": xi_k,
                "theta_xy": theta_k,
                "beta_y": beta_k,
                "AC": ac_k,
                "DC": dc_k,
                "ASSC": assc_k,
                "NC": nc_k,
                "AIC": aic_k,
                "Gp": gp_k,
                "R_alpha": ralpha_k,
            },
            "chosen_GD": chosen_gd_last,
            "chosen_GA": chosen_ga_last,
            "active_keys": active_keys,
            "pool_keys": pool_keys,
        }
        call_optional(defender, ["end_episode", "update_episode", "finalize_episode"], episode_info)

        row["t_macro"] = float(getattr(defender, "t_macro", row["t_macro"]))
        row["t_mut"] = float(getattr(defender, "t_mut", row["t_mut"]))
        row["t_summary"] = float(getattr(defender, "t_summary", row["t_summary"]))

        attacker.update_tactics(ep, f_p1, f_p2)
        attacker.update_paths(ep, g_p1, g_p2)

        active_sizes.append(len(active_keys))
        if attacker_state.get("p_marg") is not None:
            attacker_policy_series.append(np.array(attacker_state["p_marg"], dtype=float))

    if len(attacker_policy_series) > 1:
        var = np.var(np.vstack(attacker_policy_series), axis=0)
        if float(np.max(var)) < 1e-6:
            print("warning: attacker policy variance < 1e-6 -- evolution may stall.")

    if len(active_sizes) > 1:
        nondecreasing = all(x <= y for x, y in zip(active_sizes, active_sizes[1:]))
        if nondecreasing and any(x < y for x, y in zip(active_sizes, active_sizes[1:])):
            print("warning: active size never decreases -- demotion may be missing.")

    run_row = compute_run_metrics(
        episode_rows,
        horizon,
        delta,
        thresholds,
        critical_assets,
        method,
        scenario["scenario_id"],
        attacker_class["id"],
        trial_id,
    )

    return episode_rows, run_row, coverage_approx_used


def compute_run_metrics(
    episode_rows,
    horizon,
    delta,
    thresholds,
    critical_assets,
    method,
    scenario_id,
    attacker_class_id,
    trial_id,
):
    """
    Compute run-level metrics.

    Comp(k) = 1[SAL^k >= tau_SAL or theta_xy^k >= tau_theta]
    Cont(k) = 1[SAL^k <= tau_safe and xi_z^k <= tau_xi]
    T_cont = min{k: Cont(k)=1}, else +inf
    S_success = 1[sum_{k=1}^{T_cont} Comp(k)=0 and T_cont <= K]
    SAL_cum = sum_{k=1}^K delta^{k-1} SAL^k
    SAP_cum = sum_{k=1}^K delta^{k-1} SAP^k
    """
    K = len(episode_rows)
    sal = np.array([float(row.get("SAL", 0.0)) for row in episode_rows], dtype=float)
    sap = np.array([float(row.get("SAP", 0.0)) for row in episode_rows], dtype=float)
    comp = np.array([int(row.get("Comp", 0)) for row in episode_rows], dtype=int)
    cont = np.array([int(row.get("Cont", 0)) for row in episode_rows], dtype=int)

    t_cont = math.inf
    for idx, flag in enumerate(cont, start=1):
        if flag == 1:
            t_cont = idx
            break

    if t_cont <= K and int(np.sum(comp[: int(t_cont)])) == 0:
        s_success = 1
    else:
        s_success = 0

    discounts = np.power(delta, np.arange(K))
    sal_cum = float(np.sum(discounts * sal))
    sap_cum = float(np.sum(discounts * sap))

    dc_vals = np.array([float(row.get("DC", 0.0)) for row in episode_rows], dtype=float)
    assc_vals = np.array([float(row.get("ASSC", 0.0)) for row in episode_rows], dtype=float)
    nc_vals = np.array([float(row.get("NC", 0.0)) for row in episode_rows], dtype=float)
    aic_vals = np.array([float(row.get("AIC", 0.0)) for row in episode_rows], dtype=float)
    ac_vals = np.array([float(row.get("AC", 0.0)) for row in episode_rows], dtype=float)

    dc_cum = float(np.sum(dc_vals))
    assc_cum = float(np.sum(assc_vals))
    nc_cum = float(np.sum(nc_vals))
    aic_cum = float(np.sum(aic_vals))
    ac_cum = float(np.sum(ac_vals))

    lat_total = [
        float(row.get("t_macro", 0.0))
        + float(row.get("t_mut", 0.0))
        + float(row.get("t_summary", 0.0))
        for row in episode_rows
    ]
    lat_total_arr = np.array(lat_total, dtype=float) if lat_total else np.array([0.0])
    t_llm_mean = float(np.mean(lat_total_arr))
    t_llm_p90 = float(np.percentile(lat_total_arr, 90))
    t_llm_max = float(np.max(lat_total_arr))

    active_sets = [set(parse_json(row.get("active_set_json"), [])) for row in episode_rows]
    prom = 0
    demo = 0
    churn_vals = []
    for i in range(len(active_sets) - 1):
        current = active_sets[i]
        nxt = active_sets[i + 1]
        prom += len(nxt - current)
        demo += len(current - nxt)
        union = current | nxt
        inter = current & nxt
        jacc = float(len(inter)) / float(len(union)) if union else 1.0
        churn_vals.append(1.0 - jacc)

    churn_mean = float(np.mean(churn_vals)) if churn_vals else 0.0

    entropies = []
    delta_q_vals = []
    prev_bar = None
    for row in episode_rows:
        bar_q = parse_json(row.get("bar_q_json"), {})
        if bar_q:
            probs = normalize_probs(list(bar_q.values()))
            ent = -np.sum(probs * np.log(np.clip(probs, 1e-12, 1.0)))
            entropies.append(float(ent))
        if prev_bar is not None:
            keys = set(prev_bar.keys()) | set(bar_q.keys())
            vec = np.array([bar_q.get(k, 0.0) for k in keys], dtype=float)
            prev = np.array([prev_bar.get(k, 0.0) for k in keys], dtype=float)
            delta_q_vals.append(float(np.sum(np.abs(vec - prev))))
        prev_bar = bar_q

    entropy_mean = float(np.mean(entropies)) if entropies else 0.0
    delta_q_mean = float(np.mean(delta_q_vals)) if delta_q_vals else 0.0

    comp_union = set()
    coverage_vals = []
    for row in episode_rows:
        comp_assets = parse_json(row.get("compromised_assets_json"), [])
        if comp_assets:
            comp_union |= set(comp_assets)
        if "coverage" in row:
            coverage_vals.append(float(row.get("coverage", 0.0)))

    if critical_assets and comp_union:
        coverage = 1.0 - len(comp_union & set(critical_assets)) / float(len(critical_assets))
    elif coverage_vals:
        coverage = float(np.mean(coverage_vals))
    else:
        coverage = float("nan")

    coverage_ge_0_5 = 1 if (not math.isnan(coverage) and coverage >= 0.5) else 0

    def topk_success(k):
        if t_cont > K:
            return 0
        for idx, row in enumerate(episode_rows, start=1):
            if idx > t_cont:
                break
            bar_q = parse_json(row.get("bar_q_json"), {})
            if not bar_q:
                continue
            topk = sorted(bar_q.items(), key=lambda x: x[1], reverse=True)[:k]
            if row.get("chosen_GD") in {k for k, _ in topk}:
                return 1
        return 0

    return {
        "method": method,
        "scenario_id": scenario_id,
        "attacker_class_id": attacker_class_id,
        "trial_id": trial_id,
        "S_success": s_success,
        "T_cont": t_cont,
        "SAL_cum": sal_cum,
        "SAP_cum": sap_cum,
        "DC_cum": dc_cum,
        "ASSC_cum": assc_cum,
        "NC_cum": nc_cum,
        "AIC_cum": aic_cum,
        "AC_cum": ac_cum,
        "N_prom": prom,
        "N_demo": demo,
        "churn_mean": churn_mean,
        "entropy_mean": entropy_mean,
        "delta_q_mean": delta_q_mean,
        "T_LLM_mean": t_llm_mean,
        "T_LLM_p90": t_llm_p90,
        "T_LLM_max": t_llm_max,
        "Coverage": coverage,
        "Coverage_ge_0_5": coverage_ge_0_5,
        "Top1_success": topk_success(1),
        "Top3_success": topk_success(3),
        "Top5_success": topk_success(5),
    }


def aggregate_summaries(runs_df, horizon):
    summary_rows = []
    sr_wc_by_method = (
        runs_df.groupby(["method", "scenario_id", "trial_id"])["S_success"]
        .min()
        .groupby("method")
        .mean()
    )
    sal_wc_by_method = (
        runs_df.groupby(["method", "scenario_id", "trial_id"])["SAL_cum"]
        .max()
        .groupby("method")
        .mean()
    )
    for method in sorted(runs_df["method"].unique()):
        group = runs_df[runs_df["method"] == method]
        if group.empty:
            continue

        sr = float(group["S_success"].mean())
        ncr = float(np.mean(group["T_cont"] > horizon))
        ttc_vals = group[group["T_cont"] <= horizon]["T_cont"]
        ttc_mean = float(ttc_vals.mean()) if not ttc_vals.empty else float("inf")

        rel = group.groupby(["scenario_id", "attacker_class_id"])["S_success"].mean()
        pr = float((rel == 1.0).mean()) if not rel.empty else 0.0

        sr_wc = float(sr_wc_by_method.get(method, float("nan")))
        sal_wc = float(sal_wc_by_method.get(method, float("nan")))

        summary_rows.append(
            {
                "method": method,
                "SR": sr,
                "SR_wc": sr_wc,
                "SAL_cum_mean": float(group["SAL_cum"].mean()),
                "SAL_cum_median": float(group["SAL_cum"].median()),
                "SAL_wc_cum": sal_wc,
                "TTC_mean": ttc_mean,
                "NCR": ncr,
                "PR": pr,
                "Coverage_mean": float(group["Coverage"].mean()),
                "Cov_ge_0_5": float(group["Coverage_ge_0_5"].mean()),
                "DC_cum_mean": float(group["DC_cum"].mean()),
                "ASSC_cum_mean": float(group["ASSC_cum"].mean()),
                "NC_cum_mean": float(group["NC_cum"].mean()),
                "AIC_cum_mean": float(group["AIC_cum"].mean()),
                "AC_cum_mean": float(group["AC_cum"].mean()),
                "N_prom_mean": float(group["N_prom"].mean()),
                "N_demo_mean": float(group["N_demo"].mean()),
                "churn_mean": float(group["churn_mean"].mean()),
                "T_LLM_mean": float(group["T_LLM_mean"].mean()),
                "T_LLM_p90": float(group["T_LLM_p90"].mean()),
                "T_LLM_max": float(group["T_LLM_max"].mean()),
            }
        )

    summary_df = pd.DataFrame(summary_rows)

    scenario_rows = []
    for (method, scenario_id), group in runs_df.groupby(["method", "scenario_id"]):
        scenario_rows.append(
            {
                "method": method,
                "scenario_id": scenario_id,
                "SR": float(group["S_success"].mean()),
                "SAL_cum_mean": float(group["SAL_cum"].mean()),
                "SAP_cum_mean": float(group["SAP_cum"].mean()),
                "TTC_mean": float(group[group["T_cont"] <= horizon]["T_cont"].mean())
                if not group.empty
                else float("inf"),
                "NCR": float(np.mean(group["T_cont"] > horizon)),
                "Coverage_mean": float(group["Coverage"].mean()),
                "DC_cum_mean": float(group["DC_cum"].mean()),
                "AC_cum_mean": float(group["AC_cum"].mean()),
            }
        )

    summary_by_scenario_df = pd.DataFrame(scenario_rows)
    return summary_df, summary_by_scenario_df


def plot_active_pool_size(episodes_df, out_path):
    if episodes_df.empty:
        return
    grouped = episodes_df.groupby("episode_k")[["active_size", "pool_size"]].mean()
    plt.figure(figsize=(6, 4))
    plt.plot(grouped.index, grouped["active_size"], label="active")
    plt.plot(grouped.index, grouped["pool_size"], label="pool")
    plt.xlabel("episode")
    plt.ylabel("size")
    plt.title("Active and pool size")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_defender_barq_trends(episodes_df, out_path):
    if episodes_df.empty:
        return
    records = []
    for _, row in episodes_df.iterrows():
        bar_q = parse_json(row.get("bar_q_json"), {})
        for key, val in bar_q.items():
            records.append({"episode_k": row["episode_k"], "key": key, "val": val})
    if not records:
        return
    df = pd.DataFrame(records)
    pivot = df.pivot_table(index="episode_k", columns="key", values="val", aggfunc="mean")
    pivot = pivot.fillna(0.0)
    plt.figure(figsize=(7, 4))
    for col in pivot.columns:
        plt.plot(pivot.index, pivot[col], label=col)
    plt.xlabel("episode")
    plt.ylabel("bar_q")
    plt.title("Defender bar_q trends")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_attacker_strategy_trends(episodes_df, out_path):
    if episodes_df.empty:
        return
    records = []
    for _, row in episodes_df.iterrows():
        state = parse_json(row.get("attacker_state_json"), {})
        p_marg = state.get("p_marg")
        s = state.get("s")
        if p_marg is not None:
            for i, val in enumerate(p_marg):
                records.append({"episode_k": row["episode_k"], "key": f"p_GA{i+1}", "val": val})
        if s is not None:
            records.append({"episode_k": row["episode_k"], "key": "s_P1", "val": s[0]})
            records.append({"episode_k": row["episode_k"], "key": "s_P2", "val": s[1]})
    if not records:
        return
    df = pd.DataFrame(records)
    pivot = df.pivot_table(index="episode_k", columns="key", values="val", aggfunc="mean")
    pivot = pivot.fillna(0.0)
    plt.figure(figsize=(7, 4))
    for col in pivot.columns:
        plt.plot(pivot.index, pivot[col], label=col)
    plt.xlabel("episode")
    plt.ylabel("probability")
    plt.title("Attacker strategy trends")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_security_payoff(episodes_df, out_path):
    if episodes_df.empty:
        return
    grouped = episodes_df.groupby("episode_k")[["SAL", "SAP", "UA", "UD"]]
    mean_df = grouped.mean()
    std_df = grouped.std()
    plt.figure(figsize=(7, 4))
    for col in mean_df.columns:
        plt.plot(mean_df.index, mean_df[col], label=col)
        plt.fill_between(
            mean_df.index,
            mean_df[col] - std_df[col],
            mean_df[col] + std_df[col],
            alpha=0.2,
        )
    plt.xlabel("episode")
    plt.ylabel("value")
    plt.title("Security payoffs")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_defender_costs(episodes_df, out_path):
    if episodes_df.empty:
        return
    grouped = episodes_df.groupby("episode_k")[["ASSC", "NC", "AIC", "DC"]].mean()
    plt.figure(figsize=(7, 4))
    for col in grouped.columns:
        plt.plot(grouped.index, grouped[col], label=col)
    plt.xlabel("episode")
    plt.ylabel("cost")
    plt.title("Defender costs")
    plt.legend(ncol=2, fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_attacker_costs(episodes_df, out_path):
    if episodes_df.empty:
        return
    grouped = episodes_df.groupby("episode_k")[["AC"]].mean()
    plt.figure(figsize=(6, 4))
    plt.plot(grouped.index, grouped["AC"], label="AC")
    plt.xlabel("episode")
    plt.ylabel("cost")
    plt.title("Attacker costs")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_llm_latency(episodes_df, out_path):
    if episodes_df.empty:
        return
    grouped = episodes_df.groupby("episode_k")[["t_macro", "t_mut", "t_summary"]].mean()
    total = (
        episodes_df["t_macro"].fillna(0)
        + episodes_df["t_mut"].fillna(0)
        + episodes_df["t_summary"].fillna(0)
    )
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    for col in grouped.columns:
        axes[0].plot(grouped.index, grouped[col], label=col)
    axes[0].set_xlabel("episode")
    axes[0].set_ylabel("seconds")
    axes[0].set_title("LLM latency by episode")
    axes[0].legend(fontsize=8)
    axes[1].hist(total, bins=10, color="gray", edgecolor="black")
    axes[1].set_xlabel("total latency")
    axes[1].set_ylabel("count")
    axes[1].set_title("Latency histogram")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_robustness_boxplots(runs_df, out_path):
    if runs_df.empty:
        return
    attacker_ids = sorted(runs_df["attacker_class_id"].unique())
    data = [runs_df[runs_df["attacker_class_id"] == aid]["SAL_cum"].values for aid in attacker_ids]
    if not data:
        return
    plt.figure(figsize=(7, 4))
    plt.boxplot(data, labels=attacker_ids)
    wc = runs_df.groupby(["scenario_id", "trial_id"])["SAL_cum"].max().mean()
    plt.axhline(wc, color="red", linestyle="--", label="worst-case mean")
    plt.xlabel("attacker class")
    plt.ylabel("SAL_cum")
    plt.title("Robustness boxplots")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_success_rate_bars(summary_df, out_path):
    if summary_df.empty:
        return
    plt.figure(figsize=(7, 4))
    x = np.arange(len(summary_df))
    plt.bar(x - 0.15, summary_df["SR"], width=0.3, label="SR")
    plt.bar(x + 0.15, summary_df["SR_wc"], width=0.3, label="SR_wc")
    plt.xticks(x, summary_df["method"], rotation=20)
    plt.ylabel("rate")
    plt.title("Success rates")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_coverage_bars(summary_df, out_path):
    if summary_df.empty:
        return
    plt.figure(figsize=(7, 4))
    x = np.arange(len(summary_df))
    plt.bar(x - 0.15, summary_df["Coverage_mean"], width=0.3, label="Coverage")
    plt.bar(x + 0.15, summary_df["Cov_ge_0_5"], width=0.3, label="Cov>=0.5")
    plt.xticks(x, summary_df["method"], rotation=20)
    plt.ylabel("rate")
    plt.title("Coverage")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


def plot_method_figs(method, episodes_df, runs_df, figures_dir):
    method_dir = Path(figures_dir) / method
    ensure_dir(method_dir)
    plot_active_pool_size(episodes_df, method_dir / "active_pool_size.png")
    plot_defender_barq_trends(episodes_df, method_dir / "defender_barq_trends.png")
    plot_attacker_strategy_trends(episodes_df, method_dir / "attacker_strategy_trends.png")
    plot_security_payoff(episodes_df, method_dir / "security_payoff.png")
    plot_defender_costs(episodes_df, method_dir / "defender_costs.png")
    plot_attacker_costs(episodes_df, method_dir / "attacker_costs.png")
    plot_llm_latency(episodes_df, method_dir / "llm_latency.png")
    plot_robustness_boxplots(runs_df, method_dir / "robustness_boxplots.png")


def run_evaluation(
    methods=None,
    num_scenarios=DEFAULT_NUM_SCENARIOS,
    num_trials=DEFAULT_NUM_TRIALS,
    horizon=DEFAULT_HORIZON,
    output_dir="results",
    seed=DEFAULT_SEED,
    delta=DEFAULT_DELTA,
    thresholds=None,
):
    """
    Run the full evaluation loop.

    For each method, scenario, attacker class, and trial:
    simulate K episodes and log episode-level and run-level metrics.
    """
    if thresholds is None:
        thresholds = dict(DEFAULT_THRESHOLDS)

    methods = methods or list(DEFAULT_METHODS)
    output_dir = Path(output_dir)
    figures_dir = output_dir / "figures"
    ensure_dir(output_dir)
    ensure_dir(figures_dir)

    repo_root = Path(__file__).resolve().parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    try:
        env_cls = load_component("src.system.hospital_env", "HospitalEdgeCloudEnv")
    except RuntimeError:
        env_cls = HospitalEdgeCloudEnvAdapter
    try:
        defender_cls = load_component("src.llm.controller", "DefenderLLMMTD")
    except RuntimeError:
        defender_cls = DefenderLLMMTDAdapter
    try:
        attacker_cls = load_component("src.game.attacker_controller", "AttackerController")
    except RuntimeError:
        attacker_cls = AttackerController

    base_config = build_base_config(seed, horizon)
    scenarios = make_scenarios(num_scenarios)
    attacker_classes = list(ATTACKER_CLASSES)

    all_run_rows = []
    coverage_approx_used = False

    for method in methods:
        method_episode_rows = []
        method_run_rows = []
        for scenario in scenarios:
            for attacker_class in attacker_classes:
                for trial_id in range(1, num_trials + 1):
                    trial_seed = derive_seed(seed, method, scenario["scenario_id"], attacker_class["id"], trial_id)
                    episode_rows, run_row, approx_used = run_single_trial(
                        method,
                        scenario,
                        attacker_class,
                        trial_id,
                        base_config,
                        horizon,
                        delta,
                        thresholds,
                        trial_seed,
                        env_cls,
                        defender_cls,
                        attacker_cls,
                    )
                    method_episode_rows.extend(episode_rows)
                    method_run_rows.append(run_row)
                    all_run_rows.append(run_row)
                    if approx_used:
                        coverage_approx_used = True

        episodes_df = pd.DataFrame(method_episode_rows)
        runs_df = pd.DataFrame(method_run_rows)
        episodes_df.to_csv(output_dir / f"episodes_{method}.csv", index=False)
        runs_df.to_csv(output_dir / f"runs_{method}.csv", index=False)

        plot_method_figs(method, episodes_df, runs_df, figures_dir)

    runs_df_all = pd.DataFrame(all_run_rows)
    summary_df, summary_by_scenario_df = aggregate_summaries(runs_df_all, horizon)
    summary_df.to_csv(output_dir / "summary.csv", index=False)
    summary_by_scenario_df.to_csv(output_dir / "summary_by_scenario.csv", index=False)

    plot_success_rate_bars(summary_df, figures_dir / "success_rate_bars.png")
    plot_coverage_bars(summary_df, figures_dir / "coverage_bars.png")

    metadata = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "git_hash": get_git_hash(repo_root),
        "methods": methods,
        "delta": delta,
        "thresholds": thresholds,
        "config": base_config,
        "num_scenarios": num_scenarios,
        "num_trials": num_trials,
        "horizon": horizon,
        "attacker_classes": attacker_classes,
        "coverage_approximation_used": coverage_approx_used,
    }
    (output_dir / "metadata.json").write_text(to_json(metadata), encoding="utf-8")

    return {
        "summary": summary_df,
        "summary_by_scenario": summary_by_scenario_df,
        "runs": runs_df_all,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate LLM-MTD defender variants.")
    parser.add_argument("--methods", default=",".join(DEFAULT_METHODS))
    parser.add_argument("--num_scenarios", type=int, default=DEFAULT_NUM_SCENARIOS)
    parser.add_argument("--num_trials", type=int, default=DEFAULT_NUM_TRIALS)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]

    run_evaluation(
        methods=methods,
        num_scenarios=args.num_scenarios,
        num_trials=args.num_trials,
        horizon=args.horizon,
        output_dir=args.output_dir,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
