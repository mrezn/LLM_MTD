import json
import numpy as np


def _extract_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:
        return None


def _is_prob_vector(values, tol=1e-3):
    total = sum(values)
    return abs(total - 1.0) <= tol and all(v >= 0 for v in values)


def fallback(active_keys, q):
    return {
        "macro_probs": {k: float(q[i]) for i, k in enumerate(active_keys)},
        "promote_key": "NONE",
        "demote_keys": [],
        "mutation": np.eye(len(active_keys)).tolist(),
        "notes": "fallback",
    }


def parse_macro_output(text, active_keys, pool_keys, q):
    data = _extract_json(text)
    if data is None:
        return fallback(active_keys, q), False

    macro_probs = data.get("macro_probs")
    if not isinstance(macro_probs, dict):
        return fallback(active_keys, q), False
    if set(macro_probs.keys()) != set(active_keys):
        return fallback(active_keys, q), False
    if not _is_prob_vector(list(macro_probs.values())):
        return fallback(active_keys, q), False

    promote_key = data.get("promote_key", "NONE")
    if promote_key not in ["NONE"] + list(pool_keys):
        return fallback(active_keys, q), False

    demote_keys = data.get("demote_keys", [])
    if not isinstance(demote_keys, list):
        return fallback(active_keys, q), False
    if not set(demote_keys).issubset(set(active_keys)):
        return fallback(active_keys, q), False

    mutation = data.get("mutation")
    if not isinstance(mutation, list) or len(mutation) != len(active_keys):
        return fallback(active_keys, q), False
    for row in mutation:
        if not isinstance(row, list) or len(row) != len(active_keys):
            return fallback(active_keys, q), False
        if not _is_prob_vector(row, tol=1e-2):
            return fallback(active_keys, q), False

    return {
        "macro_probs": macro_probs,
        "promote_key": promote_key,
        "demote_keys": demote_keys,
        "mutation": mutation,
        "notes": data.get("notes", ""),
    }, True
