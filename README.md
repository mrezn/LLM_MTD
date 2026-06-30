# LLM_MTD_modular

LLM-guided Moving Target Defense research platform for an emulated edge-cloud
IoT network. The project combines Containernet, Ryu SDN, MITRE Caldera, MulVAL,
evolutionary game theory, an Ollama-backed defender, evaluation tools, and a
live dashboard in one modular codebase.

> This repository is intended for controlled research environments. Run attack
> abilities only on systems and networks you own or are explicitly authorized
> to test.

## What the system does

For each stage, the platform:

1. Collects workload, attack, QoS, and Ryu controller evidence.
2. Builds an evidence-based attack-path state.
3. Filters feasible attacker and defender strategies.
4. Computes attacker/defender utilities and updates EGT populations.
5. Dispatches the selected attacker through Caldera.
6. Selects a defense using either EGT alone or EGT plus an LLM.
7. Applies the MTD action through Ryu and verifies its effects.
8. Stores transition, decision, latency, QoS, and validity records.
9. Produces CSV tables, figures, audit output, and dashboard data.

The default research scenario follows:

```text
sen4 -> edge2_gw -> edge2_vm_s4 -> cloud_db
```

Path progression is evidence-based:

| Stage | Meaning | Typical evidence |
|---:|---|---|
| 0 | Entry | No confirmed gateway progression |
| 1 | Gateway | `gateway_seen` |
| 2 | Worker | `worker_seen` or worker request evidence |
| 3 | Cloud | Explicit cloud/exfiltration evidence or a valid attack-correlated baseline delta |

## Architecture

```text
                         Host machine

  Ollama :11434       Caldera :8888       Dashboard :8088
         |                  |                    |
         |           Dispatch bridge :9000      |
         |                  |                    |
         +---------+--------+--------------------+
                   |
          game/ and eval/ orchestration
                   |
      +------------+-------------------+
      |                                |
  attacker/                       defender/
  Caldera + MulVAL                LLM + action guards
      |                                |
      +------------+-------------------+
                   |
            environment/
       Ryu :8080/:6653 + Containernet
                   |
   sensors -> edge gateways -> workers -> cloud
```

## Repository layout

| Directory | Responsibility |
|---|---|
| `environment/` | Containernet topology, static network model, Ryu controller, environment adapters, and Dockerized sensor/edge/cloud services |
| `attacker/` | Caldera dispatch bridge, attack abilities, adversaries, scenarios, MulVAL topology export and policy parsing |
| `defender/` | LLM client, prompts, response parsing, strategy validation, fallback logic, action adapters, and stage summarization |
| `game/` | State builder, strategy registry/filtering, EGT utility model, population dynamics, selection, execution, and transition logs |
| `eval/` | Hybrid LLM runner, baselines, normalized state, metrics, schemas, trial runner, report tables, and figures |
| `dashboard/` | HTTP proxy and live browser dashboard |
| `scripts/` | Docker image build and Caldera resource synchronization/checking |
| `tools/` | Post-fix audit and JSONL-to-text conversion utilities |
| `tests/` | Unit and mocked integration regression tests |
| `outputs/` | Raw stage records, traces, summaries, audits, report CSVs, and figures |

Important files:

| File | Purpose |
|---|---|
| `game/strategy_space.json` | Attacker and defender strategy definitions |
| `attacker/scenarios/attack_scenarios.json` | Scenario registry and seed paths |
| `attacker/mulval/outputs/base_edge2_policy.json` | Default generated MulVAL policy |
| `eval/configs/models/hybrid_game_llm.yaml` | Live hybrid EGT plus Ollama configuration |
| `DEPLOYMENT.md` | Extended operational and troubleshooting guide |
| `systemmodel.txt` | Detailed proposed system model and algorithm description |

## Requirements

The full deployment is designed for Linux, normally Ubuntu 20.04 or newer.

- Python 3.9+
- Docker Engine
- Containernet/Mininet
- Open vSwitch
- Ryu with OpenFlow 1.3 support
- MITRE Caldera 4.x
- Ollama
- MulVAL, only when regenerating attack graphs

Python packages used by the main workflows include `pytest`, `PyYAML`,
`requests`, `pandas`, `numpy`, `matplotlib`, and Ryu's dependencies.

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd LLM_MTD_modular

python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install pytest pyyaml requests pandas numpy matplotlib ryu
```

Containernet, Docker, OVS, Caldera, and Ollama must also be installed according
to their upstream installation guides.

## Configuration

Create `.env` in the repository root. Do not commit real API keys.

```bash
CALDERA_BASE_URL=http://127.0.0.1:8888
CALDERA_API_KEY=replace_with_your_key
CALDERA_USERNAME=red
CALDERA_PASSWORD=replace_with_your_password
CALDERA_GROUP=red

STRATEGY_ATTACKER_DISPATCH_URL=http://127.0.0.1:9000/caldera/dispatch
STRATEGY_CLOUD_LOGGER_URL=

DEFENDER_RYU_ACTION_URL=http://127.0.0.1:8080/mtd/action
DEFENDER_CORE_URL=http://127.0.0.1:8088/core

DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8088
```

Load it in each terminal that needs the variables:

```bash
set -a
source .env
set +a
```

The Ollama model and timeout are configured in
`eval/configs/models/hybrid_game_llm.yaml`. The current default is
`gemma4:e4b`, with JSON mode, a 120-second per-request timeout, retries, and a
30-minute keep-alive.

## Full deployment

The startup order matters. Ryu must be active before the topology; Caldera must
be active before its resources and agents are synchronized.

### 1. Build the service images

The topology uses eight project images.

```bash
bash scripts/build_images.sh
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^llm-mtd-emo/'
```

Expected repositories:

```text
llm-mtd-emo/sensor-node
llm-mtd-emo/edge-gateway
llm-mtd-emo/edge-worker
llm-mtd-emo/cloud-db
llm-mtd-emo/cloud-object
llm-mtd-emo/cloud-metrics
llm-mtd-emo/cloud-policy
llm-mtd-emo/cloud-logger
```

### 2. Start Ollama

```bash
ollama serve
```

In another terminal:

```bash
ollama pull gemma4:e4b
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

### 3. Start Caldera

From the Caldera installation directory:

```bash
python3 server.py --insecure
```

Caldera should be available at `http://127.0.0.1:8888`.

Synchronize this project's abilities and adversaries:

```bash
cd /path/to/LLM_MTD_modular
set -a; source .env; set +a

python3 scripts/sync_caldera_resources.py \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY"
```

Verify the primary sensor adversary:

```bash
python3 scripts/check_caldera_adversary.py \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY" \
  --adversary-id c9bdece2-df3a-4c3f-bb70-31ff46bb7351
```

The result must report `exists: true` and `has_abilities: true`.

### 4. Start Ryu

In terminal 1:

```bash
cd /path/to/LLM_MTD_modular
ryu-manager --observe-links environment/sdn/controller_app.py
```

Ryu exposes:

- OpenFlow: `127.0.0.1:6653`
- REST status: `http://127.0.0.1:8080/mtd/status`
- REST metrics: `http://127.0.0.1:8080/mtd/metrics`
- MTD actions: `http://127.0.0.1:8080/mtd/action`

Before starting the topology, verify the controller process is reachable:

```bash
curl -s http://127.0.0.1:8080/mtd/status | python3 -m json.tool
```

### 5. Start the Containernet topology

If an earlier topology failed, run full Mininet cleanup manually **before**
restarting Ryu:

```bash
sudo mn -c
```

`mn -c` may terminate controller-related processes, so start Ryu again after
that command. The topology script itself performs targeted cleanup and does not
automatically call `mn -c`.

In terminal 2, while Ryu is already running:

```bash
cd /path/to/LLM_MTD_modular
sudo python3 environment/network/topology.py
```

The topology creates five OVS switches, ten sensors, edge gateways/workers, and
five cloud services. It opens the `mininet>` CLI.

Verify from the Mininet CLI:

```text
mininet> nodes
mininet> net
mininet> pingall
```

Verify that Ryu sees all switches from another terminal:

```bash
curl -s http://127.0.0.1:8080/mtd/status | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("switches:", len(d.get("switches", [])))
print("known hosts:", len(d.get("known_hosts", {})))
'
```

Expected switch count: `5`.

### 6. Start a Caldera agent in a target node

Find the host address reachable from Docker containers:

```bash
HOST_IP=$(ip -4 addr show docker0 | awk '/inet / {print $2}' | cut -d/ -f1)
echo "$HOST_IP"
```

Download the Linux sandcat agent from Caldera, copy it to `mn.sen4`, and start
it. Depending on the Caldera version, the downloaded filename/header may differ;
the following is the common Go-agent flow:

```bash
curl -s -o /tmp/sandcat.go \
  http://127.0.0.1:8888/file/download \
  -H 'Platform: linux' \
  -H 'file: sandcat.go'

sudo docker cp /tmp/sandcat.go mn.sen4:/tmp/sandcat.go
sudo docker exec -d mn.sen4 bash -c "
  chmod +x /tmp/sandcat.go &&
  /tmp/sandcat.go -server http://${HOST_IP}:8888 -group red -v \
    > /tmp/caldera_agent.log 2>&1
"
```

Wait several seconds and verify the agent:

```bash
curl -s http://127.0.0.1:8888/api/v2/agents \
  -H "KEY: $CALDERA_API_KEY" \
  | python3 -c '
import json, sys
for agent in json.load(sys.stdin):
    print(agent.get("host"), agent.get("status"), agent.get("paw"), agent.get("group"))
'
```

At least one relevant agent should be `alive`. Inspect failures with:

```bash
sudo docker exec mn.sen4 cat /tmp/caldera_agent.log
```

For later attack stages, deploy an agent to the corresponding worker such as
`mn.edge2_vm_s4` using the same process.

### 7. Start the Caldera dispatch bridge

Obtain the host-mapped ports for cloud logging and policy services:

```bash
LOGGER_PORT=$(sudo docker port mn.cloud_logger 8000/tcp | head -1 | cut -d: -f2)
POLICY_PORT=$(sudo docker port mn.cloud_policy 8000/tcp | head -1 | cut -d: -f2)
echo "logger=$LOGGER_PORT policy=$POLICY_PORT"
```

In terminal 3:

```bash
cd /path/to/LLM_MTD_modular
set -a; source .env; set +a

python3 attacker/engine/caldera_dispatch_bridge.py \
  --port 9000 \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY" \
  --group red \
  --logger-url "http://127.0.0.1:${LOGGER_PORT}/attack/event" \
  --policy-url "http://127.0.0.1:${POLICY_PORT}/context"
```

Always use the complete dispatch URL:

```text
http://127.0.0.1:9000/caldera/dispatch
```

Verify the bridge:

```bash
curl -s http://127.0.0.1:9000/health | python3 -m json.tool
```

### 8. Start the dashboard

In terminal 4:

```bash
cd /path/to/LLM_MTD_modular
python3 dashboard/dashboard_server.py
```

Open:

```text
http://127.0.0.1:8088/Dashboard.html
```

The dashboard exposes live core data, Ryu status/metrics, game history,
evaluation summaries, and decision traces. Use `/decision-trace?source=live`,
`source=eval`, or `source=both` to select the trace source.

## Verify the full stack

```bash
curl -sf http://127.0.0.1:11434/api/tags >/dev/null && echo 'Ollama OK'
curl -sf http://127.0.0.1:8888/api/v2/agents -H "KEY: $CALDERA_API_KEY" >/dev/null && echo 'Caldera OK'
curl -sf http://127.0.0.1:9000/health >/dev/null && echo 'Bridge OK'
curl -sf http://127.0.0.1:8080/mtd/status >/dev/null && echo 'Ryu OK'
curl -sf http://127.0.0.1:8088/core >/dev/null && echo 'Dashboard/core OK'
```

Check Docker services:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep 'mn\.'
```

## Run the system

### Pure evolutionary game stage

This path uses EGT population/utility selection without invoking the LLM:

```bash
python3 -m game.strategy_runtime \
  --scenario-id sen4_edge2_clouddb \
  --execute-attacker \
  --execute-defender \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch \
  --ryu-action-url http://127.0.0.1:8080/mtd/action \
  --timeout-seconds 30
```

Confirm attacker dispatch:

```bash
python3 -m game.strategy_runtime \
  --scenario-id sen4_edge2_clouddb \
  --execute-attacker \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch \
  --timeout-seconds 30 \
  | python3 -c '
import json, sys
d = json.load(sys.stdin)
attack = d["execution"]["attacker"]
print("status:", attack.get("status"))
print("HTTP:", (attack.get("post_result") or {}).get("status"))
'
```

Expected: `status: dispatched` and `HTTP: 202`.

Useful diagnostics:

```bash
python3 -m game.strategy_runtime \
  --scenario-id sen4_edge2_clouddb \
  --explain-attacker-filter \
  --allow-scenario-seed-attackers \
  --no-save-population \
  --no-stage-log \
  --no-decision-trace-log
```

Use `--strict-preconditions` for paper mode. Strict mode requires an exact
MulVAL path and fails clearly when it is unavailable. Non-strict development
mode may use a matching scenario seed without borrowing the wrong MulVAL risk.

### Hybrid EGT plus LLM stage

This path uses EGT to construct/rank the feasible strategy set and Ollama to
choose the final defender action:

Resolve the host-mapped cloud service ports in the same shell used for the
evaluation command:

```bash
LOGGER_PORT=$(sudo docker port mn.cloud_logger 8000/tcp | head -1 | cut -d: -f2)
POLICY_PORT=$(sudo docker port mn.cloud_policy 8000/tcp | head -1 | cut -d: -f2)

python3 -m eval.cli run-stage \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --cloud-logger-url "http://127.0.0.1:${LOGGER_PORT}/attack/event" \
  --cloud-policy-url "http://127.0.0.1:${POLICY_PORT}/context"
```

The current hybrid configuration executes both attacker and defender by
default. Use explicit dry-run switches when you do not want live actions:

```bash
python3 -m eval.cli run-stage \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --no-execute-attacker \
  --no-execute-defender
```

Ollama is called for the defender decision and stage summary, so a stage may
take several minutes on CPU-only machines. Latency, retries, timeout state, and
fallback use are stored in the output records.

### Evaluation trial

```bash
python3 -m eval.cli run-trial \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --seed 42 \
  --output-root outputs
```

Available model configurations:

| Configuration | Purpose |
|---|---|
| `hybrid_game_llm.yaml` | EGT strategy context plus Ollama defender |
| `llm_only.yaml` | LLM-focused comparison |
| `baseline_game.yaml` | Game-theory baseline |
| `baseline_rule.yaml` | Rule baseline |

## Output files

| Path | Content |
|---|---|
| `game/stage_history.jsonl` | Direct game-stage transitions |
| `game/decision_trace.jsonl` | Direct game decisions |
| `game/population_state.json` | Active and global EGT populations |
| `outputs/raw/live_stage_history.jsonl` | Hybrid evaluator transitions |
| `outputs/raw/live_decision_trace.jsonl` | Hybrid evaluator decisions |
| `outputs/raw/stage_summaries.jsonl` | Canonical dashboard/report summaries |
| `outputs/raw/live_population_state.json` | Evaluator population state |
| `outputs/traces/` | LLM request/response traces |
| `outputs/figures/tables/` | Report CSV tables |
| `outputs/figures/figures/` | Figure images and plotting CSVs |
| `outputs/audit/` | Automated post-fix audit output |

Raw JSONL is the canonical audit source. Generate an optional readable log:

```bash
python3 tools/jsonl_to_logs_txt.py \
  --input outputs/raw/stage_summaries.jsonl \
  --output outputs/logs.txt
```

## Generate reports

After collecting baseline and LLM stages:

```bash
python3 -m eval.cli build-report \
  --eval-stage-history outputs/raw/live_stage_history.jsonl \
  --eval-decision-trace outputs/raw/live_decision_trace.jsonl \
  --eval-stage-summaries outputs/raw/stage_summaries.jsonl \
  --eval-population outputs/raw/live_population_state.json \
  --baseline-stage-history game/stage_history.jsonl \
  --baseline-decision-trace game/decision_trace.jsonl \
  --baseline-population game/population_state.json \
  --output-dir outputs/figures \
  --include-debug-stages
```

The report loader separates baseline and LLM sources, filters paper-valid
stages when requested, and produces alignment, latency, QoS, controller,
population, and candidate-tradeoff outputs.

Run the issue audit:

```bash
python3 tools/audit_post_fix_issues.py
```

Output:

```text
outputs/audit/post_fix_issue_audit.json
```

## Regenerate the MulVAL policy

The repository includes a usable policy, so this is optional.

```bash
python3 attacker/mulval/topology_export.py \
  --output attacker/mulval/outputs/topology_auto.P
```

Run MulVAL from a directory containing both `topology_auto.P` and `goals.txt`:

```bash
cp attacker/mulval/outputs/topology_auto.P /tmp/
cp attacker/mulval/outputs/goals.txt /tmp/
cd /tmp

export MULVALROOT=/opt/mulval
export PATH="$PATH:$MULVALROOT/bin:$MULVALROOT/utils"
graph_gen.sh topology_auto.P
```

Parse the result from the project root:

```bash
python3 attacker/mulval/parser.py \
  --graph /tmp/AttackGraph.txt \
  --topology attacker/mulval/outputs/topology_auto.json \
  --scenario-id sen4_edge2_clouddb \
  --output attacker/mulval/outputs/base_edge2_policy.json
```

The parser expands the scenario-defined gateway-to-worker service route when
MulVAL reports only a direct reachability shortcut. It recomputes the four-hop
risk rather than treating a three-hop risk as an exact match.

## Testing

Run all pure unit and mocked integration tests:

```bash
python3 -m pytest -q
```

Live Mininet, Ryu, Caldera, Docker, and Ollama checks require the full stack and
are intentionally separate from the regular test suite.

## Teardown

Exit the Mininet CLI:

```text
mininet> exit
```

Then stop remaining topology containers and clean network state:

```bash
docker ps --format '{{.Names}}' \
  | grep '^mn\.' \
  | while read -r name; do docker stop "$name"; done

sudo mn -c
```

Stop the Ryu, bridge, dashboard, Ollama, and Caldera processes in their
terminals. To start a new independent EGT experiment, optionally archive and
remove population files:

```bash
mkdir -p outputs/archive
cp game/population_state.json outputs/archive/ 2>/dev/null || true
cp outputs/raw/live_population_state.json outputs/archive/ 2>/dev/null || true
rm -f game/population_state.json outputs/raw/live_population_state.json
```

## Troubleshooting

### Ryu is not reachable on port 6653

Start Ryu before topology creation:

```bash
ryu-manager --observe-links environment/sdn/controller_app.py
sudo python3 environment/network/topology.py
```

If `sudo mn -c` was used, restart Ryu afterward.

### `RTNETLINK answers: File exists`

This indicates stale OVS interfaces or bridges. The topology performs targeted
cleanup. If it persists, stop the topology, run `sudo mn -c`, restart Ryu, and
run the topology again.

### Docker socket permission denied

Add the current user to the Docker group, then start a new login session:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker ps
```

### Attacker dispatch returns 404

Use the full bridge endpoint:

```text
http://127.0.0.1:9000/caldera/dispatch
```

### Attacker dispatch returns 400

Check that the selected adversary exists and has abilities:

```bash
python3 scripts/sync_caldera_resources.py \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY"
```

Then verify an alive agent exists for the attack entry host.

### Caldera operation has zero links

- Confirm the relevant agent is alive.
- Confirm the custom adversary has non-empty `atomic_ordering`.
- Check `/tmp/caldera_agent.log` in the target container.
- Ensure the agent can reach the host's Caldera address.

### Cloud logger or cloud policy is unreachable

Do not use internal `10.0.10.x` addresses from host processes. Use the
host-mapped ports returned by `docker port`. Ryu evidence remains usable when
cloud event posting is disabled, but dashboard/cloud evidence may be incomplete.

### LLM calls are slow or fall back

```bash
curl -s http://127.0.0.1:11434/api/tags | python3 -m json.tool
```

Review `timeout_seconds`, `max_retries`, `ollama_keep_alive`, and `model_name`
in the selected model YAML. CPU-only model execution can take several minutes.
Fallback reason and latency are recorded under `llm` in each stage result.

### Dashboard does not show the latest LLM output

Confirm `outputs/raw/stage_summaries.jsonl` is being updated and use the eval
trace source. Restart `dashboard/dashboard_server.py` after changing output
paths or dashboard configuration.

### Report generation cannot infer the baseline root

Run from the repository root and provide the baseline explicitly if needed:

```bash
python3 -m eval.cli build-report \
  --eval-stage-history outputs/raw/live_stage_history.jsonl \
  --eval-stage-summaries outputs/raw/stage_summaries.jsonl \
  --baseline-stage-history game/stage_history.jsonl \
  --output-dir outputs/figures
```

## Research validity notes

- Background cloud workload alone cannot set `cloud_seen=true`.
- No-attack stage 0 forces observation and cannot execute a disruptive defense.
- Dry-run, warmup, fallback, no-attack, and inconsistent stages are excluded
  from paper-valid learning according to explicit reasons.
- Defense execution, controller confirmation, semantic effect confirmation,
  and security success are recorded separately.
- Per-stage flow/meter deltas are separated from cumulative controller counts.
- Empty payoff matrices do not update or persist EGT populations.
- Active and global populations are stored separately with a nonzero floor.
- Raw audit data is retained; reports add derived fields without deleting it.

## License and citation

Add the repository's chosen license in a `LICENSE` file before public release.
For academic use, add the paper citation or BibTeX entry here when available.

For deeper deployment details and operational examples, see
[`DEPLOYMENT.md`](DEPLOYMENT.md).
