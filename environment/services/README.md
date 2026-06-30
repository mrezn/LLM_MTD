# LLM_MTD_emo Service Images

Build all local images from the project root with:

```bash
bash scripts/build_images.sh
```

The topology expects these tags:

- `llm-mtd-emo/sensor-node:latest`: generates periodic telemetry, stamps `sensor_sent_at`, sends to one or more gateway URLs, and exposes `/health` plus `/metrics`.
- `llm-mtd-emo/edge-gateway:latest`: receives `/telemetry`, queues by sensor, forwards to the mapped worker, and exposes queue, packet, byte, drop, throughput, resource, and sensor-to-edge latency metrics.
- `llm-mtd-emo/edge-worker:latest`: processes one assigned sensor stream through `/process`, computes rolling local features, optionally forwards summaries to cloud storage, and exposes CPU, memory, latency, downtime-gap, and request counters.
- `llm-mtd-emo/cloud-db:latest`: provides a SQLite-backed API for telemetry and worker summaries, stamps `cloud_db_received_at`, and reports edge-to-cloud latency.
- `llm-mtd-emo/cloud-object:latest`: provides a tiny file/object store API.
- `llm-mtd-emo/cloud-metrics:latest`: accepts JSON metrics samples and exposes Prometheus-compatible `/metrics` plus `/core` experiment summaries.
- `llm-mtd-emo/cloud-policy:latest`: runs the baseline game-policy selector, reports policy resource metrics, and returns Ryu-owned defense intents from `/decide`.
- `llm-mtd-emo/cloud-logger:latest`: writes experiment events to JSONL through `/log`.

The cloud-policy service deliberately does not manipulate OVS directly. It emits a selected action and a Ryu intent for later enforcement by the SDN controller.

All service images include basic debugging tools: `ip`, `ping`, `ifconfig`, `curl`, and `ps`.

## Fixed Subnets

The topology assigns deterministic `/24` interface addresses instead of relying on Docker-assigned addresses:

- Edge 1: `10.0.1.0/24`, with `edge1_gw` at `10.0.1.1`, sensors `sen1` through `sen3` at `10.0.1.11` through `10.0.1.13`, and workers at `10.0.1.21` through `10.0.1.23`.
- Edge 2: `10.0.2.0/24`, with `edge2_gw` at `10.0.2.1`, sensors `sen4`, `sen5`, and `sen6:eth0` at `10.0.2.14` through `10.0.2.16`, and workers at `10.0.2.24` through `10.0.2.26`.
- Edge 3: `10.0.3.0/24`, with `edge3_gw` at `10.0.3.1`, `sen6:eth1` at `10.0.3.16`, sensors `sen7` through `sen10` at `10.0.3.17` through `10.0.3.20`, and workers at `10.0.3.26` through `10.0.3.30`.
- Cloud: `10.0.10.0/24`, with `cloud_db` through `cloud_logger` at `10.0.10.10` through `10.0.10.14`.
