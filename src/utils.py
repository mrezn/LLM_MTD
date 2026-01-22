import json

import numpy as np


def normalize(vec):
    vec = np.array(vec, dtype=float)
    total = float(np.sum(vec))
    if total <= 0:
        return np.ones_like(vec) / len(vec)
    return vec / total


def choice_from_probs(rng, keys, probs):
    probs = normalize(probs)
    idx = int(rng.choice(len(keys), p=probs))
    return idx, keys[idx]


def safe_softmax(logits, beta):
    logits = np.array(logits, dtype=float)
    scaled = beta * (logits - np.max(logits))
    exps = np.exp(scaled)
    return exps / np.sum(exps)


def json_dumps(value):
    return json.dumps(value)


def clamp(value, low, high):
    return max(low, min(high, value))
