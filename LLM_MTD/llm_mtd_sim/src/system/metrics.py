import numpy as np


def compute_xi(C, gamma, z_max):
    z = C.shape[0]
    xi_total = 0.0
    xi_by_hop = []
    C_power = np.array(C, dtype=float)
    for hop in range(1, z_max + 1):
        if hop == 1:
            C_power = C
        else:
            C_power = C_power @ C
        xi_z = (gamma ** hop) * (np.sum(C_power) / z)
        xi_by_hop.append(float(xi_z))
        xi_total += xi_z
    return float(xi_total), xi_by_hop
