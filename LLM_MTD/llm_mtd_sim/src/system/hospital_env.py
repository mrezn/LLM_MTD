import numpy as np

from .metrics import compute_xi
from .topology import effective_edges, init_hospital_graph, sample_activity, sample_adjacency


class HospitalEnv:
    def __init__(self, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.cfg["_rng"] = rng
        self.nodes, self.base_edge_probs = init_hospital_graph(cfg)
        self.cfg["_nodes"] = self.nodes
        self.prev_b = None
        self.states = ["S1", "S2"]

    def reset_episode(self, ep_id):
        self.prev_b = None
        return {"episode": ep_id, "states": list(self.states)}

    def _target_from_node(self, node):
        return {
            "target_key": node["name"],
            "C_r": node["C_r"],
            "R_cia": dict(node["R_cia"]),
            "lambdas": dict(node["lambdas"]),
            "a_r": 1.0,
        }

    def _average_targets(self, prefix):
        selected = [n for n in self.nodes if n["name"].startswith(prefix)]
        if not selected:
            return None
        C_r = float(np.mean([n["C_r"] for n in selected]))
        R_c = float(np.mean([n["R_cia"]["c"] for n in selected]))
        R_i = float(np.mean([n["R_cia"]["i"] for n in selected]))
        R_a = float(np.mean([n["R_cia"]["a"] for n in selected]))
        L_c = float(np.mean([n["lambdas"]["c"] for n in selected]))
        L_i = float(np.mean([n["lambdas"]["i"] for n in selected]))
        L_a = float(np.mean([n["lambdas"]["a"] for n in selected]))
        return {
            "target_key": prefix,
            "C_r": C_r,
            "R_cia": {"c": R_c, "i": R_i, "a": R_a},
            "lambdas": {"c": L_c, "i": L_i, "a": L_a},
            "a_r": 1.0,
        }

    def _target_for_state(self, path, state):
        if path == "P1":
            if state == "S1":
                return self._average_targets("sensor_medical")
            edge_nodes = [n for n in self.nodes if n["name"] == "edge_vm_medical"]
            target = edge_nodes[0] if edge_nodes else self.nodes[0]
            return self._target_from_node(target)

        if state == "S1":
            target = next((n for n in self.nodes if n["name"] == "cloud_api"), None)
            if target is None:
                target = next((n for n in self.nodes if n["name"] == "ctrl_sdn"), None)
            return self._target_from_node(target)

        target = next((n for n in self.nodes if n["name"] == "cloud_store"), None)
        if target is None:
            target = next((n for n in self.nodes if n["name"] == "cloud_api"), None)
        return self._target_from_node(target)

    def step(self, path, state_idx, active_defenders=None, attackers=None, payoff_builder=None):
        b = sample_activity(self.prev_b, self.cfg)
        self.prev_b = b
        R_e = sample_adjacency(self.cfg, self.base_edge_probs)
        C = effective_edges(R_e, b)
        xi_total, xi_by_hop = compute_xi(
            C, self.cfg["simulation"]["gamma"], self.cfg["simulation"]["z_max"]
        )
        state = self.states[state_idx - 1]
        target = self._target_for_state(path, state)
        state_context = {
            "C_r": target["C_r"],
            "R_cia": target["R_cia"],
            "lambdas": target["lambdas"],
            "a_r": target["a_r"],
            "target_key": target["target_key"],
        }

        info = {
            "path": path,
            "state": state,
            "b": b,
            "R_e": R_e,
            "C": C,
            "xi_base": xi_total,
            "xi_by_hop_base": xi_by_hop,
            "state_context": state_context,
        }

        if active_defenders is None or attackers is None or payoff_builder is None:
            return info

        A, B, aux = payoff_builder(active_defenders, attackers, state_context, self.cfg, self.nodes, C)
        info.update({"A": A, "B": B, "aux": aux})
        return info

    def preview_payoffs(
        self,
        path,
        state_idx,
        defender_bar_q,
        attacker_p_for_that_path,
        active_defenders,
        attackers,
        payoff_builder,
    ):
        prev_b = self.prev_b
        rng_state = self.rng.bit_generator.state

        b = sample_activity(prev_b, self.cfg)
        R_e = sample_adjacency(self.cfg, self.base_edge_probs)
        C = effective_edges(R_e, b)
        xi_total, xi_by_hop = compute_xi(
            C, self.cfg["simulation"]["gamma"], self.cfg["simulation"]["z_max"]
        )

        state = self.states[state_idx - 1]
        target = self._target_for_state(path, state)
        state_context = {
            "C_r": target["C_r"],
            "R_cia": target["R_cia"],
            "lambdas": target["lambdas"],
            "a_r": target["a_r"],
            "target_key": target["target_key"],
        }

        A, _, aux = payoff_builder(active_defenders, attackers, state_context, self.cfg, self.nodes, C)
        f_A = A @ np.array(defender_bar_q, dtype=float)

        self.rng.bit_generator.state = rng_state
        self.prev_b = prev_b

        return A, aux, f_A
