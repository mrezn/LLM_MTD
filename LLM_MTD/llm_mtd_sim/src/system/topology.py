import numpy as np


def init_hospital_graph(cfg):
    nodes = [
        {
            "name": "sensor_medical_1",
            "layer": "sensor",
            "role": "sensor",
            "C_r": 0.7,
            "R_cia": {"c": 0.7, "i": 0.6, "a": 0.5},
            "lambdas": {"c": 0.3, "i": 0.4, "a": 0.3},
        },
        {
            "name": "sensor_medical_2",
            "layer": "sensor",
            "role": "sensor",
            "C_r": 0.65,
            "R_cia": {"c": 0.6, "i": 0.5, "a": 0.4},
            "lambdas": {"c": 0.35, "i": 0.45, "a": 0.35},
        },
        {
            "name": "edge_vm_medical",
            "layer": "edge",
            "role": "edge_vm",
            "C_r": 0.9,
            "R_cia": {"c": 0.9, "i": 0.8, "a": 0.7},
            "lambdas": {"c": 0.4, "i": 0.5, "a": 0.4},
        },
        {
            "name": "edge_vm_admin",
            "layer": "edge",
            "role": "edge_vm",
            "C_r": 0.8,
            "R_cia": {"c": 0.7, "i": 0.7, "a": 0.6},
            "lambdas": {"c": 0.4, "i": 0.45, "a": 0.35},
        },
        {
            "name": "edge_vm_lab",
            "layer": "edge",
            "role": "edge_vm",
            "C_r": 0.78,
            "R_cia": {"c": 0.65, "i": 0.7, "a": 0.6},
            "lambdas": {"c": 0.35, "i": 0.4, "a": 0.35},
        },
        {
            "name": "edge_ctrl",
            "layer": "edge",
            "role": "control",
            "C_r": 0.75,
            "R_cia": {"c": 0.6, "i": 0.7, "a": 0.6},
            "lambdas": {"c": 0.35, "i": 0.4, "a": 0.3},
        },
        {
            "name": "cloud_api",
            "layer": "cloud",
            "role": "api",
            "C_r": 0.95,
            "R_cia": {"c": 0.95, "i": 0.85, "a": 0.8},
            "lambdas": {"c": 0.45, "i": 0.5, "a": 0.45},
        },
        {
            "name": "cloud_store",
            "layer": "cloud",
            "role": "store",
            "C_r": 1.0,
            "R_cia": {"c": 1.0, "i": 0.95, "a": 0.9},
            "lambdas": {"c": 0.5, "i": 0.55, "a": 0.5},
        },
        {
            "name": "ctrl_sdn",
            "layer": "cloud",
            "role": "control",
            "C_r": 0.92,
            "R_cia": {"c": 0.85, "i": 0.9, "a": 0.8},
            "lambdas": {"c": 0.45, "i": 0.5, "a": 0.45},
        },
    ]

    n = len(nodes)
    base_edge_probs = np.zeros((n, n))
    layer_probs = {
        ("sensor", "edge"): 0.7,
        ("sensor", "cloud"): 0.25,
        ("sensor", "control"): 0.2,
        ("edge", "edge"): 0.4,
        ("edge", "cloud"): 0.5,
        ("edge", "control"): 0.3,
        ("cloud", "edge"): 0.2,
        ("cloud", "cloud"): 0.3,
        ("cloud", "control"): 0.2,
        ("control", "cloud"): 0.4,
        ("control", "edge"): 0.3,
    }

    for i, src in enumerate(nodes):
        for j, dst in enumerate(nodes):
            if i == j:
                continue
            src_layer = src["layer"]
            dst_layer = dst["layer"]
            if dst.get("role") == "control":
                dst_layer = "control"
            prob = layer_probs.get((src_layer, dst_layer), 0.1)
            base_edge_probs[i, j] = prob

    return nodes, base_edge_probs


def sample_activity(prev_b, cfg):
    rng = cfg["_rng"]
    nodes = cfg["_nodes"]
    probs = []
    for node in nodes:
        if node["layer"] == "sensor":
            probs.append(0.75)
        elif node["layer"] == "edge":
            probs.append(0.65)
        else:
            probs.append(0.55)

    if prev_b is None:
        return rng.binomial(1, probs).astype(float)

    b = []
    for idx, p in enumerate(probs):
        if rng.random() < 0.8:
            b.append(prev_b[idx])
        else:
            b.append(1.0 if rng.random() < p else 0.0)
    return np.array(b, dtype=float)


def sample_adjacency(cfg, base_edge_probs):
    rng = cfg["_rng"]
    n = base_edge_probs.shape[0]
    R_e = (rng.random((n, n)) < base_edge_probs).astype(float)
    np.fill_diagonal(R_e, 0.0)
    return R_e


def effective_edges(R_e, b):
    return R_e * np.outer(b, b)
