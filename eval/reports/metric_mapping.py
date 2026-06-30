from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

import pandas as pd


METHOD_LABELS: Final[dict[str, str]] = {
    "baseline_game": "Baseline game",
    "llm_defender": "LLM defender",
}

DEFENDER_ACTION_ORDER: Final[list[str]] = [
    "observe",
    "quarantine_sensor",
    "rate_limit",
    "isolate_sensor",
    "reroute_traffic",
    "release_sensor",
]


@dataclass(frozen=True, slots=True)
class FormalMetricRow:
    formal_term: str
    meaning: str
    operationalization: str
    data_source: str
    unit_or_scale: str


FORMAL_TO_OBSERVABLE_ROWS: Final[list[FormalMetricRow]] = [
    FormalMetricRow(
        formal_term="SAL",
        meaning="Attacker-side attack value for the selected path.",
        operationalization=(
            "Approximated with target criticality, plausible-path depth, and whether the live stage reached "
            "gateway/worker/cloud attack-effect milestones."
        ),
        data_source="MulVAL path metadata, attack scenario registry, stage state summary.",
        unit_or_scale="normalized utility proxy [0,1]",
    ),
    FormalMetricRow(
        formal_term="SAP",
        meaning="Defender-side protection value for the current mission path.",
        operationalization=(
            "Approximated with target importance, current attack pressure, and whether the selected defense "
            "reduced observed attack progression."
        ),
        data_source="Stage state summary, stage validation, scenario registry.",
        unit_or_scale="normalized utility proxy [0,1]",
    ),
    FormalMetricRow(
        formal_term="AC",
        meaning="Attacker cost for mounting the selected live attack.",
        operationalization=(
            "Read from attacker strategy metadata as time, resource, knowledge, risk, and detectability cost."
        ),
        data_source="Strategy space attacker metadata.",
        unit_or_scale="normalized weighted cost [0,1]",
    ),
    FormalMetricRow(
        formal_term="DC",
        meaning="Defender cost for the selected mitigation.",
        operationalization=(
            "Read from defender strategy metadata and combined with controller overhead and QoS impact observed "
            "after action execution."
        ),
        data_source="Strategy space defender metadata, controller metrics, QoS deltas.",
        unit_or_scale="normalized weighted cost [0,1]",
    ),
    FormalMetricRow(
        formal_term="resource_significance",
        meaning="Importance of the attacked or protected node in the edge-cloud workflow.",
        operationalization=(
            "Approximated by target criticality, node role, and whether the path reaches edge gateway, edge worker, "
            "or cloud target assets."
        ),
        data_source="Scenario registry, strategy space, topology model.",
        unit_or_scale="ordinal role significance / normalized criticality",
    ),
    FormalMetricRow(
        formal_term="impact_weight",
        meaning="Weight assigned to attack or defense effects in the game update.",
        operationalization=(
            "Derived from expected attack effects, expected defense effects, and observed progression or "
            "containment outcomes."
        ),
        data_source="Strategy metadata, stage validation, state summary.",
        unit_or_scale="normalized influence weight [0,1]",
    ),
    FormalMetricRow(
        formal_term="controller_overhead",
        meaning="Operational cost imposed on the MTD control plane.",
        operationalization=(
            "Measured with active policy actions, flow rules installed, meters added, controller apply time, and "
            "controller CPU or memory deltas."
        ),
        data_source="Ryu /mtd/status, /mtd/metrics, stage controller deltas.",
        unit_or_scale="counts, milliseconds, CPU seconds, memory KB",
    ),
    FormalMetricRow(
        formal_term="qos_degradation",
        meaning="Service-quality penalty induced by attack pressure or mitigation.",
        operationalization=(
            "Measured using sensor-to-edge latency, edge-to-cloud latency, throughput, and packet loss deltas "
            "between consecutive stages."
        ),
        data_source="Stage QoS metrics and QoS deltas.",
        unit_or_scale="milliseconds, bytes per second, loss rate",
    ),
    FormalMetricRow(
        formal_term="defense_success",
        meaning="Whether the defense both executed and meaningfully constrained the attack.",
        operationalization=(
            "Separated into defense_applied, defense_confirmed, defense_effects_confirmed, and defense_success to "
            "distinguish intent, controller confirmation, and observable containment."
        ),
        data_source="Stage validation, state summary, defense-result logging.",
        unit_or_scale="boolean rate",
    ),
]


def pretty_method_label(method: str) -> str:
    return METHOD_LABELS.get(method, method.replace("_", " ").title())


def formal_mapping_frame() -> pd.DataFrame:
    return pd.DataFrame([asdict(row) for row in FORMAL_TO_OBSERVABLE_ROWS])
