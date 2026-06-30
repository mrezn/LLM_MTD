"""Containernet topology for the LLM_MTD_emo SDN deployment."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Containernet
from mininet.node import OVSSwitch, RemoteController

from environment.network.network_model import (
    CLOUD_NODE_MAP,
    CONTROLLER_IP,
    CONTROLLER_NAME,
    CONTROLLER_PORT,
    EDGE_NODE_MAP,
    OPENFLOW_VERSION,
    SENSOR_NODE_MAP,
    SUBNET_BY_SWITCH,
    SWITCH_DPIDS,
    container_interface_name,
    make_container_specs,
)


TOPOLOGY_SWITCHES = tuple(SWITCH_DPIDS.keys())


def _run_quiet(command):
    return subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _delete_link_if_present(interface_name):
    result = subprocess.run(
        ["ip", "link", "show", interface_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        _run_quiet(["ip", "link", "delete", interface_name])


def _cleanup_project_switch_bridges():
    result = subprocess.run(
        ["ovs-vsctl", "list-br"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return
    bridges = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    for bridge in TOPOLOGY_SWITCHES:
        if bridge in bridges:
            info(f"*** Removing stale OVS bridge {bridge}\n")
            _run_quiet(["ovs-vsctl", "--if-exists", "del-br", bridge])


def _cleanup_project_interfaces():
    result = subprocess.run(
        ["ip", "-o", "link", "show"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return

    stale_interfaces = []
    for line in result.stdout.splitlines():
        parts = line.split(":", maxsplit=2)
        if len(parts) < 2:
            continue
        interface_name = parts[1].strip()
        if "@" in interface_name:
            interface_name = interface_name.split("@", maxsplit=1)[0]
        if interface_name == "lo":
            continue
        if interface_name.startswith("s_") and "-eth" in interface_name:
            stale_interfaces.append(interface_name)
            continue
        if interface_name.startswith("e") and "-eth" in interface_name:
            stale_interfaces.append(interface_name)
            continue
        if interface_name.startswith("sen") and "-eth" in interface_name:
            stale_interfaces.append(interface_name)
            continue
        if interface_name.startswith("c") and "-eth" in interface_name:
            stale_interfaces.append(interface_name)

    for interface_name in sorted(set(stale_interfaces)):
        info(f"*** Removing stale link {interface_name}\n")
        _delete_link_if_present(interface_name)


def cleanup_stale_state():
    """Remove leftover Mininet containers, OVS bridges, and network interfaces."""
    info("*** Cleaning up stale Mininet/Containernet state\n")

    if os.geteuid() != 0:
        info("*** Warning: cleanup is running without root privileges; stale links may remain\n")

    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=mn.", "--format", "{{.ID}}"],
        capture_output=True, text=True, check=False,
    )
    container_ids = result.stdout.strip().split()
    if container_ids:
        info(f"*** Removing {len(container_ids)} leftover mn.* containers\n")
        subprocess.run(
            ["docker", "rm", "-f", *container_ids],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        time.sleep(1)

    _cleanup_project_switch_bridges()
    _cleanup_project_interfaces()


def require_controller_reachable(controller_ip, controller_port):
    """Require the Ryu OpenFlow endpoint before creating SDN switches."""
    try:
        with socket.create_connection((controller_ip, controller_port), timeout=1.0):
            return
    except OSError:
        raise SystemExit(
            "Ryu is not reachable at "
            f"{controller_ip}:{controller_port}.\n\n"
            "Start the controller first:\n"
            "  ryu-manager --observe-links controller_app.py\n\n"
            "Then clean any partial Mininet state and retry:\n"
            "  sudo mn -c\n"
            "  sudo python3 topology.py\n"
        )


def find_missing_images(container_specs):
    """Return local Docker image tags that are required but not built."""
    required_images = sorted({spec["image"] for spec in container_specs.values()})
    missing_images = []

    for image in required_images:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            missing_images.append(image)

    return missing_images


def require_local_images(container_specs):
    missing_images = find_missing_images(container_specs)
    if not missing_images:
        return

    missing_list = "\n".join(f"  - {image}" for image in missing_images)
    raise SystemExit(
        "Missing local Docker images for the topology:\n"
        f"{missing_list}\n\n"
        "Build them from the LLM_MTD_emo project root first:\n"
        "  bash scripts/build_images.sh\n"
    )


def add_container_nodes(net, switches, container_specs):
    """Create Docker hosts from container specs and link each interface."""
    containers = {}

    for node_name, spec in container_specs.items():
        container = net.addDocker(
            node_name,
            dimage=spec["image"],
            dcmd=spec["command"],
            ip=spec["interfaces"][0][1],
            environment=spec["environment"],
            **spec["resource_profile"]["docker"],
        )
        containers[node_name] = container

        for interface_index, (switch_name, ip_address) in enumerate(spec["interfaces"]):
            net.addLink(
                container,
                switches[switch_name],
                intfName1=container_interface_name(node_name, interface_index),
                params1={"ip": ip_address},
            )

    return containers


def configure_interdomain_routes(net, container_specs):
    """Add on-link routes so fixed /24 domains can communicate by SDN switching."""
    all_subnets = sorted(set(SUBNET_BY_SWITCH.values()))

    info("*** Adding inter-domain host routes\n")
    for node_name, spec in container_specs.items():
        host = net.get(node_name)
        primary_intf = container_interface_name(node_name, 0)
        direct_subnets = {
            SUBNET_BY_SWITCH[switch_name] for switch_name, _ in spec["interfaces"]
        }

        if not host.cmd("command -v ip").strip():
            raise RuntimeError(
                f"{node_name} does not have the ip command. "
                "Rebuild images with bash scripts/build_images.sh."
            )

        for subnet in all_subnets:
            if subnet in direct_subnets:
                continue
            host.cmd(f"ip route replace {subnet} dev {primary_intf}")


def build_network(
    controller_ip=CONTROLLER_IP,
    controller_port=CONTROLLER_PORT,
    service_images=None,
):
    """Build the Containernet topology without starting it."""
    cleanup_stale_state()
    container_specs = make_container_specs(service_images=service_images)
    require_local_images(container_specs)
    require_controller_reachable(controller_ip, controller_port)

    net = Containernet(controller=RemoteController, switch=OVSSwitch)
    net.container_specs = container_specs

    info("*** Adding remote Ryu controller\n")
    net.addController(
        CONTROLLER_NAME,
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
    )

    info("*** Adding OVS switches\n")
    switches = {
        switch_name: net.addSwitch(
            switch_name,
            cls=OVSSwitch,
            dpid=SWITCH_DPIDS[switch_name],
            protocols=OPENFLOW_VERSION,
        )
        for switch_name in SWITCH_DPIDS
    }

    info("*** Adding backbone links\n")
    net.addLink(switches["s_edge1"], switches["s_core"])
    net.addLink(switches["s_edge2"], switches["s_core"])
    net.addLink(switches["s_edge3"], switches["s_core"])
    net.addLink(switches["s_cloud"], switches["s_core"])

    info("*** Adding sensor containers\n")
    add_container_nodes(
        net,
        switches,
        {node_name: container_specs[node_name] for node_name in SENSOR_NODE_MAP},
    )

    info("*** Adding edge containers\n")
    add_container_nodes(
        net,
        switches,
        {node_name: container_specs[node_name] for node_name in EDGE_NODE_MAP},
    )

    info("*** Adding cloud containers\n")
    add_container_nodes(
        net,
        switches,
        {node_name: container_specs[node_name] for node_name in CLOUD_NODE_MAP},
    )

    return net


def trigger_arp_discovery(net, container_specs):
    """Ping the gateway from every host so Ryu discovers all hosts via ARP."""
    info("*** Triggering ARP discovery from all hosts\n")
    gateway_ips = set()
    for spec in container_specs.values():
        for _switch_name, ip_cidr in spec["interfaces"]:
            ip = ip_cidr.split("/")[0]
            octets = ip.rsplit(".", 1)
            if len(octets) == 2:
                gateway_ips.add(f"{octets[0]}.1")

    for node_name in container_specs:
        host = net.get(node_name)
        for gw_ip in sorted(gateway_ips):
            host.cmd(f"ping -c 1 -W 1 {gw_ip} > /dev/null 2>&1 &")


def main():
    """Start the topology and open the Mininet CLI."""
    setLogLevel("info")
    net = build_network()

    try:
        info("*** Starting LLM_MTD_emo topology\n")
        net.start()
        configure_interdomain_routes(net, net.container_specs)
        trigger_arp_discovery(net, net.container_specs)
        CLI(net)
    finally:
        info("*** Stopping LLM_MTD_emo topology\n")
        net.stop()


if __name__ == "__main__":
    main()
