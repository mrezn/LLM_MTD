"""Containernet topology for the LLM_MTD_emo SDN deployment."""

import socket
import subprocess

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Containernet
from mininet.node import OVSSwitch, RemoteController

from network_model import (
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


def main():
    """Start the topology and open the Mininet CLI."""
    setLogLevel("info")
    net = build_network()

    try:
        info("*** Starting LLM_MTD_emo topology\n")
        net.start()
        configure_interdomain_routes(net, net.container_specs)
        CLI(net)
    finally:
        info("*** Stopping LLM_MTD_emo topology\n")
        net.stop()


if __name__ == "__main__":
    main()
