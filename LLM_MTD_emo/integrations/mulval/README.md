# MulVAL Integration

This integration keeps MulVAL as an attacker-strategy generator for the
defender policy layer. MulVAL should feed `cloud_policy` with structured attack
paths and candidate defense actions. It should not call Ryu, OVS, or Mininet
directly.

## Layout

- `../attack_scenarios.json`: shared MulVAL-to-Caldera scenario registry.
- `assets/scenario_base.P`: first hand-written validation scenario.
- `assets/topology_export.py`: exports the abstract four-layer topology from
  `network_model.py`.
- `assets/mulval_input_builder.py`: renders topology JSON into MulVAL `.P`
  facts.
- `assets/parser.py`: converts MulVAL text/XML output, or a small `.P`
  scenario, into policy-friendly JSON.
- `outputs/`: generated `.P`, `AttackGraph.*`, and parsed policy JSON outputs.

## Four-Layer Mapping

The exporter maps the project into these MulVAL-facing roles:

- Sensor layer: `sen1` through `sen10`.
- Edge layer: `edge*_gw` gateways and `edge*_vm_*` workers.
- Cloud layer: `cloud_db`, `cloud_object`, `cloud_metrics`, `cloud_policy`,
  and `cloud_logger`.
- Controller layer: `ryu_controller`, represented as a management visibility
  actor rather than a direct OVS manipulator.

Connectivity is exported from the same static model used by `topology.py`:

- Sensors can reach their configured edge gateway telemetry endpoints.
- `sen6` can reach both `edge2_gw` and `edge3_gw`.
- Gateways can reach their assigned edge workers.
- Edge nodes can reach cloud services through the SDN core.
- Ryu management visibility is recorded as project annotations.

The first version uses abstract weaknesses instead of CVEs:

- `sensor_node_exposed`
- `weak_auth_on_gateway`
- `worker_service_exposed`
- `cloud_api_reachable`
- `controller_management_reachable`

## Scenario Registry

Use `integrations/attack_scenarios.json` as the common bridge between MulVAL
paths and Caldera live attack runs. Each scenario contains:

- `scenario_id`
- `entry_node`
- `mulval_path`
- `target_asset`
- `live_attack_type`
- `success_criteria`
- `candidate_defender_actions`

The success criteria checklist gives a fair comparison point across attacker
strategies: gateway seen, worker request increase, cloud counter change, attack
effect threshold, and defense success.

## Validate Manually First

Start with the hand-written small scenario:

```bash
cd LLM_MTD_emo
mkdir -p integrations/mulval/outputs/base_edge2_path
cp integrations/mulval/assets/scenario_base.P integrations/mulval/outputs/base_edge2_path/input.P
```

Run MulVAL from the output directory so generated files stay isolated:

```bash
cd integrations/mulval/outputs/base_edge2_path
graph_gen.sh input.P
```

Depending on your MulVAL installation and flags, this may create files such as:

- `AttackGraph.txt`
- `AttackGraph.xml`
- CSV files
- PDF or DOT graph files for visual debugging

For this project, the important artifact is the machine-readable graph, usually
`AttackGraph.xml` or `AttackGraph.txt`.

## Export The Full Abstract Topology

From the project root:

```bash
python integrations/mulval/assets/topology_export.py \
  --json-output integrations/mulval/outputs/topology_auto.json \
  --output integrations/mulval/outputs/topology_auto.P
```

Then run MulVAL on the generated `.P` in an output subdirectory:

```bash
mkdir -p integrations/mulval/outputs/auto_full_topology
cp integrations/mulval/outputs/topology_auto.P integrations/mulval/outputs/auto_full_topology/input.P
cd integrations/mulval/outputs/auto_full_topology
graph_gen.sh input.P
```

## Parse MulVAL Output For cloud_policy

Parse the hand-written scenario, useful before MulVAL is installed:

```bash
cd LLM_MTD_emo
python integrations/mulval/assets/parser.py \
  --graph integrations/mulval/assets/scenario_base.P \
  --scenario-id base_edge2_path \
  --output integrations/mulval/outputs/base_edge2_policy.json
```

Parse actual MulVAL output. If your MulVAL run only creates `AttackGraph.txt`,
use that path here. The parser also falls back from a missing sibling
`AttackGraph.xml` to `AttackGraph.txt`.

```bash
python integrations/mulval/assets/parser.py \
  --graph integrations/mulval/outputs/base_edge2_path/AttackGraph.txt \
  --topology integrations/mulval/outputs/topology_auto.json \
  --scenario-id base_edge2_path \
  --output integrations/mulval/outputs/base_edge2_policy.json
```

Example policy JSON shape:

```json
{
  "scenario_id": "base_edge2_path",
  "entry_points": ["sen4"],
  "attack_paths": [
    ["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]
  ],
  "critical_targets": ["cloud_db"],
  "path_risk_scores": {
    "sen4->edge2_gw->edge2_vm_s4->cloud_db": 0.82
  },
  "attacker_strategy_space": [
    {
      "entry_node": "sen4",
      "pivot_sequence": ["edge2_gw", "edge2_vm_s4"],
      "target_asset": "cloud_db",
      "expected_damage_weight": 0.82,
      "candidate_defender_actions": [
        {"action": "quarantine_sensor", "target": "sen4"},
        {"action": "rate_limit", "target": "sen4", "kbps": 128},
        {"action": "isolate_sensor", "target": "edge2_gw"},
        {"action": "isolate_sensor", "target": "edge2_vm_s4"}
      ]
    }
  ]
}
```

The next integration step is to let `cloud_policy` read this JSON and add the
`attacker_strategy_space` entries to its game-policy context before selecting a
Ryu-owned defense intent.
