from dataclasses import dataclass


@dataclass(frozen=True)
class AttackerStrategy:
    key: str
    pi: dict
    W_cia: dict
    base_cost_features: dict
    a_r: float = 1.0


@dataclass(frozen=True)
class DefenderStrategy:
    key: str
    theta_vector: list
    layer_scope: list
    effects: dict
    sharing_enabled: bool = False


ATTACKERS = [
    AttackerStrategy(
        key="GA1",
        pi={"c": 0.8, "i": 0.9, "a": 0.7},
        W_cia={"c": 0.20, "i": 0.35, "a": 0.45},
        base_cost_features={"T": 2, "H": 3, "K": 4, "R": 2, "D": 3},
        a_r=1.0,
    ),
    AttackerStrategy(
        key="GA2",
        pi={"c": 0.8, "i": 0.9, "a": 0.7},
        W_cia={"c": 0.10, "i": 0.55, "a": 0.35},
        base_cost_features={"T": 5, "H": 4, "K": 5, "R": 4, "D": 5},
        a_r=1.0,
    ),
    AttackerStrategy(
        key="GA3",
        pi={"c": 0.8, "i": 0.9, "a": 0.7},
        W_cia={"c": 0.45, "i": 0.15, "a": 0.40},
        base_cost_features={"T": 6, "H": 2, "K": 3, "R": 3, "D": 4},
        a_r=1.0,
    ),
]

DEFENDERS = [
    DefenderStrategy(
        key="GD1",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["sensor", "edge"],
        effects={"ingress_factor": 0.75, "c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD2",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["sensor", "edge"],
        effects={"ingress_factor": 0.60, "capability_mult": 0.9, "c_star": 1.1},
    ),
    DefenderStrategy(
        key="GD3",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge"],
        effects={"agility_bonus": 0.1, "c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD4",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge"],
        effects={"sigma": 0.3, "c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD5",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge", "cloud"],
        effects={"lambda_boost": 0.10, "decoy_delta": 0.3, "c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD6",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge", "cloud"],
        effects={"rate_limit_varphi": 0.2, "c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD7",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge"],
        effects={"c_star": 1.0},
    ),
    DefenderStrategy(
        key="GD8",
        theta_vector=[0.34, 0.33, 0.33],
        layer_scope=["edge", "cloud"],
        effects={"edge_failover": True, "nc_boost": 1.0, "c_star": 1.0},
    ),
]

DEFENDER_KEYS = [d.key for d in DEFENDERS]

INITIAL_ACTIVE = ["GD1", "GD2", "GD3"]
INITIAL_POOL = ["GD4", "GD5", "GD6", "GD7", "GD8"]
