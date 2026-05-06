"""Shared logical model for the LLM_MTD_emo topology and Ryu controller.fefdvdvdfeefDDDDDDDDD"""

import json


CONTROLLER_NAME = "c0"
CONTROLLER_IP = "127.0.0.1"
CONTROLLER_PORT = 6653
OPENFLOW_VERSION = "OpenFlow13"
SERVICE_PORT = 8000
SERVICE_COMMAND = "python -u /app/app.py"

SWITCH_DPIDS = {
    "s_edge1": "0000000000000001",
    "s_edge2": "0000000000000002",
    "s_edge3": "0000000000000003",
    "s_core": "0000000000000004",
    "s_cloud": "0000000000000005",
}
SWITCH_NAME_BY_DPID = {int(dpid, 16): name for name, dpid in SWITCH_DPIDS.items()}
DPID_BY_SWITCH_NAME = {name: int(dpid, 16) for name, dpid in SWITCH_DPIDS.items()}

SERVICE_IMAGES = {
    "sensor": "llm-mtd-emo/sensor-node:latest",
    "edge_gateway": "llm-mtd-emo/edge-gateway:latest",
    "edge_worker": "llm-mtd-emo/edge-worker:latest",
    "cloud_db": "llm-mtd-emo/cloud-db:latest",
    "cloud_object": "llm-mtd-emo/cloud-object:latest",
    "cloud_metrics": "llm-mtd-emo/cloud-metrics:latest",
    "cloud_policy": "llm-mtd-emo/cloud-policy:latest",
    "cloud_logger": "llm-mtd-emo/cloud-logger:latest",
}

SENSOR_NODE_MAP = {
    "sen1": ("s_edge1",),
    "sen2": ("s_edge1",),
    "sen3": ("s_edge1",),
    "sen4": ("s_edge2",),
    "sen5": ("s_edge2",),
    "sen6": ("s_edge2", "s_edge3"),
    "sen7": ("s_edge3",),
    "sen8": ("s_edge3",),
    "sen9": ("s_edge3",),
    "sen10": ("s_edge3",),
}

EDGE_NODE_MAP = {
    "edge1_gw": ("s_edge1",),
    "edge1_vm_s1": ("s_edge1",),
    "edge1_vm_s2": ("s_edge1",),
    "edge1_vm_s3": ("s_edge1",),
    "edge2_gw": ("s_edge2",),
    "edge2_vm_s4": ("s_edge2",),
    "edge2_vm_s5": ("s_edge2",),
    "edge2_vm_s6": ("s_edge2",),
    "edge3_gw": ("s_edge3",),
    "edge3_vm_s6": ("s_edge3",),
    "edge3_vm_s7": ("s_edge3",),
    "edge3_vm_s8": ("s_edge3",),
    "edge3_vm_s9": ("s_edge3",),
    "edge3_vm_s10": ("s_edge3",),
}

CLOUD_NODE_MAP = {
    "cloud_db": ("s_cloud",),
    "cloud_object": ("s_cloud",),
    "cloud_metrics": ("s_cloud",),
    "cloud_policy": ("s_cloud",),
    "cloud_logger": ("s_cloud",),
}

FOG_INSTANCES = {
    "edge1": {
        "kind": "edge",
        "nodes": (
            "edge1_gw",
            "edge1_vm_s1",
            "edge1_vm_s2",
            "edge1_vm_s3",
        ),
    },
    "edge2": {
        "kind": "edge",
        "nodes": (
            "edge2_gw",
            "edge2_vm_s4",
            "edge2_vm_s5",
            "edge2_vm_s6",
        ),
    },
    "edge3": {
        "kind": "edge",
        "nodes": (
            "edge3_gw",
            "edge3_vm_s6",
            "edge3_vm_s7",
            "edge3_vm_s8",
            "edge3_vm_s9",
            "edge3_vm_s10",
        ),
    },
    "cloud": {
        "kind": "cloud",
        "nodes": (
            "cloud_db",
            "cloud_object",
            "cloud_metrics",
            "cloud_policy",
            "cloud_logger",
        ),
    },
}

FOG_INSTANCE_BY_NODE = {
    node_name: instance_name
    for instance_name, instance in FOG_INSTANCES.items()
    for node_name in instance["nodes"]
}

FOG_INSTANCE_RESOURCE_MODELS = {
    "edge1": "edge_constrained",
    "edge2": "edge_constrained",
    "edge3": "edge_constrained",
    "cloud": "cloud_overprovisioned",
}

CPU_PERIOD_US = 100000

RESOURCE_PROFILES = {
    "sensor_tiny": {
        "cpu": 0.2,
        "memory": "128m",
        "docker": {
            "cpu_period": CPU_PERIOD_US,
            "cpu_quota": 20000,
            "mem_limit": "128m",
        },
    },
    "edge_worker_constrained": {
        "cpu": 0.5,
        "memory": "256m",
        "docker": {
            "cpu_period": CPU_PERIOD_US,
            "cpu_quota": 50000,
            "mem_limit": "256m",
        },
    },
    "edge_gateway_constrained": {
        "cpu": 1.0,
        "memory": "512m",
        "docker": {
            "cpu_period": CPU_PERIOD_US,
            "cpu_quota": 100000,
            "mem_limit": "512m",
        },
    },
    "cloud_standard": {
        "cpu": 1.0,
        "memory": "512m",
        "docker": {
            "cpu_period": CPU_PERIOD_US,
            "cpu_quota": 100000,
            "mem_limit": "512m",
        },
    },
    "cloud_heavy": {
        "cpu": 2.0,
        "memory": "1g",
        "docker": {
            "cpu_period": CPU_PERIOD_US,
            "cpu_quota": 200000,
            "mem_limit": "1g",
        },
    },
}

NODE_RESOURCE_PROFILE = {
    **{node_name: "sensor_tiny" for node_name in SENSOR_NODE_MAP},
    "edge1_gw": "edge_gateway_constrained",
    "edge1_vm_s1": "edge_worker_constrained",
    "edge1_vm_s2": "edge_worker_constrained",
    "edge1_vm_s3": "edge_worker_constrained",
    "edge2_gw": "edge_gateway_constrained",
    "edge2_vm_s4": "edge_worker_constrained",
    "edge2_vm_s5": "edge_worker_constrained",
    "edge2_vm_s6": "edge_worker_constrained",
    "edge3_gw": "edge_gateway_constrained",
    "edge3_vm_s6": "edge_worker_constrained",
    "edge3_vm_s7": "edge_worker_constrained",
    "edge3_vm_s8": "edge_worker_constrained",
    "edge3_vm_s9": "edge_worker_constrained",
    "edge3_vm_s10": "edge_worker_constrained",
    "cloud_db": "cloud_heavy",
    "cloud_object": "cloud_standard",
    "cloud_metrics": "cloud_standard",
    "cloud_policy": "cloud_heavy",
    "cloud_logger": "cloud_standard",
}

ARCHITECTURE_NODE_MAP = {
    **SENSOR_NODE_MAP,
    **EDGE_NODE_MAP,
    **CLOUD_NODE_MAP,
}

SUBNETS = {
    "edge1": "10.0.1.0/24",
    "edge2": "10.0.2.0/24",
    "edge3": "10.0.3.0/24",
    "cloud": "10.0.10.0/24",
}

SUBNET_BY_SWITCH = {
    "s_edge1": SUBNETS["edge1"],
    "s_edge2": SUBNETS["edge2"],
    "s_edge3": SUBNETS["edge3"],
    "s_cloud": SUBNETS["cloud"],
}

NODE_INTERFACE_IPS = {
    "sen1": (("s_edge1", "10.0.1.11/24"),),
    "sen2": (("s_edge1", "10.0.1.12/24"),),
    "sen3": (("s_edge1", "10.0.1.13/24"),),
    "sen4": (("s_edge2", "10.0.2.14/24"),),
    "sen5": (("s_edge2", "10.0.2.15/24"),),
    "sen6": (
        ("s_edge2", "10.0.2.16/24"),
        ("s_edge3", "10.0.3.16/24"),
    ),
    "sen7": (("s_edge3", "10.0.3.17/24"),),
    "sen8": (("s_edge3", "10.0.3.18/24"),),
    "sen9": (("s_edge3", "10.0.3.19/24"),),
    "sen10": (("s_edge3", "10.0.3.20/24"),),
    "edge1_gw": (("s_edge1", "10.0.1.1/24"),),
    "edge1_vm_s1": (("s_edge1", "10.0.1.21/24"),),
    "edge1_vm_s2": (("s_edge1", "10.0.1.22/24"),),
    "edge1_vm_s3": (("s_edge1", "10.0.1.23/24"),),
    "edge2_gw": (("s_edge2", "10.0.2.1/24"),),
    "edge2_vm_s4": (("s_edge2", "10.0.2.24/24"),),
    "edge2_vm_s5": (("s_edge2", "10.0.2.25/24"),),
    "edge2_vm_s6": (("s_edge2", "10.0.2.26/24"),),
    "edge3_gw": (("s_edge3", "10.0.3.1/24"),),
    "edge3_vm_s6": (("s_edge3", "10.0.3.26/24"),),
    "edge3_vm_s7": (("s_edge3", "10.0.3.27/24"),),
    "edge3_vm_s8": (("s_edge3", "10.0.3.28/24"),),
    "edge3_vm_s9": (("s_edge3", "10.0.3.29/24"),),
    "edge3_vm_s10": (("s_edge3", "10.0.3.30/24"),),
    "cloud_db": (("s_cloud", "10.0.10.10/24"),),
    "cloud_object": (("s_cloud", "10.0.10.11/24"),),
    "cloud_metrics": (("s_cloud", "10.0.10.12/24"),),
    "cloud_policy": (("s_cloud", "10.0.10.13/24"),),
    "cloud_logger": (("s_cloud", "10.0.10.14/24"),),
}

NODE_INTERFACE_ALIASES = {
    "sen1": "sen1",
    "sen2": "sen2",
    "sen3": "sen3",
    "sen4": "sen4",
    "sen5": "sen5",
    "sen6": "sen6",
    "sen7": "sen7",
    "sen8": "sen8",
    "sen9": "sen9",
    "sen10": "sen10",
    "edge1_gw": "e1gw",
    "edge1_vm_s1": "e1s1",
    "edge1_vm_s2": "e1s2",
    "edge1_vm_s3": "e1s3",
    "edge2_gw": "e2gw",
    "edge2_vm_s4": "e2s4",
    "edge2_vm_s5": "e2s5",
    "edge2_vm_s6": "e2s6",
    "edge3_gw": "e3gw",
    "edge3_vm_s6": "e3s6",
    "edge3_vm_s7": "e3s7",
    "edge3_vm_s8": "e3s8",
    "edge3_vm_s9": "e3s9",
    "edge3_vm_s10": "e3s10",
    "cloud_db": "cdb",
    "cloud_object": "cobj",
    "cloud_metrics": "cmet",
    "cloud_policy": "cpol",
    "cloud_logger": "clog",
}


def container_interface_name(node_name, interface_index):
    return f"{NODE_INTERFACE_ALIASES[node_name]}-eth{interface_index}"


def ip_without_prefix(cidr_address):
    return cidr_address.split("/", maxsplit=1)[0]


def primary_ip(node_name):
    return ip_without_prefix(NODE_INTERFACE_IPS[node_name][0][1])


def node_ips(node_name):
    return tuple(ip_without_prefix(ip_address) for _, ip_address in NODE_INTERFACE_IPS[node_name])


def service_url(node_name, path):
    return f"http://{primary_ip(node_name)}:{SERVICE_PORT}{path}"


def validate_interface_ip_plan():
    missing_nodes = set(ARCHITECTURE_NODE_MAP) - set(NODE_INTERFACE_IPS)
    extra_nodes = set(NODE_INTERFACE_IPS) - set(ARCHITECTURE_NODE_MAP)
    if missing_nodes or extra_nodes:
        raise ValueError(
            f"IP plan mismatch; missing={missing_nodes}, extra={extra_nodes}"
        )

    for node_name, switch_names in ARCHITECTURE_NODE_MAP.items():
        ip_switch_names = tuple(
            switch_name for switch_name, _ in NODE_INTERFACE_IPS[node_name]
        )
        if ip_switch_names != switch_names:
            raise ValueError(
                f"{node_name} switch map {switch_names} does not match "
                f"IP plan {ip_switch_names}"
            )

    missing_alias_nodes = set(ARCHITECTURE_NODE_MAP) - set(NODE_INTERFACE_ALIASES)
    extra_alias_nodes = set(NODE_INTERFACE_ALIASES) - set(ARCHITECTURE_NODE_MAP)
    if missing_alias_nodes or extra_alias_nodes:
        raise ValueError(
            "Interface alias mismatch; "
            f"missing={missing_alias_nodes}, extra={extra_alias_nodes}"
        )

    for node_name, interfaces in NODE_INTERFACE_IPS.items():
        for interface_index, _interface in enumerate(interfaces):
            interface_name = container_interface_name(node_name, interface_index)
            if len(interface_name) > 15:
                raise ValueError(
                    f"{node_name} interface name {interface_name} is too long "
                    "for Linux IFNAMSIZ=16"
                )


def validate_resource_plan():
    missing_profile_nodes = set(ARCHITECTURE_NODE_MAP) - set(NODE_RESOURCE_PROFILE)
    extra_profile_nodes = set(NODE_RESOURCE_PROFILE) - set(ARCHITECTURE_NODE_MAP)
    if missing_profile_nodes or extra_profile_nodes:
        raise ValueError(
            "Resource profile mismatch; "
            f"missing={missing_profile_nodes}, extra={extra_profile_nodes}"
        )

    unknown_profiles = {
        profile_name
        for profile_name in NODE_RESOURCE_PROFILE.values()
        if profile_name not in RESOURCE_PROFILES
    }
    if unknown_profiles:
        raise ValueError(f"Unknown resource profiles: {unknown_profiles}")

    grouped_nodes = {
        node_name for instance in FOG_INSTANCES.values() for node_name in instance["nodes"]
    }
    expected_grouped_nodes = set(EDGE_NODE_MAP) | set(CLOUD_NODE_MAP)
    if grouped_nodes != expected_grouped_nodes:
        raise ValueError(
            "Fog instance grouping must cover edge and cloud nodes exactly; "
            f"expected={expected_grouped_nodes}, actual={grouped_nodes}"
        )

    unknown_group_nodes = grouped_nodes - set(ARCHITECTURE_NODE_MAP)
    if unknown_group_nodes:
        raise ValueError(f"Fog instances include unknown nodes: {unknown_group_nodes}")


def resource_profile_for(node_name):
    profile_name = NODE_RESOURCE_PROFILE[node_name]
    profile = RESOURCE_PROFILES[profile_name]
    return {
        "profile": profile_name,
        "cpu": profile["cpu"],
        "memory": profile["memory"],
        "docker": profile["docker"].copy(),
    }


SENSOR_DESTINATION_MAP = {
    "sen1": (service_url("edge1_gw", "/telemetry"),),
    "sen2": (service_url("edge1_gw", "/telemetry"),),
    "sen3": (service_url("edge1_gw", "/telemetry"),),
    "sen4": (service_url("edge2_gw", "/telemetry"),),
    "sen5": (service_url("edge2_gw", "/telemetry"),),
    "sen6": (
        service_url("edge2_gw", "/telemetry"),
        service_url("edge3_gw", "/telemetry"),
    ),
    "sen7": (service_url("edge3_gw", "/telemetry"),),
    "sen8": (service_url("edge3_gw", "/telemetry"),),
    "sen9": (service_url("edge3_gw", "/telemetry"),),
    "sen10": (service_url("edge3_gw", "/telemetry"),),
}

EDGE_GATEWAY_WORKER_MAP = {
    "edge1_gw": {
        "sen1": service_url("edge1_vm_s1", "/process"),
        "sen2": service_url("edge1_vm_s2", "/process"),
        "sen3": service_url("edge1_vm_s3", "/process"),
    },
    "edge2_gw": {
        "sen4": service_url("edge2_vm_s4", "/process"),
        "sen5": service_url("edge2_vm_s5", "/process"),
        "sen6": service_url("edge2_vm_s6", "/process"),
    },
    "edge3_gw": {
        "sen6": service_url("edge3_vm_s6", "/process"),
        "sen7": service_url("edge3_vm_s7", "/process"),
        "sen8": service_url("edge3_vm_s8", "/process"),
        "sen9": service_url("edge3_vm_s9", "/process"),
        "sen10": service_url("edge3_vm_s10", "/process"),
    },
}

EDGE_WORKER_ASSIGNMENTS = {
    "edge1_vm_s1": ("edge1", "sen1"),
    "edge1_vm_s2": ("edge1", "sen2"),
    "edge1_vm_s3": ("edge1", "sen3"),
    "edge2_vm_s4": ("edge2", "sen4"),
    "edge2_vm_s5": ("edge2", "sen5"),
    "edge2_vm_s6": ("edge2", "sen6"),
    "edge3_vm_s6": ("edge3", "sen6"),
    "edge3_vm_s7": ("edge3", "sen7"),
    "edge3_vm_s8": ("edge3", "sen8"),
    "edge3_vm_s9": ("edge3", "sen9"),
    "edge3_vm_s10": ("edge3", "sen10"),
}

EDGE_GATEWAY_IDS = {
    "edge1_gw": "edge1",
    "edge2_gw": "edge2",
    "edge3_gw": "edge3",
}

CLOUD_SERVICE_ENV = {
    "cloud_db": {
        "DB_PATH": "/data/telemetry.db",
        "METRICS_URL": service_url("cloud_metrics", "/metrics"),
        "LOGGER_URL": service_url("cloud_logger", "/log"),
    },
    "cloud_object": {"OBJECT_ROOT": "/data/objects"},
    "cloud_metrics": {},
    "cloud_policy": {
        "POLICY_MODE": "game-baseline",
        "LLM_POLICY_PROVIDER": "disabled",
        "POLICY_OBSERVE_ONLY": "1",
        "MULVAL_POLICY_JSON": "/data/mulval_policy.json",
        "CALDERA_RESULT_JSON": "/data/caldera_result.json",
        "METRICS_URL": service_url("cloud_metrics", "/metrics"),
        "LOGGER_URL": service_url("cloud_logger", "/log"),
        "REPORT_INTERVAL_SECONDS": "5.0",
        "DROP_THRESHOLD": "5",
        "QUEUE_THRESHOLD": "50",
        "LATENCY_THRESHOLD_MS": "250",
    },
    "cloud_logger": {
        "LOG_PATH": "/data/experiment-events.jsonl",
        "METRICS_URL": service_url("cloud_metrics", "/attack/event"),
    },
}

CLOUD_SERVICE_IMAGE_KEYS = {
    "cloud_db": "cloud_db",
    "cloud_object": "cloud_object",
    "cloud_metrics": "cloud_metrics",
    "cloud_policy": "cloud_policy",
    "cloud_logger": "cloud_logger",
}


def make_container_specs(service_images=None):
    """Freeze image and environment variables for every Docker-host node."""
    validate_interface_ip_plan()
    validate_resource_plan()

    images = SERVICE_IMAGES.copy()
    if service_images:
        images.update(service_images)

    container_specs = {}
    for sensor_id, switch_names in SENSOR_NODE_MAP.items():
        resource_profile = resource_profile_for(sensor_id)
        container_specs[sensor_id] = {
            "image": images["sensor"],
            "command": SERVICE_COMMAND,
            "switches": switch_names,
            "interfaces": NODE_INTERFACE_IPS[sensor_id],
            "fog_instance": FOG_INSTANCE_BY_NODE.get(sensor_id),
            "resource_profile": resource_profile,
            "environment": {
                "SENSOR_ID": sensor_id,
                "PRIMARY_IP": primary_ip(sensor_id),
                "RESOURCE_PROFILE": resource_profile["profile"],
                "PAYLOAD_TYPE": "iot_telemetry",
                "DESTINATION_URLS": ",".join(SENSOR_DESTINATION_MAP[sensor_id]),
                "METRICS_URL": service_url("cloud_metrics", "/metrics"),
                "SEND_INTERVAL_SECONDS": "2.0",
                "REPORT_INTERVAL_SECONDS": "5.0",
                "PORT": str(SERVICE_PORT),
            },
        }

    for gateway_name, edge_id in EDGE_GATEWAY_IDS.items():
        resource_profile = resource_profile_for(gateway_name)
        container_specs[gateway_name] = {
            "image": images["edge_gateway"],
            "command": SERVICE_COMMAND,
            "switches": EDGE_NODE_MAP[gateway_name],
            "interfaces": NODE_INTERFACE_IPS[gateway_name],
            "fog_instance": FOG_INSTANCE_BY_NODE[gateway_name],
            "resource_profile": resource_profile,
            "environment": {
                "GATEWAY_ID": gateway_name,
                "EDGE_ID": edge_id,
                "PRIMARY_IP": primary_ip(gateway_name),
                "FOG_INSTANCE": FOG_INSTANCE_BY_NODE[gateway_name],
                "RESOURCE_PROFILE": resource_profile["profile"],
                "SENSOR_WORKER_MAP": json.dumps(EDGE_GATEWAY_WORKER_MAP[gateway_name]),
                "METRICS_URL": service_url("cloud_metrics", "/metrics"),
                "LOGGER_URL": service_url("cloud_logger", "/log"),
                "MAX_QUEUE_SIZE": "100",
                "PORT": str(SERVICE_PORT),
            },
        }

    for worker_name, (edge_id, sensor_id) in EDGE_WORKER_ASSIGNMENTS.items():
        resource_profile = resource_profile_for(worker_name)
        container_specs[worker_name] = {
            "image": images["edge_worker"],
            "command": SERVICE_COMMAND,
            "switches": EDGE_NODE_MAP[worker_name],
            "interfaces": NODE_INTERFACE_IPS[worker_name],
            "fog_instance": FOG_INSTANCE_BY_NODE[worker_name],
            "resource_profile": resource_profile,
            "environment": {
                "WORKER_ID": worker_name,
                "EDGE_ID": edge_id,
                "ASSIGNED_SENSOR": sensor_id,
                "PRIMARY_IP": primary_ip(worker_name),
                "FOG_INSTANCE": FOG_INSTANCE_BY_NODE[worker_name],
                "RESOURCE_PROFILE": resource_profile["profile"],
                "CLOUD_SUMMARY_URL": service_url("cloud_db", "/summary"),
                "METRICS_URL": service_url("cloud_metrics", "/metrics"),
                "LOGGER_URL": service_url("cloud_logger", "/log"),
                "SUMMARY_EVERY": "5",
                "PORT": str(SERVICE_PORT),
            },
        }

    for cloud_name, switch_names in CLOUD_NODE_MAP.items():
        image_key = CLOUD_SERVICE_IMAGE_KEYS[cloud_name]
        resource_profile = resource_profile_for(cloud_name)
        container_specs[cloud_name] = {
            "image": images[image_key],
            "command": SERVICE_COMMAND,
            "switches": switch_names,
            "interfaces": NODE_INTERFACE_IPS[cloud_name],
            "fog_instance": FOG_INSTANCE_BY_NODE[cloud_name],
            "resource_profile": resource_profile,
            "environment": {
                **CLOUD_SERVICE_ENV[cloud_name],
                "PRIMARY_IP": primary_ip(cloud_name),
                "FOG_INSTANCE": FOG_INSTANCE_BY_NODE[cloud_name],
                "RESOURCE_PROFILE": resource_profile["profile"],
                "PORT": str(SERVICE_PORT),
            },
        }

    return container_specs
