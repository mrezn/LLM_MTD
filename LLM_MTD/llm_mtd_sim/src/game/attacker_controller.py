import numpy as np

from ..utils import normalize

ATTACKER_KEYS = ["GA1", "GA2", "GA3"]


class AttackerController:
    def __init__(self, cfg, rng=None):
        self.cfg = cfg
        if rng is None:
            rng = np.random.default_rng(cfg["simulation"]["seed"] + 13)
        self.rng = rng
        self.attacker_keys = list(ATTACKER_KEYS)
        self.s = np.array([0.5, 0.5], dtype=float)
        self.p_P1 = np.array([1.0 / 3, 1.0 / 3, 1.0 / 3], dtype=float)
        self.p_P2 = np.array([1.0 / 3, 1.0 / 3, 1.0 / 3], dtype=float)
        self.prev_path = None
        self.episode_counter = 0

    def start_episode(self, episode_id):
        self.episode_counter = episode_id

    def marginal_p(self):
        return normalize(self.s[0] * self.p_P1 + self.s[1] * self.p_P2)

    def _softmax(self, values, tau):
        tau = max(float(tau), 1e-9)
        vals = np.array(values, dtype=float) / tau
        vals -= np.max(vals)
        exp_vals = np.exp(vals)
        return normalize(exp_vals)

    def _fitness(self, f_values, omega, tau):
        transform = self.cfg["attacker"].get("fitness_transform", "exp")
        scale = float(omega) / max(float(tau), 1e-9)
        if transform != "exp":
            transform = "exp"
        F = np.exp(scale * np.array(f_values, dtype=float))
        clip = float(self.cfg["attacker"].get("fitness_clip_min", 1e-12))
        return np.maximum(F, clip)

    def evaluate_paths(self, k, defender_bar_q, env_preview):
        f_p1_s1 = env_preview("P1", 1, defender_bar_q, self.p_P1)[2]
        f_p1_s2 = env_preview("P1", 2, defender_bar_q, self.p_P1)[2]
        f_p2_s1 = env_preview("P2", 1, defender_bar_q, self.p_P2)[2]
        f_p2_s2 = env_preview("P2", 2, defender_bar_q, self.p_P2)[2]

        f_P1 = 0.5 * (f_p1_s1 + f_p1_s2)
        f_P2 = 0.5 * (f_p2_s1 + f_p2_s2)

        bar_f_P1 = float(np.dot(self.p_P1, f_P1))
        bar_f_P2 = float(np.dot(self.p_P2, f_P2))

        C_switch = float(self.cfg["attacker"]["C_switch"])
        g_P1 = bar_f_P1 - (C_switch if self.prev_path and self.prev_path != "P1" else 0.0)
        g_P2 = bar_f_P2 - (C_switch if self.prev_path and self.prev_path != "P2" else 0.0)

        debug = {
            "f_P1": f_P1,
            "f_P2": f_P2,
            "f_P1_states": [f_p1_s1, f_p1_s2],
            "f_P2_states": [f_p2_s1, f_p2_s2],
            "bar_f_P1": bar_f_P1,
            "bar_f_P2": bar_f_P2,
        }
        return g_P1, g_P2, debug

    def choose_path(self, k, g_P1, g_P2):
        tau_br = self.cfg["attacker"]["tau_BR_P"]
        rho = self.cfg["attacker"]["rho_P"]
        br = self._softmax([g_P1, g_P2], tau_br)
        pi = normalize((1 - rho) * self.s + rho * br)
        idx = int(self.rng.choice(2, p=pi))
        chosen = "P1" if idx == 0 else "P2"
        self.prev_path = chosen
        return chosen, {"BRpath": br, "piPath": pi}

    def choose_tactic(self, k, chosen_path, f_Ai_state):
        tau_br = self.cfg["attacker"]["tau_BR_A"]
        rho = self.cfg["attacker"]["rho_A"]
        br = self._softmax(f_Ai_state, tau_br)
        p_path = self.p_P1 if chosen_path == "P1" else self.p_P2
        pi = normalize((1 - rho) * p_path + rho * br)
        idx = int(self.rng.choice(len(pi), p=pi))
        chosen = self.attacker_keys[idx]
        return chosen, {"BR": br, "pi": pi, "idx": idx}

    def update_tactics(self, k, f_Ai_P1, f_Ai_P2):
        eta = self.cfg["attacker"]["eta_A"]
        eps = self.cfg["attacker"]["eps_A"]
        omega = self.cfg["attacker"]["omega_A"]
        tau = self.cfg["attacker"]["tau_A"]

        self.p_P1 = self._replicator_mutator(self.p_P1, f_Ai_P1, eta, eps, omega, tau)
        self.p_P2 = self._replicator_mutator(self.p_P2, f_Ai_P2, eta, eps, omega, tau)
        return self.p_P1, self.p_P2

    def update_paths(self, k, g_P1, g_P2):
        eta = self.cfg["attacker"]["eta_P"]
        eps = self.cfg["attacker"]["eps_P"]
        omega = self.cfg["attacker"]["omega_P"]
        tau = self.cfg["attacker"]["tau_P"]

        F = self._fitness([g_P1, g_P2], omega, tau)
        denom = float(np.sum(self.s * F))
        if denom <= 0:
            s_tilde = normalize(self.s)
        else:
            s_tilde = normalize(self.s * F / denom)

        M = np.array([[1 - eps, eps], [eps, 1 - eps]], dtype=float)
        s_mut = s_tilde @ M
        self.s = normalize((1 - eta) * self.s + eta * s_mut)
        return self.s

    def tactic_fitness(self, f_Ai, path_label):
        omega = self.cfg["attacker"]["omega_A"]
        tau = self.cfg["attacker"]["tau_A"]
        return self._fitness(f_Ai, omega, tau)

    def _replicator_mutator(self, p, f_values, eta, eps, omega, tau):
        F = self._fitness(f_values, omega, tau)
        denom = float(np.sum(p * F))
        if denom <= 0:
            p_tilde = normalize(p)
        else:
            p_tilde = normalize(p * F / denom)

        n = len(p)
        if n < 2:
            return normalize(p)

        M = np.full((n, n), eps / (n - 1), dtype=float)
        np.fill_diagonal(M, 1 - eps)
        p_mut = p_tilde @ M
        return normalize((1 - eta) * p + eta * p_mut)
