# LLM_MTD_modular — Deployment & Run Guide

This guide walks through every step to bring the full system up from scratch:
Docker images, Ollama LLM, Ryu SDN controller, Mininet/Containernet topology,
Caldera attacker framework with agent deployment, MulVAL attack-graph generation,
game engine, LLM-backed defender, evaluation harness, and dashboard.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Host Machine                             │
│                                                                 │
│  Ollama (:11434)       Caldera (:8888)     Dashboard (:8088)   │
│  Ryu SDN (:8080/:6653) Caldera Bridge (:9000/caldera/dispatch) │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            Mininet / Containernet                       │   │
│  │                                                         │   │
│  │  Edge 1 (10.0.1.x)  Edge 2 (10.0.2.x)  Edge 3 (10.0.3.x) │
│  │  sen1-3, gw, vms    sen4-6, gw, vms    sen7-10, gw, vms   │
│  │                                                         │   │
│  │  Cloud (10.0.10.x): db, object, metrics, policy, logger │   │
│  │                                                         │   │
│  │  5 OVS switches: s_edge1, s_edge2, s_edge3, s_core,    │   │
│  │                  s_cloud (all managed by Ryu)           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Subnet assignments (from `network_model.py`):**

| Domain | Subnet       | Key nodes |
|--------|--------------|-----------|
| Edge 1 | 10.0.1.0/24  | edge1\_gw=10.0.1.1, sen1=.11, sen2=.12, sen3=.13 |
| Edge 2 | 10.0.2.0/24  | edge2\_gw=10.0.2.1, sen4=.14, sen5=.15, sen6=.16 |
| Edge 3 | 10.0.3.0/24  | edge3\_gw=10.0.3.1, sen7=.17, sen8=.18, sen9=.19, sen10=.20 |
| Cloud  | 10.0.10.0/24 | cloud\_db=.10, cloud\_object=.11, cloud\_metrics=.12, cloud\_policy=.13, cloud\_logger=.14 |

> **Note:** sen6 is dual-homed (10.0.2.16 on s\_edge2 and 10.0.3.16 on s\_edge3).

---

## System Requirements

| Requirement            | Version / Notes |
|------------------------|-----------------|
| OS                     | Ubuntu 20.04 / 22.04 (or WSL2 on Windows) |
| Python                 | 3.9 or newer |
| Docker                 | 20.10+ with BuildKit enabled |
| Mininet / Containernet | Containernet fork (Mininet with Docker support) |
| Open vSwitch           | 2.13+ (installed automatically with Containernet) |
| Ryu SDN Framework      | `pip install ryu` |
| Caldera                | v4.x, running on `localhost:8888` |
| Ollama                 | Latest, running on `localhost:11434` |
| MulVAL                 | Required only for attack-graph regeneration (Step 10) |

**Install Python dependencies:**

```bash
cd LLM_MTD_modular/
pip install ryu requests pyyaml openai anthropic
```

---

## Step 1 — Environment Variables

Create `.env` in `LLM_MTD_modular/`:

```bash
# === Caldera (attacker) ===
CALDERA_BASE_URL=http://127.0.0.1:8888
CALDERA_API_KEY=your_caldera_api_key_here
CALDERA_USERNAME=red
CALDERA_PASSWORD=your_password
CALDERA_GROUP=red
CALDERA_DISPATCH_PORT=9000

# === LLM / Ollama (defender decision) ===
LLM_PROVIDER=ollama
LLM_MODEL_NAME=gemma4:e4b
OLLAMA_BASE_URL=http://127.0.0.1:11434

# Optional cloud provider keys (only needed if not using Ollama)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# === Ryu / environment ===
DEFENDER_RYU_ACTION_URL=http://127.0.0.1:8080/mtd/action
DEFENDER_CORE_URL=http://127.0.0.1:8088/core

# === Game layer ===
# IMPORTANT: include the full path /caldera/dispatch — not just the port
STRATEGY_ATTACKER_DISPATCH_URL=http://127.0.0.1:9000/caldera/dispatch
# Leave empty — the bridge uses its own --logger-url (host-accessible port).
# Do NOT use 10.0.10.x here — those are internal Mininet IPs, unreachable from host.
STRATEGY_CLOUD_LOGGER_URL=

# === Eval layer ===
LLM_MTD_EVAL_CORE_URL=http://127.0.0.1:8088/core
LLM_MTD_EVAL_RYU_STATUS_URL=http://127.0.0.1:8080/mtd/status
LLM_MTD_EVAL_RYU_METRICS_URL=http://127.0.0.1:8080/mtd/metrics
LLM_MTD_EVAL_RYU_ACTION_URL=http://127.0.0.1:8080/mtd/action
LLM_MTD_EVAL_OUTPUT_DIR=outputs

# === Dashboard ===
DASHBOARD_PORT=8088
DASHBOARD_HOST=0.0.0.0
```

Load it before running any component:

```bash
export $(grep -v '^#' .env | xargs)
```

---

## Step 2 — Install and Verify Ollama

Ollama must be running before the eval harness (Step 12) or the LLM defender will fall back to game-theory selection.

**Install Ollama:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Pull the model used by the project:**
```bash
ollama pull gemma4:e4b
```

**Verify Ollama is responding:**
```bash
curl -s http://127.0.0.1:11434/api/generate \
  -d '{
    "model": "gemma4:e4b",
    "prompt": "Return only valid JSON: {\"status\":\"ok\"}",
    "stream": false,
    "format": "json"
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print('response:', d.get('response','<empty>')[:120])"
```

Expected: a short JSON string in the `response` field.

**Test the LLMClient integration:**
```bash
cd LLM_MTD_modular/
python3 -c "
from defender.decision.llm_client import LLMClient
c = LLMClient({
    'provider': 'ollama',
    'model_name': 'gemma4:e4b',
    'base_url': 'http://127.0.0.1:11434',
    'strict_json': True,
    'timeout_seconds': 30,
})
r = c.complete_json('You are a test agent.', 'Return: {\"ok\": true}')
print('provider :', r.provider)
print('model    :', r.model_name)
print('latency  :', round(r.latency_ms), 'ms')
print('response :', r.raw_text[:200])
"
```

If `latency_ms` > 0 and `response` is non-empty, Ollama is wired correctly.

---

## Step 3 — Build Docker Service Images

The topology uses 8 Docker images. Build them from the project root:

```bash
cd LLM_MTD_modular/
bash scripts/build_images.sh
```

Verify all 8 images exist:

```bash
docker images | grep llm-mtd-emo
```

Expected — 8 rows, all tagged `latest`:

```
llm-mtd-emo/sensor-node      latest   ...
llm-mtd-emo/edge-gateway     latest   ...
llm-mtd-emo/edge-worker      latest   ...
llm-mtd-emo/cloud-db         latest   ...
llm-mtd-emo/cloud-object     latest   ...
llm-mtd-emo/cloud-metrics    latest   ...
llm-mtd-emo/cloud-policy     latest   ...
llm-mtd-emo/cloud-logger     latest   ...
```

---

## Step 4 — Start the Ryu SDN Controller

**Terminal 1** — must be running before Mininet starts.

```bash
cd LLM_MTD_modular/
ryu-manager --observe-links environment/sdn/controller_app.py
```

This listens on:
- `6653` — OpenFlow 1.3 (OVS switches connect here)
- `8080` — REST API (MTD actions, status, metrics)

**Verify:**

```bash
curl -s http://127.0.0.1:8080/mtd/status | python3 -m json.tool | head -20
```

Expected: JSON containing `"switches"` and `"active_actions"`. An empty switches list is normal — switches connect after Mininet starts.

---

## Step 5 — Start the Mininet / Containernet Topology

**Terminal 2** — requires root. Ryu must already be running (Step 4).

sudo docker rm -f $(sudo docker ps -a -- filter name=mn. -q) 2>/dev/null; sudo mn -c
```bash

sudo mn -c
sudo ovs-vsctl --if-exists del-br s_edge1
sudo ovs-vsctl --if-exists del-br s_edge2
sudo ovs-vsctl --if-exists del-br s_edge3
sudo ovs-vsctl --if-exists del-br s_core
sudo ovs-vsctl --if-exists del-br s_cloud

cd LLM_MTD_modular/
sudo python3 environment/network/topology.py
```

This script:
1. Verifies Ryu is reachable on port 6653 (exits with instructions if not)
2. Verifies all 8 Docker images are built (exits listing missing images)
3. Creates 5 OVS switches: `s_edge1`, `s_edge2`, `s_edge3`, `s_core`, `s_cloud`
4. Starts 29 containers: 10 sensors, 14 edge nodes (3 gateways + 11 workers), and 5 cloud services
5. Assigns static subnet addresses
6. Drops into the Mininet CLI (`mininet>`)

**Verify inside the Mininet CLI:**

```bash
mininet> nodes
mininet> net
mininet> pingall
```

Some loss across domains is normal before routes converge.

**Verify Ryu now sees all 5 switches:**

```bash
curl -s http://127.0.0.1:8080/mtd/status | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('switches:', len(d.get('switches',[])))"
```

Expected: `switches: 5`

---

## Step 6 — Set Up Caldera (Attacker Framework)

Caldera must be installed and running independently at `http://127.0.0.1:8888`.

### 6a — Start Caldera

```bash
# From your Caldera installation directory:
python3 server.py --insecure
```

Open `http://127.0.0.1:8888` in a browser and log in. Retrieve your API key from the UI under Settings.

### 6b — Import Abilities and Adversaries into Caldera

The project ships 3 ability YAMLs and 3 adversary YAMLs. These must be imported into Caldera.

**Recommended idempotent import (run after every fresh Caldera installation or
when Caldera starts without the custom resources):**

```bash
cd LLM_MTD_modular/
python3 scripts/sync_caldera_resources.py \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY"
```

The command imports abilities before adversaries and verifies that every
adversary has a non-empty ability ordering. It is safe to run more than once.
The file-copy method below remains available when managing Caldera resources
directly through its data directories.

**Preflight check a specific adversary UUID before starting the game:**

```bash
python3 scripts/check_caldera_adversary.py \
  --caldera-url http://127.0.0.1:8888 \
  --api-key "$CALDERA_API_KEY" \
  --adversary-id c9bdece2-df3a-4c3f-bb70-31ff46bb7351
```

Expected: `"exists": true` and `"has_abilities": true`.

**Copy abilities into Caldera's stockpile plugin data directory:**

```bash
CALDERA_DIR=/path/to/caldera     # adjust to your Caldera installation

# Abilities go into the stockpile plugin's ability directories
cp LLM_MTD_modular/attacker/actions/linux/sensor_probe.yml \
   $CALDERA_DIR/plugins/stockpile/data/abilities/discovery/

cp LLM_MTD_modular/attacker/actions/linux/edge_http_abuse.yml \
   $CALDERA_DIR/plugins/stockpile/data/abilities/impact/

cp LLM_MTD_modular/attacker/actions/linux/cloud_db_probe.yml \
   $CALDERA_DIR/plugins/stockpile/data/abilities/discovery/
```

**Copy adversaries into Caldera's stockpile plugin adversary directory:**

```bash
cp LLM_MTD_modular/attacker/adversaries/sensor_to_edge.yml \
   $CALDERA_DIR/plugins/stockpile/data/adversaries/

cp LLM_MTD_modular/attacker/adversaries/edge_to_cloud.yml \
   $CALDERA_DIR/plugins/stockpile/data/adversaries/

cp LLM_MTD_modular/attacker/adversaries/dual_homed_sensor_path.yml \
   $CALDERA_DIR/plugins/stockpile/data/adversaries/
```

**Restart Caldera** after copying so it picks up the new files:

```bash
# In the Caldera terminal: Ctrl-C, then restart
python3 server.py --insecure
```

**Verify adversaries are loaded with abilities:**

```bash
curl -s http://127.0.0.1:8888/api/v2/adversaries \
  -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys, json
for a in json.load(sys.stdin):
    name = a.get('name', '?')
    abilities = len(a.get('atomic_ordering', []))
    print(f'  {name}: {abilities} abilities')
"
```

Expected: `LLM MTD sensor to edge: 2 abilities`, `LLM MTD edge to cloud: 2 abilities`, `LLM MTD dual homed sensor path: 2 abilities`.

> **If abilities count is 0:** The adversary YAML was loaded but the referenced ability
> IDs were not found. Verify the ability YAMLs were copied to the correct directory
> and that their `id` fields match the adversary's `phases` references.
>
> The mapping is:
> - `87b6de2e-fcb6-4f44-9a7e-79af476c7d41` → `sensor_probe.yml`
> - `62e7655e-dc3e-49e2-9564-220d7c15582e` → `edge_http_abuse.yml`
> - `f6b91346-caa4-4046-9c7a-1b7376515b48` → `cloud_db_probe.yml`

### 6c — Deploy a Caldera Agent Inside the Target Docker Node

Without a live Caldera agent running inside `mn.sen4`, Caldera will create operations but nothing will execute (`chain: []`, `links=0`).

**Find your host IP reachable from inside Docker containers:**

```bash
# Docker bridge gateway (most common on Ubuntu):
ip addr show docker0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1
# Typically: 172.17.0.1
```

Note this IP — referred to as `<HOST_IP>` below.

**Deploy the sandcat agent into the sen4 container:**

```bash
# Download the agent
curl -s -o /tmp/sandcat.go \
  "http://127.0.0.1:8888/file/download" \
  -H "Platform: linux" \
  -H "file: sandcat.go"

# Copy into the container
sudo docker cp /tmp/sandcat.go mn.sen4:/tmp/sandcat.go

# Start the agent inside the container (runs in background)
sudo docker exec -d mn.sen4 bash -c "
  chmod +x /tmp/sandcat.go && \
  /tmp/sandcat.go \
    -server http://<HOST_IP>:8888 \
    -group red \
    -v \
    > /tmp/caldera_agent.log 2>&1
"
```

Replace `<HOST_IP>` with the address you found above (e.g., `172.17.0.1`).

**Alternative — use the Python sandcat agent:**

```bash
curl -s -o /tmp/sandcat.py \
  "http://127.0.0.1:8888/file/download" \
  -H "Platform: linux" \
  -H "Filename: sandcat.py"

sudo docker cp /tmp/sandcat.py mn.sen4:/tmp/sandcat.py

sudo docker exec -d mn.sen4 bash -c "
  python3 /tmp/sandcat.py \
    -server http://<HOST_IP>:8888 \
    -group red \
    > /tmp/caldera_agent.log 2>&1
"
```

**Verify the agent registered (allow 10-20 seconds):**

```bash
curl -s http://127.0.0.1:8888/api/v2/agents \
  -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys, json
agents = json.load(sys.stdin)
for a in agents:
    print(f\"host={a.get('host')} status={a.get('status')} paw={a.get('paw')} group={a.get('group')}\")
"
```

Expected: at least one agent with `host=sen4` and `status=alive`.

**If the agent does not appear:**
- Check the log: `sudo docker exec mn.sen4 cat /tmp/caldera_agent.log`
- Verify the host IP is reachable from inside: `sudo docker exec mn.sen4 curl -s http://<HOST_IP>:8888`
- Ensure the `red` group exists in Caldera

---

## Step 7 — Start the Caldera Attacker Dispatch Bridge

**Terminal 3** — bridge between the game engine and Caldera.

First, find the host-mapped ports for cloud\_logger and cloud\_policy:

```bash
LOGGER_PORT=$(sudo docker port mn.cloud_logger 8000/tcp 2>/dev/null | head -1 | cut -d: -f2)
POLICY_PORT=$(sudo docker port mn.cloud_policy 8000/tcp 2>/dev/null | head -1 | cut -d: -f2)
echo "LOGGER_PORT=$LOGGER_PORT  POLICY_PORT=$POLICY_PORT"
```

Start the bridge with host-accessible logger/policy URLs:

```bash
cd LLM_MTD_modular/
python3 attacker/engine/caldera_dispatch_bridge.py \
  --port 9000 \
  --caldera-url http://127.0.0.1:8888 \
  --api-key $CALDERA_API_KEY \
  --group red \
  --logger-url "http://127.0.0.1:${LOGGER_PORT}/attack/event" \
  --policy-url "http://127.0.0.1:${POLICY_PORT}/context"
```

The bridge listens on port 9000. The only dispatch endpoint is `POST /caldera/dispatch`.

**Verify:**

```bash
curl -s http://127.0.0.1:9000/health | python3 -m json.tool
```

Expected: `{"service": "caldera-dispatch-bridge", "caldera_url": "...", "logger_url": "http://127.0.0.1:XXXXX/attack/event", ...}`

> **Critical URL note:** When calling the bridge, the URL must include the full path:
> `http://127.0.0.1:9000/caldera/dispatch` — a POST to the root path returns 404.

> **Logger URL note:** Do NOT use internal Mininet IPs (10.0.10.x) for `--logger-url`.
> Those IPs are inside the Containernet overlay network and are not reachable from the host.
> Use the Docker port-mapped address (`127.0.0.1:XXXXX`) instead.

---

## Step 8 — Start the Dashboard

**Terminal 4.**

```bash
cd LLM_MTD_modular/
python3 dashboard/dashboard_server.py
```

Proxies on `http://0.0.0.0:8088`:

| Path | Proxied to |
|------|------------|
| `/core` | cloud\_metrics `/metrics` |
| `/mtd/status` | Ryu `:8080/mtd/status` |
| `/mtd/metrics` | Ryu `:8080/mtd/metrics` |
| `/decision-trace` | `game/decision_trace.jsonl` |
| `/stage-history` | `game/stage_history.jsonl` |

Open the live dashboard:
```
http://localhost:8088/Dashboard.html
```

---

## Step 9 — Full Stack Health Check

Run this after Steps 4-8 are up:

```bash
cd LLM_MTD_modular/

echo "--- Ollama ---"
curl -sf http://127.0.0.1:11434/api/tags \
  | python3 -c "import sys,json; d=json.load(sys.stdin); [print(' model:', m['name']) for m in d.get('models',[])]" \
  || echo "Ollama not running"

echo "--- Ryu SDN ---"
curl -s http://127.0.0.1:8080/mtd/status | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('switches:', len(d.get('switches',[])), '| active_actions:', len(d.get('active_actions',{})))"

echo "--- Dashboard proxy ---"
curl -s http://127.0.0.1:8088/core | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('core endpoint OK')" 2>/dev/null || echo "not up"

echo "--- Caldera bridge ---"
curl -sf http://127.0.0.1:9000/health && echo " OK" || echo "not up"

echo "--- Caldera agents ---"
curl -s http://127.0.0.1:8888/api/v2/agents -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys,json
agents=json.load(sys.stdin)
alive=[a for a in agents if a.get('status')=='alive']
print(f'{len(alive)} alive agent(s):', [a.get('host') for a in alive])
"

echo "--- Caldera adversaries ---"
curl -s http://127.0.0.1:8888/api/v2/adversaries -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    n = a.get('name','?')
    c = len(a.get('atomic_ordering',[]))
    if 'LLM' in n or 'llm' in n:
        print(f'  {n}: {c} abilities')
"

echo "--- Game stages logged ---"
wc -l game/stage_history.jsonl 2>/dev/null || echo "no stages yet"
```

---

## Step 10 — Generate the MulVAL Attack Graph (Optional)

The game engine reads a pre-generated `attacker/mulval/outputs/base_edge2_policy.json`.
This step regenerates it from the live topology. Skip if MulVAL is not installed — the
existing file works for the `sen4_edge2_clouddb` scenario.

### 10a — Export the topology to JSON and MulVAL .P file

```bash
cd LLM_MTD_modular/
python3 attacker/mulval/topology_export.py \
  --output attacker/mulval/outputs/topology_auto.P
```

This writes three files:
- `attacker/mulval/outputs/topology_auto.json` (abstract topology)
- `attacker/mulval/outputs/topology_auto.P` (MulVAL Prolog input)
- `attacker/mulval/outputs/goals.txt` (MulVAL attack goal)

### 10b — Run MulVAL

```bash
cd /tmp/

# Copy the generated files to the working directory
cp ~/Desktop/najafi/LLM_MTD_modular/attacker/mulval/outputs/topology_auto.P .
cp ~/Desktop/najafi/LLM_MTD_modular/attacker/mulval/outputs/goals.txt .

export MULVALROOT=/opt/mulval
export PATH=$PATH:/opt/mulval/utils:/opt/mulval/bin

graph_gen.sh topology_auto.P

ls -lh AttackGraph.*
```

### 10c — Parse MulVAL output to policy JSON

```bash
cd LLM_MTD_modular/
python3 attacker/mulval/parser.py \
  --graph /tmp/AttackGraph.txt \
  --topology attacker/mulval/outputs/topology_auto.json \
  --scenario-id sen4_edge2_clouddb \
  --output attacker/mulval/outputs/base_edge2_policy.json
```

The parser preserves the scenario's application route. If MulVAL emits the
reachability shortcut `sen4 -> edge2_gw -> cloud_db`, the parser expands it to
`sen4 -> edge2_gw -> edge2_vm_s4 -> cloud_db` only when the exported topology
contains the explicit `gateway_to_worker_for_sen4` assignment and that worker
reaches the same cloud target. The resulting four-hop score is recomputed; the
three-hop risk is never reused as an exact-path score.

Verify:

```bash
python3 -c "
import json
d = json.load(open('attacker/mulval/outputs/base_edge2_policy.json'))
print('scenario_id  :', d.get('scenario_id'))
print('attack_paths :', d.get('attack_paths'))
print('risk_scores  :', d.get('path_risk_scores'))
"
```

For attacker-filter diagnostics in development mode:

```bash
python3 -m game.strategy_runtime \
  --execute-attacker \
  --allow-scenario-seed-attackers \
  --explain-attacker-filter \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch
```

Non-strict mode permits attacker path prefixes backed by the scenario seed.
`--strict-preconditions` is paper mode: it requires an exact full MulVAL path
and fails immediately when that path is absent.

Structured JSONL files under `outputs/raw/` are the canonical audit records.
An optional readable log can be generated without replacing those records:

```bash
python3 tools/jsonl_to_logs_txt.py \
  --input outputs/raw/stage_summaries.jsonl \
  --output outputs/logs.txt
```

---

## Step 11 — Run a Single Game Stage (Manual Test)

With all services up (Steps 4-9), run one attacker/defender decision cycle.

```bash
cd LLM_MTD_modular/
python3 -m game.strategy_runtime \
  --execute-defender \
  --execute-attacker \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch \
  --ryu-action-url http://127.0.0.1:8080/mtd/action \
  --timeout-seconds 30
```

What happens internally:
1. **Build state** — reads `/core`, `/mtd/metrics`, `/mtd/status`, MulVAL policy JSON
2. **Evolutionary step** — updates attacker/defender population distributions (replicator dynamics: eta\_atk=0.18, eta\_def=0.22)
3. **Select pair** — picks dominant attacker and defender strategies by population share
4. **Execute attacker** — POSTs attack plan to `http://127.0.0.1:9000/caldera/dispatch`; bridge finds the live `sen4` agent, creates a Caldera operation, and polls it
5. **Execute defender** — POSTs MTD action to Ryu (`http://127.0.0.1:8080/mtd/action`)
6. **Log** — appends to `game/stage_history.jsonl` and `game/decision_trace.jsonl`

> **Defender selection note:** `strategy_runtime` uses pure game-theory selection
> (highest evolutionary population share), NOT the LLM. The LLM is only invoked via
> the eval harness in Step 12. In early stages D0\_observe typically has the highest
> population share (~38%) and will be selected. This is expected.

**Verify attacker dispatch succeeded:**

```bash
python3 -m game.strategy_runtime \
  --execute-attacker \
  --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch \
  --timeout-seconds 30 \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
atk = d['execution']['attacker']
print('status      :', atk.get('status'))
print('http_status :', (atk.get('post_result') or {}).get('status'))
"
```

Expected: `status: dispatched` and `http_status: 202`.

**Check a Caldera operation was created:**

```bash
curl -s http://127.0.0.1:8888/api/v2/operations \
  -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys, json
ops = json.load(sys.stdin)
for op in ops[-3:]:
    print(f\"name={op.get('name','?')[:40]}  state={op.get('state')}  links={len(op.get('chain',[]))}\")
"
```

Expected: `state=running` or `state=finished` with `links > 0`.

> **If `links=0`:** The operation was created but Caldera found no abilities to run.
> Verify the adversary has abilities: check Step 6b. If the adversary shows 0 abilities,
> the ability YAMLs were not imported correctly.

> **If the bridge response includes `"warnings"` about ad-hoc adversary:** The custom
> adversary was not found in Caldera. Re-import per Step 6b and restart Caldera.

**Check logs:**

```bash
tail -1 game/stage_history.jsonl | python3 -m json.tool | head -30
tail -1 game/decision_trace.jsonl | python3 -m json.tool | head -20
```

---

## Step 12 — LLM-Backed Defender via the Eval Harness

The Ollama-backed defender (`defender/decision/llm_client.py`) is NOT called by
`strategy_runtime`. It is only activated through the eval framework.

### Understanding the two defender paths

| Path | How invoked | Defender selection |
|------|-------------|--------------------|
| `game.strategy_runtime` | Direct CLI | Pure evolutionary game theory (dominant population share) |
| `eval.cli run-stage` | Eval harness | Game theory + **Ollama** selects the final action |

### 12a — Verify Ollama is ready

```bash
time curl -s http://127.0.0.1:11434/api/generate \
  -d "{\"model\":\"gemma4:e4b\",\"prompt\":\"You are an MTD defender. Select D1_quarantine_sen4 or D0_observe. Return JSON only.\",\"stream\":false}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('response:', d.get('response','')[:80])"
```

If this takes longer than 30 seconds, increase `timeout_seconds` in `eval/configs/models/hybrid_game_llm.yaml`.

### 12b — Run a hybrid game + LLM stage

```bash
cd LLM_MTD_modular/
python3 -m eval.cli run-stage \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb
```

### 12c — Run a hybrid game + LLM trial

```bash
python3 -m eval.cli run-trial \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --seed 42 \
  --output-root outputs/
```

### 12d — Run only the LLM defender (no game layer)

```bash
python3 -m eval.cli run-stage \
  --model-config eval/configs/models/llm_only.yaml \
  --scenario-id sen4_edge2_clouddb
```

### 12e — Enable live Ryu action execution

By default eval configs have `execute_defender: false` (dry run). To actually push MTD actions to Ryu, edit `eval/configs/models/hybrid_game_llm.yaml`:

```yaml
trial:
  execute_defender: true
  dry_run: false
```

Then run:

```bash
python3 -m eval.cli run-stage \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --execute-defender
```

Verify Ryu applied the action:

```bash
curl -s http://127.0.0.1:8080/mtd/status | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('active_actions',{}), indent=2))"
```

---

## Step 13 — Defender Environment Controller (Standalone Check)

Test the defender's environment view independently:

```bash
# Snapshot current environment state
python3 environment/defender_env_controller.py --snapshot

# Apply a test MTD action
python3 environment/defender_env_controller.py \
  --apply-action '{"action": "rate_limit", "target": "sen4", "kbps": 128}'

# Verify it appeared in Ryu
curl -s http://127.0.0.1:8080/mtd/status | python3 -m json.tool | grep -A5 sen4

# Clear the action
python3 environment/defender_env_controller.py \
  --apply-action '{"action": "clear_target_policy", "target": "sen4"}'
```

---

## Step 14 — Attacker Environment Controller (Standalone Check)

```bash
python3 environment/attacker_env_controller.py --observe
```

Prints reachable nodes, active sensor IPs, edge gateway IPs, and any running Caldera agents.

---

## Step 15 — Generate Reports

After running stages or trials, generate paper-ready reports:

```bash
cd LLM_MTD_modular/

# Build figures and CSV from stage history
python3 -m eval.cli build-report \
  --eval-stage-history game/stage_history.jsonl \
  --eval-decision-trace game/decision_trace.jsonl \
  --output-dir outputs/figures/
```

---

## Startup Order Summary

Services must start in this exact order:

```
Background:
  Ollama:   ollama serve                     (port 11434)
  Caldera:  python3 server.py --insecure     (port 8888)

Terminal 1:  ryu-manager --observe-links environment/sdn/controller_app.py
Terminal 2:  sudo python3 environment/network/topology.py
             → then: deploy Caldera agent inside mn.sen4 (Step 6c)
Terminal 3:  python3 attacker/engine/caldera_dispatch_bridge.py \
               --port 9000 --caldera-url http://127.0.0.1:8888 \
               --api-key $CALDERA_API_KEY --group red \
               --logger-url "http://127.0.0.1:$LOGGER_PORT/attack/event"
Terminal 4:  python3 dashboard/dashboard_server.py
Terminal 5:  python3 -m game.strategy_runtime \
               --execute-defender --execute-attacker \
               --attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch \
               --ryu-action-url http://127.0.0.1:8080/mtd/action \
               --timeout-seconds 30
Terminal 6:  python3 -m eval.cli run-stage \
               --model-config eval/configs/models/hybrid_game_llm.yaml \
               --scenario-id sen4_edge2_clouddb
```

---

## Teardown

```bash
# Inside the Mininet CLI
mininet> exit

# Stop any running Caldera agents in containers
docker ps --format '{{.Names}}' | grep '^mn\.' | while read name; do
    docker exec "$name" bash -c "pkill -f caldera_agent || pkill -f sandcat || true" 2>/dev/null
done

# Clean OVS / network state
sudo mn -c

# Stop remaining Containernet Docker containers
docker ps --format '{{.Names}}' | grep '^mn\.' | xargs docker stop 2>/dev/null || true

# Reset game population state (optional — clears evolutionary history)
rm -f game/population_state.json
```

---

## Troubleshooting

### Attacker dispatch returns 404

```
"status": "dispatch_failed", "dispatch_http_status": 404
```

**Cause:** The dispatch URL is missing the `/caldera/dispatch` path. The bridge only accepts `POST /caldera/dispatch`.

**Fix:** Use `http://127.0.0.1:9000/caldera/dispatch` everywhere.

---

### Caldera operation created but no links execute (links=0)

**Cause 1:** No alive Caldera agent on `sen4`. Complete Step 6c.

**Cause 2:** The adversary resolved to `ad-hoc` (no abilities). The bridge will include `"warnings"` in its response when this happens. Verify adversaries have abilities:

```bash
curl -s http://127.0.0.1:8888/api/v2/adversaries -H "KEY: $CALDERA_API_KEY" \
  | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    print(a.get('name','?'), len(a.get('atomic_ordering',[])), 'abilities')
"
```

If any LLM MTD adversary shows 0 abilities, re-import ability YAMLs per Step 6b.

---

### Cloud logger / policy unreachable from host

```
"error": "<urlopen error timed out>", "url": "http://10.0.10.14:8000/..."
```

**Cause:** The 10.0.10.x addresses are internal Mininet IPs, not reachable from the host.

**Fix:** Use Docker port-mapped addresses. The bridge's `--logger-url` and `--policy-url` flags should use `http://127.0.0.1:XXXXX` with the port from `docker port mn.cloud_logger 8000/tcp`.

---

### Defender always selects D0\_observe

**In strategy\_runtime:** Expected. Replicator dynamics starts with near-equal populations; D0\_observe has the highest utility in the absence of attack signals. The population will shift once real attack effects register.

**In eval:** Check the trace for `fallback_used: true`. If true, Ollama is failing and the system falls back to game-theory. Check `fallback_reason` and see below.

---

### LLM defender always falls back

| `fallback_reason` | Cause | Fix |
|-------------------|-------|-----|
| `ConnectionRefusedError` | Ollama not running | `ollama serve` |
| `timed out` | Model too slow | Increase `timeout_seconds` in yaml |
| `invalid_defender_strategy_id` | Model hallucinated an ID | Check the prompt template |
| `JSONDecodeError` | Malformed output | Set `strict_json: true` in config |

---

### MulVAL `goals.txt: No such file or directory`

**Cause:** `graph_gen.sh` requires `goals.txt` in the working directory.

**Fix:** Run `topology_export.py` first (Step 10a) — it now generates `goals.txt` alongside the `.P` file. Copy both files to the working directory before running `graph_gen.sh`.

---

### eval.cli `run` command not found

```
error: argument command: invalid choice: 'run'
```

**Cause:** The eval CLI does not have a `run` subcommand.

**Fix:** Use `run-trial` or `run-stage` instead:

```bash
# Single stage with LLM:
python3 -m eval.cli run-stage \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb

# Full trial:
python3 -m eval.cli run-trial \
  --model-config eval/configs/models/hybrid_game_llm.yaml \
  --scenario-id sen4_edge2_clouddb \
  --seed 42
```

---

### BrokenPipeError in Caldera bridge

```
BrokenPipeError: [Errno 32] Broken pipe
```

**Cause:** The game runtime disconnected before the bridge finished writing its response (timeout too short).

**Fix:** Use `--timeout-seconds 30` when running `strategy_runtime`. The bridge now handles this error gracefully without crashing.
