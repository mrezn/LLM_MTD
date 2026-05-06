"""Export the LLM_MTD_emo topology as an abstract MulVAL security model.

This imports `network_model.py`, the same static model source used by
`topology.py`. It avoids importing `topology.py` directly because that would
require Mininet/Containernet for a static export.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ASSET_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ASSET_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ASSET_DIR) not in sys.path:
    sys.path.insert(0, str(ASSET_DIR))

from mulval_input_builder import render_mulval_program  # noqa: E402
from network_model import (  # noqa: E402
    CLOUD_NODE_MAP,
    EDGE_GATEWAY_IDS,
    EDGE_GATEWAY_WORKER_MAP,
    EDGE_NODE_MAP,
    EDGE_WORKER_ASSIGNMENTS,
    SENSOR_DESTINATION_MAP,
    SENSOR_NODE_MAP,
    SWITCH_DPIDS,
    node_ips,
    primary_ip,
)


SERVICE_PORT = 8000
DEFAULT_SCENARIO_ID = "auto_full_topology"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_abstract_topology():
    """Return a policy-friendly abstract model for MulVAL input generation."""
    model = {
        "schema_version": "llm-mtd-mulval-topology-v1",
        "scenario_id": DEFAULT_SCENARIO_ID,
        "generated_at": now_iso(),
        "source": "network_model.py",
        "hosts": [],
        "connectivity": [],
        "weaknesses": [],
        "entry_points": [
            {
                "id": "external_compromise_sensor",
                "kind": "compromised_host",
                "node": "sen4",
                "description": "Attacker already controls one sensor.",
            },
            {
                "id": "edge_gateway_facing_service",
                "kind": "external_service",
                "node": "edge2_gw",
                "protocol": "tcp",
                "port": SERVICE_PORT,
                "description": "Exposed edge gateway API surface.",
            },
            {
                "id": "cloud_exposed_endpoint",
                "kind": "external_service",
                "node": "cloud_policy",
                "protocol": "tcp",
                "port": SERVICE_PORT,
                "description": "Cloud policy endpoint is externally reachable.",
            },
        ],
        "critical_targets": ["cloud_db"],
        "attack_goal": {
            "predicate": "execCode",
            "host": "cloud_db",
            "privilege": "root",
        },
        "management_visibility": [],
    }

    for node_name in SENSOR_NODE_MAP:
        model["hosts"].append(_host(node_name, layer="sensor", role="sensor"))

    for node_name in EDGE_NODE_MAP:
        role = "edge_gateway" if node_name in EDGE_GATEWAY_IDS else "edge_worker"
        model["hosts"].append(_host(node_name, layer="edge", role=role))

    for node_name in CLOUD_NODE_MAP:
        model["hosts"].append(_host(node_name, layer="cloud", role="cloud_service"))

    model["hosts"].append(
        {
            "id": "ryu_controller",
            "layer": "controller",
            "role": "sdn_controller",
            "ips": ["127.0.0.1"],
        }
    )

    _add_sensor_gateway_connectivity(model)
    _add_gateway_worker_connectivity(model)
    _add_edge_cloud_connectivity(model)
    _add_controller_management_visibility(model)
    _add_weakness_assumptions(model)
    _dedupe_connectivity(model)
    return model


def _host(node_name, layer, role):
    return {
        "id": node_name,
        "layer": layer,
        "role": role,
        "ips": list(node_ips(node_name)),
        "primary_ip": primary_ip(node_name),
    }


def _add_sensor_gateway_connectivity(model):
    for sensor_id, destination_urls in SENSOR_DESTINATION_MAP.items():
        for destination_url in destination_urls:
            gateway = _node_for_url(destination_url)
            if gateway:
                _add_connectivity(model, sensor_id, gateway, "sensor_to_gateway")


def _add_gateway_worker_connectivity(model):
    for gateway, worker_routes in EDGE_GATEWAY_WORKER_MAP.items():
        for sensor_id, destination_url in worker_routes.items():
            worker = _node_for_url(destination_url)
            if worker:
                _add_connectivity(
                    model,
                    gateway,
                    worker,
                    f"gateway_to_worker_for_{sensor_id}",
                )


def _add_edge_cloud_connectivity(model):
    for edge_node in EDGE_NODE_MAP:
        for cloud_node in CLOUD_NODE_MAP:
            _add_connectivity(model, edge_node, cloud_node, "edge_to_cloud_via_core")


def _add_controller_management_visibility(model):
    for switch_name in SWITCH_DPIDS:
        model["management_visibility"].append(
            {"controller": "ryu_controller", "target": switch_name}
        )
    for node_name in list(EDGE_NODE_MAP) + list(CLOUD_NODE_MAP) + list(SENSOR_NODE_MAP):
        model["management_visibility"].append(
            {"controller": "ryu_controller", "target": node_name}
        )


def _add_weakness_assumptions(model):
    for sensor_id in SENSOR_NODE_MAP:
        _add_weakness(model, sensor_id, "sensor_node_exposed", "sensor_agent_api")

    for gateway in EDGE_GATEWAY_IDS:
        _add_weakness(model, gateway, "weak_auth_on_gateway", "edge_gateway_api")

    for worker in EDGE_WORKER_ASSIGNMENTS:
        _add_weakness(model, worker, "worker_service_exposed", "edge_worker_api")

    for cloud_node in CLOUD_NODE_MAP:
        _add_weakness(model, cloud_node, "cloud_api_reachable", f"{cloud_node}_api")

    _add_weakness(
        model,
        "ryu_controller",
        "controller_management_reachable",
        "ryu_rest_api",
        port=8080,
    )


def _add_weakness(
    model,
    host,
    weakness_id,
    service,
    protocol="tcp",
    port=SERVICE_PORT,
    privilege="root",
):
    model["weaknesses"].append(
        {
            "host": host,
            "id": weakness_id,
            "service": service,
            "protocol": protocol,
            "port": port,
            "privilege": privilege,
            "exploit_class": "remoteExploit",
            "impact": "privEscalation",
        }
    )


def _add_connectivity(model, src, dst, reason, protocol="tcp", port=SERVICE_PORT):
    model["connectivity"].append(
        {
            "src": src,
            "dst": dst,
            "protocol": protocol,
            "port": port,
            "reason": reason,
        }
    )


def _dedupe_connectivity(model):
    seen = set()
    deduped = []
    for edge in model["connectivity"]:
        key = (edge["src"], edge["dst"], edge["protocol"], edge["port"], edge["reason"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    model["connectivity"] = deduped


def _node_for_url(url):
    host = urlparse(url).hostname
    if not host:
        return None
    for node_name in list(SENSOR_NODE_MAP) + list(EDGE_NODE_MAP) + list(CLOUD_NODE_MAP):
        if host in node_ips(node_name):
            return node_name
    return None


def write_json(model, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def write_mulval(model, output_path, scenario_id=None):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_mulval_program(model, scenario_id=scenario_id),
        encoding="utf-8",
    )
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Export LLM_MTD_emo topology to MulVAL input.")
    parser.add_argument(
        "--output",
        "-o",
        default=str(PROJECT_ROOT / "integrations" / "mulval" / "outputs" / "topology_auto.P"),
        help="Output MulVAL .P file.",
    )
    parser.add_argument(
        "--json-output",
        default=str(PROJECT_ROOT / "integrations" / "mulval" / "outputs" / "topology_auto.json"),
        help="Output abstract topology JSON file.",
    )
    parser.add_argument("--scenario-id", default=DEFAULT_SCENARIO_ID)
    args = parser.parse_args()

    model = build_abstract_topology()
    model["scenario_id"] = args.scenario_id
    json_path = write_json(model, args.json_output)
    mulval_path = write_mulval(model, args.output, scenario_id=args.scenario_id)
    print(f"wrote {json_path}")
    print(f"wrote {mulval_path}")


if __name__ == "__main__":
    main()
