# LLM_MTD_emo

## Project Architecture

This project has two main loops:

1. The telemetry and processing loop:
   `sensor -> edge gateway -> edge worker -> cloud_db`
2. The monitoring and defense loop:
   `metrics/events -> cloud_policy/strategy -> Ryu controller -> OpenFlow rules`

### High-Level Block Diagram

```text
                                   +----------------------------------+
                                   |         network_model.py         |
                                   | nodes, switches, IP plan, images,|
                                   | routes, worker mappings, env     |
                                   +----------------+-----------------+
                                                    |
                 +----------------------------------+----------------------------------+
                 |                                  |                                  |
                 v                                  v                                  v
      +----------------------+          +-----------------------+          +----------------------+
      |     topology.py      |          |   controller_app.py  |          | MulVAL exporters and |
      | builds Containernet  |          | Ryu learning switch  |          | parsers              |
      | topology and links   |          | + MTD REST API       |          | integrations/mulval/ |
      +----------+-----------+          +-----------+-----------+          +----------+-----------+
                 |                                  |                                 |
                 |                                  |                                 |
                 v                                  |                                 v
   +-----------------------------+                  |                     +-------------------------+
   | Docker service containers   |                  |                     |   cloud_policy context  |
   | images/*/app.py             |                  |                     |  MulVAL / Caldera /     |
   |                             |                  |                     |  strategy inputs         |
   +--------------+--------------+                  |                     +------------+------------+
                  |                                 |                                  |
                  | data path                       | control path                     |
                  v                                 |                                  v
   +------------------+     +------------------+    |                     +-------------------------+
   | sensor-node      | --> | edge-gateway     | -->|                     | images/cloud-policy/    |
   | generates        |     | queues + routes  |    |                     | app.py                  |
   +------------------+     +------------------+    |                     | selects defense action  |
                                                     |                     +------------+------------+
   +------------------+     +------------------+    |                                  |
   | edge-worker      | --> | cloud-db         | <--+                                  |
   | processes and    |     | stores summaries |                                       |
   | summarizes       |     | in SQLite        |                                       |
   +------------------+     +------------------+                                       |
            |                         |                                                 |
            +------------+------------+-------------------------+-----------------------+
                         |                                      |
                         v                                      v
             +-----------------------+              +------------------------+
             | cloud-metrics         |              | cloud-logger           |
             | aggregates samples,   |              | stores JSONL events    |
             | summaries, attack and |              | and forwards attacker/ |
             | defense signals       |              | defender events        |
             +-----------+-----------+              +-----------+------------+
                         |                                      |
                         +-------------------+------------------+
                                             |
                                             v
                               +-----------------------------+
                               | dashboard_server.py         |
                               | Dashboard.html              |
                               | browser-facing proxy/view   |
                               +-----------------------------+
```

### Runtime Flow

```text
Telemetry flow:
sensor-node -> edge-gateway -> edge-worker -> cloud-db

Metrics flow:
all services -> cloud-metrics
selected events -> cloud-logger -> cloud-metrics
controller metrics -> Ryu /mtd/metrics

Defense flow:
state_builder.py -> strategy_runtime.py -> cloud_policy -> controller_app.py -> OVS switches
```

## Repository Structure

The tree below shows the main hand-written files. Generated outputs, logs, and
`__pycache__` files are omitted for readability.

```text
LLM_MTD_emo/
├── README.md
├── Dashboard.html
├── controller_app.py
├── dashboard_server.py
├── network_model.py
├── topology.py
├── scripts/
│   └── build_images.sh
├── images/
│   ├── README.md
│   ├── shared/
│   │   └── mtd_common.py
│   ├── sensor-node/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── edge-gateway/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── edge-worker/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── cloud-db/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── cloud-object/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── cloud-metrics/
│   │   ├── Dockerfile
│   │   └── app.py
│   ├── cloud-policy/
│   │   ├── Dockerfile
│   │   └── app.py
│   └── cloud-logger/
│       ├── Dockerfile
│       └── app.py
├── integrations/
│   ├── attack_scenarios.json
│   ├── caldera/
│   │   ├── README.md
│   │   ├── caldera_client.py
│   │   ├── caldera_dispatch_bridge.py
│   │   ├── abilities/
│   │   └── adversaries/
│   ├── mulval/
│   │   ├── README.md
│   │   ├── assets/
│   │   │   ├── mulval_input_builder.py
│   │   │   ├── parser.py
│   │   │   ├── scenario_base.P
│   │   │   └── topology_export.py
│   │   └── outputs/
│   └── strategy/
│       ├── README.md
│       ├── game_model.py
│       ├── policy_selector.py
│       ├── stage_transition.py
│       ├── state_builder.py
│       ├── strategy_manager.py
│       ├── strategy_runtime.py
│       ├── strategy_space.json
│       └── population_state.json
└── splunkd
```

## Module and File Map

### Core Topology and Control Files

| File | Purpose | Main code inside |
| --- | --- | --- |
| `network_model.py` | Shared static model for the whole lab | switch IDs, node maps, IP plan, Docker image names, resource profiles, sensor destinations, worker routing, container environment generation |
| `topology.py` | Starts the live Containernet lab | checks controller reachability, checks local images, creates switches and Docker hosts, adds links, installs inter-domain routes |
| `controller_app.py` | Single Ryu controller and defense API | OpenFlow pipeline, topology discovery, learning-switch logic, host learning, `/mtd/status`, `/mtd/metrics`, `/mtd/action`, quarantine/rate-limit/reroute/release actions |
| `dashboard_server.py` | Browser-facing proxy for live metrics | serves `Dashboard.html`, proxies `/core`, `/metrics`, `/mtd/*`, supports Docker fallback when host cannot reach service IPs |
| `Dashboard.html` | Frontend dashboard | visualizes experiment and controller metrics through the proxy server |

### Service Modules Under `images/`

| File | Service role | What the code does |
| --- | --- | --- |
| `images/shared/mtd_common.py` | Shared utility library | environment parsing, JSON IO helpers, timestamp helpers, HTTP POST helper, Prometheus-style metric formatting |
| `images/sensor-node/app.py` | Sensor simulator | periodically creates telemetry payloads and sends them to one or more gateways |
| `images/edge-gateway/app.py` | Edge ingress and routing | receives sensor telemetry, measures ingestion latency, queues per-sensor traffic, forwards to mapped workers |
| `images/edge-worker/app.py` | Edge processing worker | validates assigned sensor, processes values, builds summary features, periodically forwards summaries to `cloud_db` |
| `images/cloud-db/app.py` | Storage service | stores telemetry and summaries in SQLite, reports storage counters and edge-to-cloud latency |
| `images/cloud-metrics/app.py` | Metrics aggregator | receives JSON metric samples and attack/defense events, exposes `/core`, `/experiment/summary`, and Prometheus-style `/metrics` |
| `images/cloud-policy/app.py` | Defense decision service | merges MulVAL/Caldera/strategy context with runtime metrics, selects defender action, emits a Ryu intent |
| `images/cloud-logger/app.py` | Event log service | appends JSONL experiment events and forwards attacker/defender events to `cloud_metrics` |
| `images/cloud-object/app.py` | Object storage helper | simple file/object store, not part of the primary telemetry pipeline |

### Strategy Layer Under `integrations/strategy/`

| File | Role | Main logic |
| --- | --- | --- |
| `strategy_space.json` | Strategy registry | attacker and defender strategy library with metadata, costs, rewards, paths, and action payloads |
| `state_builder.py` | Build the repeated-game state `S_t` | reads `/core`, `/mtd/metrics`, `/mtd/status`, MulVAL outputs, and scenario registry into one normalized state object |
| `strategy_manager.py` | Activate valid strategies | filters the full attacker/defender libraries into active sets for the current state |
| `game_model.py` | Evolutionary game engine | computes utility proxies and applies replicator-style population updates |
| `policy_selector.py` | Final strategy selection | picks one attacker and one defender from the evolved population |
| `strategy_runtime.py` | Main strategy orchestrator | runs one stage end to end, optionally dispatches attacker plans and defender actions, and saves stage results |
| `stage_transition.py` | Transition journaling | writes stage history and decision trace JSONL records |

### Attack-Graph and Attack-Execution Integrations

| File | Role | Main logic |
| --- | --- | --- |
| `integrations/attack_scenarios.json` | Shared scenario registry | links MulVAL paths, Caldera attack type, targets, success criteria, and candidate defender actions |
| `integrations/mulval/assets/topology_export.py` | MulVAL topology exporter | converts `network_model.py` into an abstract attack-graph model |
| `integrations/mulval/assets/mulval_input_builder.py` | MulVAL program renderer | turns abstract topology JSON into MulVAL `.P` facts |
| `integrations/mulval/assets/parser.py` | MulVAL output parser | converts `AttackGraph.xml` or `AttackGraph.txt` into policy-friendly JSON with paths, risk, and candidate defenses |
| `integrations/caldera/caldera_client.py` | Manual Caldera result summarizer | turns observed trial deltas into one compact attack result and optional defense result |
| `integrations/caldera/caldera_dispatch_bridge.py` | Live Caldera bridge | receives selected attacker plans, creates Caldera operations, polls for completion, and posts attack events back into the lab |

## Code Organization by Responsibility

```text
Static model and configuration
  network_model.py

Network emulation and SDN control
  topology.py
  controller_app.py

Container services
  images/shared/mtd_common.py
  images/sensor-node/app.py
  images/edge-gateway/app.py
  images/edge-worker/app.py
  images/cloud-db/app.py
  images/cloud-object/app.py
  images/cloud-metrics/app.py
  images/cloud-policy/app.py
  images/cloud-logger/app.py

Strategy and game layer
  integrations/strategy/*

MulVAL integration
  integrations/mulval/assets/*

Caldera integration
  integrations/caldera/*

User-facing dashboard and helpers
  dashboard_server.py
  Dashboard.html
  scripts/build_images.sh
```

## Run Orderjjjjj

Build the service images:

```bash
bash scripts/build_images.sh
```

Start the single Ryu controller app:

```bash
ryu-manager --observe-links controller_app.py
```

Start the Containernet topology:

```bash
sudo python3 topology.py
```

If `topology.py` reports missing `llm-mtd-emo/...` Docker images, run `bash scripts/build_images.sh` from this directory first. Those tags are local experiment images, not public registry images.

If a topology start fails part-way through, clean Mininet state before retrying:

```bash
sudo mn -c
```

`topology.py` requires Ryu to be reachable at `127.0.0.1:6653`. If it is not listening, topology creation stops before creating switches or containers.

The service images include `iproute2`, `iputils-ping`, `net-tools`, `curl`, and `procps` for container-side debugging. Rebuild the images and recreate the topology after Dockerfile changes:

```bash
bash scripts/build_images.sh
sudo mn -c
sudo python3 topology.py
```

Useful checks from the Containernet CLI:

```text
sen1 ip -br addr
sen6 ip -br addr
edge1_gw ip -br addr
sen1 ping -c 3 10.0.1.1
sen4 ping -c 3 10.0.2.1
sen1 curl http://10.0.1.1:8000/health
edge1_gw curl -s http://10.0.1.21:8000/health
```

Short names such as `e1gw` and `e1s1` are Linux interface aliases, not hostnames. Use the container name, such as `edge1_gw`, or the fixed IP, such as `10.0.1.1`. Use the real service port `8000`; do not type `<PORT>` in the CLI because the shell treats it as input redirection.

Containernet is configured with `dcmd="python -u /app/app.py"` for every Docker host. After startup, verify service processes from a normal terminal:

```bash
sudo docker exec mn.sen1 ps aux
sudo docker exec mn.edge1_gw ps aux
sudo docker exec mn.edge1_vm_s1 ps aux
sudo docker exec mn.cloud_db ps aux
sudo docker logs --tail 20 mn.sen1
sudo docker logs --tail 20 mn.edge1_gw
sudo docker logs --tail 20 mn.cloud_db
```

You should see `python -u /app/app.py` in `ps aux` and a startup line in `docker logs`.

Debug in this order:

1. Same-edge ping, such as `sen4 ping -c 3 10.0.2.1`.
2. Same-edge HTTP, such as `edge1_gw curl -s http://10.0.1.21:8000/health`.
3. Inter-domain traffic, such as `edge1_gw ping -c 3 10.0.10.10`.

If same-edge ping fails, check the Ryu terminal and the OVS controller state before testing cloud paths:

```text
sh ovs-vsctl show
sh ovs-ofctl -O OpenFlow13 dump-flows s_edge2
```

The topology adds on-link routes after `net.start()` so fixed `/24` domains can communicate across the SDN fabric. Restart the topology after pulling route changes:

```bash
exit
sudo mn -c
sudo python3 topology.py
```

If you installed `quarantine_sensor` for `sen4`, this failure is expected:

```text
sen4 ping -c 3 edge2_gw
```

Release `sen4` before using it as a baseline connectivity test:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"release_sensor","target":"sen4"}'
```

## Controller API

The controller listens on Ryu's WSGI REST port, usually `8080`.

Check controller state:

```bash
curl http://127.0.0.1:8080/mtd/status
```

Apply an isolation action:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"quarantine_sensor","target":"sen4"}'
```

Rate-limit a sensor:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"rate_limit","target":"sen6","kbps":128}'
```

Prefer the Edge 3 side of dual-homed `sen6`:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"reroute_traffic","target":"sen6","via":"s_edge3"}'
```

For `sen6`, this installs policy rules on the non-preferred edge switch. The
controller drops the non-preferred interface IP, blocks traffic from any `sen6`
IP to the non-preferred gateway, and, when the host port has already been
learned, drops ingress from that host port too. If the response says
`"port_rule": "pending_host_location"`, let `sen6` send a little traffic or
check `curl http://127.0.0.1:8080/mtd/status` until `10.0.2.16` appears under
`known_host_interfaces`, then run the reroute action again.

Release target-specific policy rules:

```bash
curl -X POST http://127.0.0.1:8080/mtd/action \
  -H 'Content-Type: application/json' \
  -d '{"action":"release_sensor","target":"sen4"}'
```

`cloud_policy` should emit a decision or Ryu intent. It should not manipulate OVS directly.

Implemented OpenFlow-backed actions in the first controller:

- `quarantine_sensor` / `isolate_sensor`: installs high-priority IPv4 and ARP drop rules for the target.
- `rate_limit` / `rate_limit_sensor`: installs OpenFlow meters and policy-table rules for the target source IP.
- `reroute_traffic` / `reroute_sensor`: for dual-homed nodes such as `sen6`, drops the non-preferred interface path and blocks target traffic to the non-preferred gateway.
- `release_sensor`: removes target-specific policy rules tracked by the controller.
- `observe`: accepts the policy decision without installing a rule.

The API recognizes `migrate_worker_traffic` and `change_sensor_ip_handling`, but returns `not_installed` until the worker-mapping or address-rewrite model is made explicit.

Controller-side action overhead is exposed separately:

```bash
curl http://127.0.0.1:8080/mtd/metrics
```

Each successful action response also includes `flow_rules_installed`,
`flow_delete_commands`, `meters_added`, `active_policy_actions`,
`ryu_request_received_at`, `ryu_flow_mods_enqueued_at`, and
`ryu_apply_duration_ms`.

## Metrics Measurement Block

The first experimental metrics are implemented in the existing services, not as
new topology nodes.

- `sensor-node`: adds `sensor_sent_at` to each telemetry message and reports generated, sent, failed, byte, and throughput counters.
- `edge-gateway`: records `gateway_received_at`, `sensor_to_gateway_latency_ms`, queue length, received/forwarded/drop/error counters, byte counters, throughput, CPU, and memory.
- `edge-worker`: records gateway-to-worker latency, processing latency, processed request count, summary output count, summary output gaps for downtime analysis, CPU, and memory.
- `cloud_db`: records `cloud_db_received_at`, `edge_to_cloud_latency_ms`, stored record counts, byte counters, and storage confirmations.
- `cloud_metrics`: stores JSON samples and exposes a core experiment summary.
- `cloud_logger`: stores JSONL experiment events from gateways, workers, cloud_db, and cloud_policy.
- `controller_app.py`: records action timing, flow-rule changes, meter changes, delete commands, and active policy actions.

Because this instrumentation changes container app code and environment
variables, rebuild the images and recreate the topology:

```bash
bash scripts/build_images.sh
exit
sudo mn -c
sudo python3 topology.py
```

Check the core metrics summary:

```bash
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/core
```

The same summary is available as:

```bash
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/experiment/summary
```

Useful direct checks:

```bash
sudo docker exec mn.sen6 curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.edge2_gw curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.edge3_vm_s6 curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.cloud_db curl -s http://127.0.0.1:8000/metrics
curl http://127.0.0.1:8080/mtd/metrics
```

Ryu runs outside the Docker service fabric, so controller overhead is exposed on
the Ryu REST API rather than pushed into `cloud_metrics` by default.

For the browser dashboard, do not use `python3 -m http.server`. A static file
server will treat `/core` and `/mtd/metrics` as filenames and return 404. Use
the project dashboard proxy instead:

```bash
cd ~/LLM_MTD_emo
python3 dashboard_server.py
```

Then open:

```text
http://<ubuntu-vm-ip>:8088/Dashboard.html
```

If `/core` fails because the host cannot reach `10.0.10.12:8000`, run the
dashboard server with Docker access so it can query inside `mn.cloud_metrics`:

```bash
sudo -E python3 dashboard_server.py
```

For message loss, compare sensor generated/sent counters with gateway
received/forwarded/drop counters, worker request/summary counters, and
cloud_db stored summary counters. For MTD overhead, compare the Ryu action
duration and flow-change counts with the next change in gateway, worker, or
cloud_db counters.

## Attack Scenario Registry and Event Hooks

MulVAL and Caldera share scenario definitions through:

```text
integrations/attack_scenarios.json
```

Each scenario records the entry node, MulVAL path, live attack type, target
asset, success-check checklist, and candidate Ryu-owned defender actions.

Attack and defense-result events should be sent to `cloud_logger`; it writes
JSONL and forwards attacker/defender counters to `cloud_metrics` through
`/attack/event`.

Start event example:

```bash
sudo docker exec mn.cloud_logger curl -s -X POST http://127.0.0.1:8000/attack/event \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"attack_start","scenario_id":"sen4_edge2_clouddb","entry_node":"sen4","tool":"caldera","adversary_id":"sensor_to_edge"}'
```

Result event example:

```bash
sudo docker exec mn.cloud_logger curl -s -X POST http://127.0.0.1:8000/attack/event \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"attack_result","scenario_id":"sen4_edge2_clouddb","success":true,"gateway_seen":true,"worker_seen":true,"cloud_seen":true,"attack_effect_success":true,"defense_success":false}'
```

Defense result example:

```bash
sudo docker exec mn.cloud_logger curl -s -X POST http://127.0.0.1:8000/attack/event \
  -H 'Content-Type: application/json' \
  -d '{"event_type":"defense_result","scenario_id":"sen4_edge2_clouddb","defense_action":"quarantine_sensor","target":"sen4","defense_success":true,"signals":{"drop_rules_active":true,"counters_stopped":true,"ovs_drop_counter_delta":12}}'
```

For live strategy runs, prefer the host-mapped service ports from `docker ps`
instead of direct Containernet service IPs. The strategy runtime accepts the
mapped `cloud_logger` URL and automatically records a `defense_result` after a
confirmed non-observe Ryu action:

```bash
python3 integrations/strategy/strategy_runtime.py \
  --scenario-id sen4_edge2_clouddb \
  --core-url http://127.0.0.1:<cloud_metrics_port>/core \
  --cloud-policy-url http://127.0.0.1:<cloud_policy_port>/context \
  --cloud-logger-url http://127.0.0.1:<cloud_logger_port>/attack/event \
  --execute-defender \
  --observe-delay-seconds 5 \
  --timeout-seconds 8
```

To make the selected attacker plan launch Caldera instead of staying in
`dry_run`, start the dispatch bridge with Caldera credentials:

```bash
CALDERA_API_KEY=<red_api_key> \
python3 integrations/caldera/caldera_dispatch_bridge.py \
  --logger-url http://127.0.0.1:<cloud_logger_port>/attack/event \
  --policy-url http://127.0.0.1:<cloud_policy_port>/context
```

Then add attacker execution to the strategy command:

```bash
--execute-attacker \
--attacker-dispatch-url http://127.0.0.1:9000/caldera/dispatch
```

After a manual Caldera run, create the compact result JSON with:

```bash
python3 integrations/caldera/caldera_client.py \
  --operation-id sensor_edge_trial_01 \
  --scenario-id sen4_edge2_clouddb \
  --success true \
  --gateway-received-delta 42 \
  --worker-request-delta 39 \
  --cloud-summary-delta 8 \
  --gateway-queue-spike false
```

`cloud_policy` can read MulVAL and Caldera context in observe-only mode either
from `/data/mulval_policy.json` plus `/data/caldera_result.json`, or by posting
to:

```bash
sudo docker exec mn.cloud_policy curl -s -X POST http://127.0.0.1:8000/context \
  -H 'Content-Type: application/json' \
  -d '{"caldera_result":{"operation_id":"sensor_edge_trial_01","scenario_id":"sen4_edge2_clouddb","entry_node":"sen4","attempted_path":["sen4","edge2_gw","edge2_vm_s4","cloud_db"],"success":true}}'
```

Then check the experiment metrics:

```bash
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/core
sudo docker exec mn.cloud_metrics curl -s http://127.0.0.1:8000/metrics
sudo docker exec mn.cloud_logger curl -s http://127.0.0.1:8000/events?limit=5
```

Because this changes service container code and `cloud_logger` environment
variables, rebuild images and recreate the topology:

```bash
bash scripts/build_images.sh
exit
sudo mn -c
sudo python3 topology.py
```

Caldera stays outside this repository. The repo-side Caldera abilities,
adversaries, install-copy commands, Sandcat enrollment steps, and first manual
operation checklist are documented in:

```text
integrations/caldera/README.md
```

## Fogbed-Style Instances

Fog instance grouping and resource profiles are defined in `network_model.py`.

- `edge1`: `edge1_gw`, `edge1_vm_s1`, `edge1_vm_s2`, `edge1_vm_s3`
- `edge2`: `edge2_gw`, `edge2_vm_s4`, `edge2_vm_s5`, `edge2_vm_s6`
- `edge3`: `edge3_gw`, `edge3_vm_s6`, `edge3_vm_s7`, `edge3_vm_s8`, `edge3_vm_s9`, `edge3_vm_s10`
- `cloud`: `cloud_db`, `cloud_object`, `cloud_metrics`, `cloud_policy`, `cloud_logger`

Resource profiles:

- sensors: `0.2` CPU, `128m` memory
- edge workers: `0.5` CPU, `256m` memory
- edge gateways: `1.0` CPU, `512m` memory
- standard cloud services: `1.0` CPU, `512m` memory
- heavier cloud services, currently `cloud_db` and `cloud_policy`: `2.0` CPU, `1g` memory

`topology.py` passes these profiles to Containernet through Docker resource knobs such as `cpu_period`, `cpu_quota`, and `mem_limit`. Ryu does not do fog-instance grouping; it only observes and controls forwarding behavior.
