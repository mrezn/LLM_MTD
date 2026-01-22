import numpy as np

from .strategies import AttackerStrategy
from ..system.metrics import compute_xi


def beta_y(c_star, a_r, pi):
    val = c_star * a_r * pi
    return (1 - np.exp(-val)) / (1 + np.exp(-val))


def theta_xy(lambda_x, beta):
    return 1 - lambda_x * beta


def _theta_values(attacker, defender, lambdas, a_r):
    c_star = defender.effects.get("c_star", 1.0)
    thetas = {}
    for attr in ["c", "i", "a"]:
        beta = beta_y(c_star, a_r, attacker.pi[attr])
        thetas[attr] = theta_xy(lambdas[attr], beta)
    return thetas


def SAL(attacker, defender, xi, C_r_target, R_cia_target, lambdas_target, a_r):
    thetas = _theta_values(attacker, defender, lambdas_target, a_r)
    total = sum(
        thetas[attr] * attacker.W_cia[attr] * R_cia_target[attr]
        for attr in ["c", "i", "a"]
    )
    return (1 + xi) * C_r_target * total


def SAP(attacker, defender, xi, C_r_target, R_cia_target, lambdas_target, a_r, cfg):
    thetas = _theta_values(attacker, defender, lambdas_target, a_r)
    mu_y = cfg["costs"]["mu_y"]
    total = 0.0
    for attr in ["c", "i", "a"]:
        term = (mu_y * thetas[attr] + (1 - thetas[attr]))
        term *= (1 - attacker.W_cia[attr]) * R_cia_target[attr]
        total += term
    return (1 - xi) * C_r_target * total


def AC(attacker, defender, cfg):
    features = dict(attacker.base_cost_features)
    if defender.key == "GD6" and attacker.key in {"GA1", "GA3"}:
        features["T"] += 1
    if defender.key == "GD7":
        if attacker.key == "GA3":
            features["T"] += 2
            features["K"] += 2
        if attacker.key == "GA1":
            features["K"] += 1

    w = cfg["costs"]["attacker"]
    total = (
        w["w_T"] * features["T"]
        + w["w_H"] * features["H"]
        + w["w_K"] * features["K"]
        + w["w_R"] * features["R"]
        + w["w_D"] * features["D"]
    )
    return total, features


def DC(defender, cfg, a_r):
    alpha_ass = cfg["costs"]["defender"]["alpha_ass"]
    SQ = cfg["costs"]["defender"]["SQ"]
    k_s = cfg["costs"]["defender"]["k_s"]
    alpha_aic = cfg["costs"]["defender"]["alpha_aic"]

    l_size = max(1, len(defender.layer_scope))
    c_star = defender.effects.get("c_star", 1.0)

    ASSC = alpha_ass * c_star * a_r * l_size
    NC = SQ * (1 - 1 / (1 + np.exp(-(a_r - k_s))))

    sigma = defender.effects.get("sigma", 0.0)
    delta = defender.effects.get("decoy_delta", 0.0)
    varphi = defender.effects.get("rate_limit_varphi", 0.0)

    AIC = alpha_aic * (0.4 * sigma + 0.4 * delta + 0.2 * varphi) * l_size

    if defender.key == "GD8":
        NC += defender.effects.get("nc_boost", 1.0)

    total = ASSC + NC + AIC
    return total, ASSC, NC, AIC


def apply_defender_to_topology(defender, C, nodes):
    C_mod = np.array(C, copy=True)
    if defender.key in {"GD1", "GD2"}:
        factor = defender.effects.get("ingress_factor", 1.0)
        for i, src in enumerate(nodes):
            for j, dst in enumerate(nodes):
                if src["layer"] == "sensor" and (
                    dst["layer"] == "edge" or dst["name"] == "cloud_api"
                ):
                    C_mod[i, j] *= factor

    if defender.key == "GD4":
        sigma = defender.effects.get("sigma", 0.0)
        for i, src in enumerate(nodes):
            for j, dst in enumerate(nodes):
                if src["layer"] == "edge" and dst["layer"] == "edge":
                    C_mod[i, j] *= (1 - sigma)
                if src["layer"] == "edge" and dst.get("role") == "control":
                    C_mod[i, j] *= (1 - sigma) * 0.8

    return C_mod


def apply_defender_to_target(attacker, defender, state_context):
    pi = dict(attacker.pi)
    a_r = attacker.a_r
    lambdas = dict(state_context["lambdas"])
    C_r_target = state_context["C_r"]

    if defender.key == "GD2":
        for k in pi:
            pi[k] *= defender.effects.get("capability_mult", 1.0)
    if defender.key == "GD6":
        for k in pi:
            pi[k] *= (1 - defender.effects.get("rate_limit_varphi", 0.0))
    if defender.key == "GD3":
        a_r = min(1.5, a_r + defender.effects.get("agility_bonus", 0.0))
    if defender.key == "GD5":
        for k in lambdas:
            lambdas[k] = min(0.95, lambdas[k] + defender.effects.get("lambda_boost", 0.0))
        C_r_target *= (1 - 0.10 * defender.effects.get("decoy_delta", 0.0))
    if defender.key == "GD8":
        C_r_target = max(0.0, C_r_target - 0.25)

    return {
        "pi": pi,
        "a_r": a_r,
        "lambdas": lambdas,
        "C_r_target": C_r_target,
        "R_cia": state_context["R_cia"],
    }


def build_payoff_matrices(active_defenders, attackers, state_context, cfg, nodes, C_base):
    m = len(attackers)
    n = len(active_defenders)
    A = np.zeros((m, n))
    B = np.zeros((m, n))

    pair_details = {a.key: {} for a in attackers}
    defender_xi = {}
    defender_dc = {}

    sal_vals = []
    sap_vals = []
    ac_sum = {"T": 0.0, "H": 0.0, "K": 0.0, "R": 0.0, "D": 0.0}
    dc_sum = {"ASSC": 0.0, "NC": 0.0, "AIC": 0.0}
    pair_count = 0

    for j, defender in enumerate(active_defenders):
        C_mod = apply_defender_to_topology(defender, C_base, nodes)
        xi_total, xi_by_hop = compute_xi(
            C_mod, cfg["simulation"]["gamma"], cfg["simulation"]["z_max"]
        )
        defender_xi[defender.key] = {"xi_total": xi_total, "xi_by_hop": xi_by_hop}

        dc_by_attacker = []

        for i, attacker in enumerate(attackers):
            params = apply_defender_to_target(attacker, defender, state_context)
            attacker_mod = AttackerStrategy(
                key=attacker.key,
                pi=params["pi"],
                W_cia=attacker.W_cia,
                base_cost_features=attacker.base_cost_features,
                a_r=params["a_r"],
            )

            sal = SAL(
                attacker_mod,
                defender,
                xi_total,
                params["C_r_target"],
                params["R_cia"],
                params["lambdas"],
                params["a_r"],
            )
            sap = SAP(
                attacker_mod,
                defender,
                xi_total,
                params["C_r_target"],
                params["R_cia"],
                params["lambdas"],
                params["a_r"],
                cfg,
            )

            AC_total, AC_components = AC(attacker, defender, cfg)
            DC_total, ASSC, NC, AIC = DC(defender, cfg, params["a_r"])

            thetas = _theta_values(attacker_mod, defender, params["lambdas"], params["a_r"])
            G_p = cfg["costs"]["beta_reg"] * float(np.mean(list(thetas.values())))
            R_alpha = cfg["costs"]["third_party"]["alpha_I"] if defender.sharing_enabled else 0.0

            U_A = sal - AC_total - G_p
            U_D = sap - DC_total - R_alpha

            A[i, j] = U_A
            B[i, j] = U_D

            pair_details[attacker.key][defender.key] = {
                "xi_total": xi_total,
                "xi_by_hop": xi_by_hop,
                "SAL": sal,
                "SAP": sap,
                "AC_total": AC_total,
                "AC_components": AC_components,
                "DC_total": DC_total,
                "DC_components": {"ASSC": ASSC, "NC": NC, "AIC": AIC},
            }

            dc_by_attacker.append(DC_total)

            sal_vals.append(sal)
            sap_vals.append(sap)
            for k in ac_sum:
                ac_sum[k] += AC_components[k]
            dc_sum["ASSC"] += ASSC
            dc_sum["NC"] += NC
            dc_sum["AIC"] += AIC
            pair_count += 1

        defender_dc[defender.key] = {
            "total": float(np.mean(dc_by_attacker)),
            "ASSC": float(np.mean([DC(defender, cfg, state_context["a_r"])[1]])),
            "NC": float(np.mean([DC(defender, cfg, state_context["a_r"])[2]])),
            "AIC": float(np.mean([DC(defender, cfg, state_context["a_r"])[3]])),
            "dc_by_attacker": dc_by_attacker,
        }

    summary = {
        "sal_mean": float(np.mean(sal_vals)) if sal_vals else 0.0,
        "sap_mean": float(np.mean(sap_vals)) if sap_vals else 0.0,
        "ac_mean": {k: (ac_sum[k] / pair_count) if pair_count else 0.0 for k in ac_sum},
        "dc_mean": {k: (dc_sum[k] / pair_count) if pair_count else 0.0 for k in dc_sum},
    }

    aux = {
        "pair_details": pair_details,
        "defender_xi": defender_xi,
        "defender_dc": defender_dc,
        "summary": summary,
    }

    return A, B, aux
