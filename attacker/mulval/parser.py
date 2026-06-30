"""Parse MulVAL output into policy-friendly JSON for LLM_MTD_emo."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path


ASSET_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ASSET_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(ASSET_DIR) not in sys.path:
    sys.path.insert(0, str(ASSET_DIR))


HOST_TOKEN_TEMPLATE = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"
HACL_PATTERN = re.compile(
    r"hacl\(\s*([A-Za-z0-9_']+)\s*,\s*([A-Za-z0-9_']+)\s*,",
    re.IGNORECASE,
)
ATTACKER_PATTERN = re.compile(r"attackerLocated\(\s*([A-Za-z0-9_']+)\s*\)", re.IGNORECASE)
ATTACK_GOAL_PATTERN = re.compile(
    r"attackGoal\(\s*execCode\(\s*([A-Za-z0-9_']+)\s*,",
    re.IGNORECASE,
)
EXEC_CODE_PATTERN = re.compile(
    r"execCode\(\s*([A-Za-z0-9_']+)\s*,",
    re.IGNORECASE,
)
EXPECTED_PATH_PATTERN = re.compile(r"expectedAttackPath\(([^)]+)\)\.", re.IGNORECASE)


def load_topology(path=None):
    if path:
        with open(path, "r", encoding="utf-8") as topology_file:
            return json.load(topology_file)

    from topology_export import build_abstract_topology

    return build_abstract_topology()


def read_graph_text(path):
    graph_path = resolve_graph_path(path)
    raw = graph_path.read_text(encoding="utf-8", errors="replace")
    if graph_path.suffix.lower() != ".xml":
        return raw

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return raw

    values = [raw]
    for element in root.iter():
        if element.text:
            values.append(element.text)
        if element.tail:
            values.append(element.tail)
        values.extend(str(value) for value in element.attrib.values())
    return "\n".join(values)


def resolve_graph_path(path):
    graph_path = Path(path)
    if graph_path.exists():
        return graph_path

    if graph_path.name == "AttackGraph.xml":
        text_fallback = graph_path.with_name("AttackGraph.txt")
        if text_fallback.exists():
            return text_fallback

    raise FileNotFoundError(
        f"MulVAL graph file not found: {graph_path}. "
        "If graph_gen.sh only produced text output, pass AttackGraph.txt "
        "or place it next to the requested AttackGraph.xml path."
    )


def parse_policy_graph(graph_path, topology=None, scenario_id="mulval_scenario"):
    topology = topology or load_topology()
    graph_text = read_graph_text(graph_path)
    hosts = sorted((host["id"] for host in topology.get("hosts", [])), key=len, reverse=True)
    mentioned_hosts = extract_host_mentions(graph_text, hosts)
    expected_paths = extract_expected_paths(graph_text, hosts)

    entry_points = extract_attacker_locations(graph_text, hosts)
    if not entry_points:
        entry_points = [
            entry["node"]
            for entry in topology.get("entry_points", [])
            if entry.get("kind") == "compromised_host"
        ]

    critical_targets = extract_attack_goals(graph_text, hosts)
    if not critical_targets:
        critical_targets = list(topology.get("critical_targets", []))

    graph_edges = extract_hacl_edges(graph_text, hosts)
    topology_edges = [
        (edge["src"], edge["dst"])
        for edge in topology.get("connectivity", [])
        if edge["src"] in hosts and edge["dst"] in hosts
    ]

    attack_paths = []
    if expected_paths:
        attack_paths.extend(expected_paths)
    else:
        attack_paths.extend(
            derive_paths(
                entry_points,
                critical_targets,
                preferred_edges=graph_edges,
                fallback_edges=topology_edges,
                allowed_hosts=mentioned_hosts,
            )
        )

    if not attack_paths and mentioned_hosts:
        attack_paths.append(mentioned_hosts)

    attack_paths = dedupe_paths(expand_scenario_service_paths(attack_paths, topology))
    risk_scores = {
        path_key(path): score_path(path, critical_targets)
        for path in attack_paths
    }

    return {
        "schema_version": "llm-mtd-mulval-policy-v1",
        "scenario_id": scenario_id,
        "entry_points": entry_points,
        "attack_paths": attack_paths,
        "critical_targets": critical_targets,
        "path_risk_scores": risk_scores,
        "attacker_strategy_space": [
            {
                "entry_node": path[0] if path else None,
                "pivot_sequence": path[1:-1],
                "target_asset": path[-1] if path else None,
                "expected_damage_weight": risk_scores[path_key(path)],
                "candidate_defender_actions": candidate_defenses(path, topology),
            }
            for path in attack_paths
        ],
    }


def extract_host_mentions(text, hosts):
    found = []
    for host in hosts:
        pattern = HOST_TOKEN_TEMPLATE.format(re.escape(host))
        if re.search(pattern, text):
            found.append(host)

    positions = {}
    for host in found:
        match = re.search(HOST_TOKEN_TEMPLATE.format(re.escape(host)), text)
        positions[host] = match.start() if match else 0
    return sorted(found, key=lambda host: positions[host])


def extract_attacker_locations(text, hosts):
    host_set = set(hosts)
    result = []
    for match in ATTACKER_PATTERN.finditer(text):
        host = clean_atom(match.group(1))
        if host in host_set and host not in result:
            result.append(host)
    return result


def extract_attack_goals(text, hosts):
    host_set = set(hosts)
    result = []
    for match in ATTACK_GOAL_PATTERN.finditer(text):
        host = clean_atom(match.group(1))
        if host in host_set and host not in result:
            result.append(host)
    if result:
        return result

    first_exec_code = EXEC_CODE_PATTERN.search(text)
    if first_exec_code:
        host = clean_atom(first_exec_code.group(1))
        if host in host_set:
            result.append(host)
    return result


def extract_hacl_edges(text, hosts):
    host_set = set(hosts)
    edges = []
    for match in HACL_PATTERN.finditer(text):
        src = clean_atom(match.group(1))
        dst = clean_atom(match.group(2))
        if src in host_set and dst in host_set:
            edges.append((src, dst))
    return edges


def extract_expected_paths(text, hosts):
    host_set = set(hosts)
    paths = []
    for match in EXPECTED_PATH_PATTERN.finditer(text):
        tokens = [clean_atom(token.strip()) for token in match.group(1).split(",")]
        path = [token for token in tokens[1:] if token in host_set]
        if len(path) >= 2:
            paths.append(path)
    return paths


def derive_paths(entry_points, targets, preferred_edges, fallback_edges, allowed_hosts):
    paths = []
    allowed = set(allowed_hosts or [])
    edge_sets = [preferred_edges]
    if fallback_edges:
        edge_sets.append(fallback_edges)

    for edges in edge_sets:
        if not edges:
            continue
        for entry in entry_points:
            for target in targets:
                path = shortest_path(entry, target, edges, allowed_hosts=allowed)
                if path:
                    paths.append(path)
        if paths:
            break
    return paths


def expand_scenario_service_paths(paths, topology):
    """Expand a direct gateway/cloud path through its scenario-assigned worker.

    MulVAL models reachability and can choose a direct gateway edge. The game models
    the application route. Expansion occurs only when the topology explicitly labels
    a unique ``gateway_to_worker_for_<sensor>`` route and that worker reaches the
    same cloud target.
    """
    connectivity = topology.get("connectivity", [])
    edges = {(edge.get("src"), edge.get("dst")) for edge in connectivity}
    expanded = []
    for path in paths:
        mapped = list(path)
        if len(mapped) == 3 and mapped[0].startswith("sen") and mapped[-1].startswith("cloud_"):
            entry, gateway, target = mapped
            route_reason = f"gateway_to_worker_for_{entry}"
            workers = [
                edge.get("dst")
                for edge in connectivity
                if edge.get("src") == gateway and edge.get("reason") == route_reason
            ]
            if len(workers) == 1 and (workers[0], target) in edges:
                mapped = [entry, gateway, workers[0], target]
        expanded.append(mapped)
    return expanded


def shortest_path(start, target, edges, allowed_hosts=None):
    allowed_hosts = set(allowed_hosts or [])
    adjacency = {}
    for src, dst in edges:
        if allowed_hosts and (src not in allowed_hosts or dst not in allowed_hosts):
            continue
        adjacency.setdefault(src, []).append(dst)

    queue = deque([(start, [start])])
    seen = {start}
    while queue:
        node, path = queue.popleft()
        if node == target:
            return path
        for neighbor in adjacency.get(node, []):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    return None


def score_path(path, critical_targets):
    if not path:
        return 0.0
    score = 0.50 + (max(len(path) - 1, 0) * 0.08)
    if path[-1] in set(critical_targets):
        score += 0.08
    return round(min(score, 0.95), 2)


def candidate_defenses(path, topology):
    actions = []
    host_roles = {host["id"]: host.get("role") for host in topology.get("hosts", [])}

    if not path:
        return actions

    entry = path[0]
    if entry.startswith("sen"):
        actions.append({"action": "quarantine_sensor", "target": entry})
        actions.append({"action": "rate_limit", "target": entry, "kbps": 128})

    if "sen6" in path:
        via = "s_edge2" if "edge3_gw" in path else "s_edge3"
        actions.append({"action": "reroute_traffic", "target": "sen6", "via": via})

    for node in path[1:-1]:
        role = host_roles.get(node, "")
        if role in ("edge_gateway", "edge_worker"):
            actions.append({"action": "isolate_sensor", "target": node})

    if path[-1].startswith("cloud_"):
        actions.append({"action": "rate_limit", "target": path[0], "kbps": 64})

    return dedupe_actions(actions)


def dedupe_actions(actions):
    seen = set()
    result = []
    for action in actions:
        key = json.dumps(action, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def dedupe_paths(paths):
    seen = set()
    result = []
    for path in paths:
        key = tuple(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def path_key(path):
    return "->".join(path)


def clean_atom(value):
    return value.strip().strip("'")


def main():
    parser = argparse.ArgumentParser(description="Convert MulVAL output to policy JSON.")
    parser.add_argument("--graph", required=True, help="AttackGraph.xml, AttackGraph.txt, or scenario .P path.")
    parser.add_argument("--topology", default=None, help="Optional topology JSON from topology_export.py.")
    parser.add_argument("--scenario-id", default="mulval_scenario")
    parser.add_argument("--output", "-o", required=True, help="Output policy JSON path.")
    args = parser.parse_args()

    topology = load_topology(args.topology)
    policy_graph = parse_policy_graph(args.graph, topology=topology, scenario_id=args.scenario_id)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(policy_graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
