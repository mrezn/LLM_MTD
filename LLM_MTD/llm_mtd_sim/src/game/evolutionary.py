import numpy as np

from ..utils import normalize


def expected_payoffs(A, B, p, q):
    fA = A @ q
    fD = B.T @ p
    return fA, fD


def fitness(f, omega):
    F = 1.0 + omega * f
    return np.maximum(F, 1e-6)


def attacker_update(p, fA, eta, omega_A):
    F = fitness(fA, omega_A)
    denom = float(np.sum(p * F))
    if denom <= 0:
        return normalize(p)
    p_next = p + eta * ((p * F) / denom - p)
    return normalize(p_next)


def defender_update(q, fD, eta, omega_D, llm_lambda, sigma_llm, M):
    F = fitness(fD, omega_D)
    qbar = normalize((1 - llm_lambda) * q + llm_lambda * sigma_llm)
    denom = float(np.sum(qbar * F))
    if denom <= 0:
        q_tilde = qbar
    else:
        q_tilde = (qbar * F) / denom
    q_tilde = normalize(q_tilde)
    M = np.array(M, dtype=float)
    if M.shape[0] != len(q):
        M = np.eye(len(q))
    q_mut = q_tilde @ M
    q_next = (1 - eta) * q + eta * q_mut
    return normalize(q_next), {"qbar": qbar, "q_tilde": q_tilde, "q_mut": q_mut}


def apply_active_pool_control(
    active_keys,
    pool_keys,
    q,
    fD_episode,
    cfg,
    low_q_streak,
    dc_history,
    llm_suggested,
    last_promo_episode,
    episode,
    no_demotion_episodes,
):
    active_keys = list(active_keys)
    pool_keys = list(pool_keys)
    q = np.array(q, dtype=float)
    demoted = []

    def top2_keys():
        if len(active_keys) <= 2:
            return set(active_keys)
        idxs = np.argsort(q)
        return {active_keys[idxs[-1]], active_keys[idxs[-2]]}

    def can_demote(key):
        if key not in active_keys:
            return False
        if len(active_keys) - len(demoted) <= 3:
            return False
        if key in top2_keys():
            return False
        return True

    for key in llm_suggested.get("demote_keys", []):
        if can_demote(key) and key not in demoted:
            demoted.append(key)

    max_active = cfg["active_pool"]["max_active"]
    if len(active_keys) - len(demoted) > max_active:
        idxs = np.argsort(fD_episode)
        for idx in idxs:
            key = active_keys[idx]
            if can_demote(key) and key not in demoted:
                demoted.append(key)
            if len(active_keys) - len(demoted) <= max_active:
                break

    demote_q_min = cfg["active_pool"]["demote_q_min"]
    demote_patience = cfg["active_pool"]["demote_patience"]
    for i, key in enumerate(active_keys):
        if key in demoted:
            continue
        if low_q_streak.get(key, 0) >= demote_patience and q[i] < demote_q_min:
            if can_demote(key):
                demoted.append(key)

    dc_max = cfg["active_pool"]["dc_max"]
    for key in active_keys:
        if key in demoted:
            continue
        history = dc_history.get(key)
        if history:
            avg = float(np.mean(history))
            if avg > dc_max and can_demote(key):
                demoted.append(key)

    if not demoted and no_demotion_episodes >= demote_patience and len(active_keys) > 3:
        idxs = np.argsort(q)
        for idx in idxs:
            key = active_keys[idx]
            if can_demote(key):
                demoted.append(key)
                break

    if demoted:
        no_demotion_episodes = 0
    else:
        no_demotion_episodes += 1

    for key in list(demoted):
        if key not in active_keys:
            demoted.remove(key)
            continue
        idx = active_keys.index(key)
        active_keys.pop(idx)
        q = np.delete(q, idx)
        pool_keys.append(key)
        low_q_streak.pop(key, None)
        dc_history.pop(key, None)
        if fD_episode is not None and len(fD_episode) > idx:
            fD_episode = np.delete(fD_episode, idx)

    if q.size > 0:
        q = normalize(q)

    promoted_key = ""
    promote_every = cfg["active_pool"]["promote_every"]
    llm_key = llm_suggested.get("promote_key", "NONE")
    llm_allowed = (
        llm_key not in (None, "NONE")
        and llm_key in pool_keys
        and (episode - last_promo_episode) >= promote_every
    )
    scheduled = (episode % promote_every == 0) and (episode - last_promo_episode) >= promote_every
    if pool_keys and (llm_allowed or scheduled):
        promote_key = llm_key if llm_allowed else pool_keys[0]
        pool_keys.remove(promote_key)
        active_keys.append(promote_key)
        q = np.append(q, cfg["active_pool"]["q_new_init"])
        q = normalize(q)
        low_q_streak[promote_key] = 0
        dc_history[promote_key] = []
        last_promo_episode = episode
        promoted_key = promote_key

    return (
        active_keys,
        pool_keys,
        q,
        promoted_key,
        demoted,
        low_q_streak,
        dc_history,
        last_promo_episode,
        no_demotion_episodes,
    )
