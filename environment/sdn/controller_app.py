"""Single Ryu controller app for the LLM_MTD_emo topology.

Run with:

    ryu-manager --observe-links controller_app.py

The app keeps baseline forwarding and MTD enforcement in one controller:

- table 0: defense policy rules installed by the REST API
- table 1: default L2 learning-switch forwarding
"""

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_link, get_switch
from webob import Response

from environment.network.network_model import (
    ARCHITECTURE_NODE_MAP,
    NODE_INTERFACE_IPS,
    SUBNETS,
    SWITCH_NAME_BY_DPID,
    node_ips,
)


REST_CONTEXT_KEY = "llm_mtd_controller_app"
POLICY_TABLE = 0
FORWARDING_TABLE = 1
LEARNING_PRIORITY = 100
DEFENSE_PRIORITY = 40000
POLICY_COOKIE_BASE = 0xA11CE00000000000
ACTION_ALIASES = {
    "rate_limit_sensor": "rate_limit",
    "reroute_sensor": "reroute_traffic",
}
MANAGED_HOST_NETWORKS = tuple(
    ip_network(subnet)
    for subnet in (
        SUBNETS["edge1"],
        SUBNETS["edge2"],
        SUBNETS["edge3"],
        SUBNETS["cloud"],
    )
)


def json_response(payload, status=200):
    return Response(
        content_type="application/json",
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        status=status,
    )


def text_response(payload, status=200):
    return Response(
        content_type="text/plain; version=0.0.4",
        body=payload.encode("utf-8"),
        status=status,
    )


def metric_line(name, value, labels=None):
    label_text = ""
    if labels:
        rendered = [
            f'{key}="{str(label_value).replace(chr(34), chr(92) + chr(34))}"'
            for key, label_value in labels.items()
        ]
        label_text = "{" + ",".join(rendered) + "}"
    return f"{name}{label_text} {value}"


def dpid_name(dpid):
    return SWITCH_NAME_BY_DPID.get(dpid, f"unknown:{dpid}")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def is_managed_host_ip(value):
    try:
        address = ip_address(value)
    except ValueError:
        return False
    return any(address in network for network in MANAGED_HOST_NETWORKS)


class LlmMtdController(app_manager.RyuApp):
    """One central SDN controller for baseline forwarding and MTD actions."""

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self.mac_to_port = {}
        self.host_locations = {}
        self.host_interface_locations = {}
        self.link_ports = {}
        self.switch_ports = {}
        self.active_actions = {}
        self.controller_metrics = {
            "actions_total": 0,
            "flow_rules_installed_total": 0,
            "flow_delete_commands_total": 0,
            "meters_added_total": 0,
            "last_action_duration_ms": 0.0,
            "last_action": {},
        }

        wsgi = kwargs["wsgi"]
        wsgi.register(MtdRestApi, {REST_CONTEXT_KEY: self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self._register_datapath(datapath)
        self._install_default_pipeline(datapath)

    @set_ev_cls(
        ofp_event.EventOFPStateChange,
        [MAIN_DISPATCHER, DEAD_DISPATCHER],
    )
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self._register_datapath(datapath)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)
            self.logger.info(
                "Switch disconnected: %s dpid=%s",
                dpid_name(datapath.id),
                datapath.id,
            )

    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, _ev):
        self._refresh_topology()

    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, _ev):
        self._refresh_topology()

    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, _ev):
        self._refresh_topology()

    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, _ev):
        self._refresh_topology()

    @set_ev_cls(event.EventHostAdd)
    def host_add_handler(self, ev):
        host = ev.host
        if host.port:
            for ip_address in host.ipv4:
                self._remember_host(
                    ip_address,
                    host.port.dpid,
                    host.port.port_no,
                    host.mac,
                )

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src = eth.src
        dst = eth.dst
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        self._learn_ip_location(pkt, dpid, in_port, src)

        out_port = self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_src=src, eth_dst=dst)
            self.add_flow(
                datapath,
                priority=LEARNING_PRIORITY,
                match=match,
                actions=actions,
                table_id=FORWARDING_TABLE,
                idle_timeout=60,
            )

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    def _register_datapath(self, datapath):
        self.datapaths[datapath.id] = datapath
        self.mac_to_port.setdefault(datapath.id, {})
        self.logger.info(
            "Switch registered: %s dpid=%s",
            dpid_name(datapath.id),
            datapath.id,
        )

    def _install_default_pipeline(self, datapath):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        self.add_flow(
            datapath,
            priority=0,
            match=parser.OFPMatch(),
            instructions=[parser.OFPInstructionGotoTable(FORWARDING_TABLE)],
            table_id=POLICY_TABLE,
        )

        controller_actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]
        self.add_flow(
            datapath,
            priority=0,
            match=parser.OFPMatch(),
            actions=controller_actions,
            table_id=FORWARDING_TABLE,
        )

    def add_flow(
        self,
        datapath,
        priority,
        match,
        actions=None,
        instructions=None,
        table_id=POLICY_TABLE,
        idle_timeout=0,
        hard_timeout=0,
        cookie=0,
    ):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto

        if instructions is None:
            instructions = []
            if actions is not None:
                instructions.append(parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS,
                    actions,
                ))

        mod = parser.OFPFlowMod(
            datapath=datapath,
            cookie=cookie,
            table_id=table_id,
            priority=priority,
            match=match,
            instructions=instructions,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        datapath.send_msg(mod)

    def _refresh_topology(self):
        self.switch_ports = {
            switch.dp.id: sorted(port.port_no for port in switch.ports)
            for switch in get_switch(self, None)
        }
        self.link_ports = {}

        for link in get_link(self, None):
            self.link_ports[(link.src.dpid, link.dst.dpid)] = {
                "src_port": link.src.port_no,
                "dst_port": link.dst.port_no,
                "src_switch": dpid_name(link.src.dpid),
                "dst_switch": dpid_name(link.dst.dpid),
            }

        self.logger.info(
            "Topology refreshed: switches=%s links=%s",
            len(self.switch_ports),
            len(self.link_ports),
        )

    def _learn_ip_location(self, pkt, dpid, port, mac):
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is not None and arp_pkt.src_ip != "0.0.0.0":
            self._remember_host(arp_pkt.src_ip, dpid, port, mac)

        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
        if ipv4_pkt is not None:
            self._remember_host(ipv4_pkt.src, dpid, port, mac)

    def _remember_host(self, ip_address, dpid, port, mac):
        if not is_managed_host_ip(ip_address):
            return

        location = {
            "dpid": dpid,
            "port": port,
            "mac": mac,
            "node": self._node_name_for_ip(ip_address),
        }
        self.host_locations[ip_address] = location
        self.host_interface_locations[(ip_address, dpid)] = location

    def _node_name_for_ip(self, ip_address):
        for node_name in ARCHITECTURE_NODE_MAP:
            if ip_address in node_ips(node_name):
                return node_name
        return None

    def controller_status(self):
        static_known_hosts = {}
        for node_name, interfaces in NODE_INTERFACE_IPS.items():
            for switch_name, cidr_address in interfaces:
                ip_address = cidr_address.split("/", maxsplit=1)[0]
                static_known_hosts[ip_address] = {
                    "dpid": next(
                        (dpid for dpid, name in SWITCH_NAME_BY_DPID.items() if name == switch_name),
                        0,
                    ),
                    "port": None,
                    "mac": "",
                    "node": node_name,
                    "switch": switch_name,
                    "source": "static",
                }
        merged_known_hosts = dict(static_known_hosts)
        for ip_address, location in self.host_locations.items():
            if not is_managed_host_ip(ip_address):
                continue
            merged_known_hosts[ip_address] = {
                **merged_known_hosts.get(ip_address, {}),
                **location,
                "switch": dpid_name(location.get("dpid")),
                "source": (
                    "merged"
                    if ip_address in static_known_hosts
                    else "dynamic"
                ),
            }
        missing_dynamic_host_ips = sorted(
            ip_address for ip_address in static_known_hosts
            if ip_address not in self.host_locations
        )
        missing_dynamic_host_nodes = sorted(
            {
                merged_known_hosts[ip_address].get("node")
                for ip_address in missing_dynamic_host_ips
                if merged_known_hosts.get(ip_address, {}).get("node")
            }
        )
        return {
            "switches": [
                {
                    "dpid": dpid,
                    "name": dpid_name(dpid),
                    "ports": self.switch_ports.get(dpid, []),
                    "connected": dpid in self.datapaths,
                }
                for dpid in sorted(set(self.datapaths) | set(self.switch_ports))
            ],
            "links": [
                {
                    "src_dpid": src_dpid,
                    "dst_dpid": dst_dpid,
                    **details,
                }
                for (src_dpid, dst_dpid), details in sorted(self.link_ports.items())
            ],
            "known_hosts": {
                ip_address: location
                for ip_address, location in sorted(merged_known_hosts.items())
                if is_managed_host_ip(ip_address)
            },
            "known_host_interfaces": [
                {
                    "ip": ip_address,
                    "switch": dpid_name(dpid),
                    **location,
                }
                for (ip_address, dpid), location in sorted(
                    self.host_interface_locations.items()
                )
                if is_managed_host_ip(ip_address)
            ],
            "expected_nodes": {
                node_name: [
                    {"switch": switch_name, "ip": ip_address}
                    for switch_name, ip_address in NODE_INTERFACE_IPS[node_name]
                ]
                for node_name in sorted(ARCHITECTURE_NODE_MAP)
            },
            "subnets": SUBNETS,
            "known_host_diagnostics": {
                "expected_host_count": len(static_known_hosts),
                "observed_dynamic_host_count": len(
                    [ip for ip in self.host_locations if is_managed_host_ip(ip)]
                ),
                "merged_known_host_count": len(merged_known_hosts),
                "missing_dynamic_host_ips": missing_dynamic_host_ips,
                "missing_dynamic_host_nodes": missing_dynamic_host_nodes,
                "known_host_source": "merged",
            },
            "active_actions": self.active_actions,
            "controller_metrics": self.controller_metrics,
        }

    def controller_metric_lines(self):
        metrics = self.controller_metrics
        lines = [
            metric_line("ryu_controller_actions_total", metrics["actions_total"], {}),
            metric_line(
                "ryu_controller_flow_rules_installed_total",
                metrics["flow_rules_installed_total"],
                {},
            ),
            metric_line(
                "ryu_controller_flow_delete_commands_total",
                metrics["flow_delete_commands_total"],
                {},
            ),
            metric_line("ryu_controller_meters_added_total", metrics["meters_added_total"], {}),
            metric_line(
                "ryu_controller_last_action_duration_ms",
                metrics["last_action_duration_ms"],
                {},
            ),
            metric_line("ryu_controller_active_policy_actions", len(self.active_actions), {}),
        ]
        for action_id, action_state in sorted(self.active_actions.items()):
            labels = {
                "action_id": action_id,
                "action": action_state.get("action", "unknown"),
                "target": action_state.get("target", ""),
            }
            lines.append(metric_line("ryu_controller_active_policy_entry", 1, labels))
        return lines

    def apply_defense_action(self, request_body):
        request_received_at = utc_now_iso()
        started_at = time.monotonic()
        payload, status = self._apply_defense_action_core(request_body)
        return self._finalize_action_response(
            payload,
            status,
            request_received_at,
            started_at,
        )

    def _apply_defense_action_core(self, request_body):
        request_body = self._normalize_action_request(request_body)
        action = request_body.get("action", "").strip()
        target = str(request_body.get("target", "") or "").strip()

        if not action:
            return {"error": "missing action"}, 400

        if action == "observe":
            return {
                "status": "accepted",
                "action": "observe",
                "message": "no OpenFlow rule needed for observe",
                "received": request_body,
                "flow_rules_installed": 0,
                "flow_delete_commands": 0,
                "meters_added": 0,
            }, 202

        if action in ("quarantine_sensor", "isolate_sensor"):
            return self._apply_isolation(action, target, request_body)

        if action in ("release_sensor", "clear_quarantine", "clear_target_policy"):
            return self._clear_target_policy(target)
        if action == "clear_all_policies":
            return self._clear_all_policies()

        if action == "rate_limit":
            return self._apply_rate_limit(target, request_body)

        if action == "reroute_traffic":
            return self._apply_dual_homed_reroute(target, request_body)

        if action in ("migrate_worker_traffic", "change_sensor_ip_handling"):
            return {
                "status": "not_installed",
                "reason": (
                    f"{action} needs an app-level mapping or address-rewrite model "
                    "before a safe OpenFlow rule can be generated"
                ),
                "received": request_body,
            }, 501

        return {"error": f"unsupported action: {action}"}, 400

    def _finalize_action_response(self, payload, status, request_received_at, started_at):
        if not isinstance(payload, dict):
            return payload, status

        duration_ms = (time.monotonic() - started_at) * 1000
        payload.setdefault("ryu_request_received_at", request_received_at)
        payload.setdefault("ryu_flow_mods_enqueued_at", utc_now_iso())
        payload.setdefault("ryu_apply_duration_ms", duration_ms)
        payload.setdefault("active_policy_actions", len(self.active_actions))

        if status < 400:
            flow_rules = int(payload.get("flow_rules_installed", 0) or 0)
            delete_commands = int(payload.get("flow_delete_commands", 0) or 0)
            meters_added = int(payload.get("meters_added", 0) or 0)

            self.controller_metrics["actions_total"] += 1
            self.controller_metrics["flow_rules_installed_total"] += flow_rules
            self.controller_metrics["flow_delete_commands_total"] += delete_commands
            self.controller_metrics["meters_added_total"] += meters_added
            self.controller_metrics["last_action_duration_ms"] = duration_ms
            self.controller_metrics["last_action"] = {
                "action": payload.get("action", "unknown"),
                "target": payload.get("target", ""),
                "status": payload.get("status", "accepted"),
                "flow_rules_installed": flow_rules,
                "flow_delete_commands": delete_commands,
                "meters_added": meters_added,
                "duration_ms": duration_ms,
                "request_received_at": request_received_at,
                "flow_mods_enqueued_at": payload["ryu_flow_mods_enqueued_at"],
            }
            self.logger.info(
                "MTD action action=%s target=%s status=%s duration_ms=%.3f "
                "flows=%s deletes=%s meters=%s active=%s",
                payload.get("action", "unknown"),
                payload.get("target", ""),
                payload.get("status", "accepted"),
                duration_ms,
                flow_rules,
                delete_commands,
                meters_added,
                len(self.active_actions),
            )

        return payload, status

    def _normalize_action_request(self, request_body):
        normalized = dict(request_body)
        nested_action = request_body.get("selected_action") or request_body.get("ryu_intent")

        if isinstance(nested_action, dict):
            normalized.setdefault("action", nested_action.get("action") or nested_action.get("type"))
            normalized.setdefault("target", nested_action.get("target", ""))
            normalized.setdefault("source_decision", nested_action)

        action = str(normalized.get("action", "") or "")
        normalized["action"] = ACTION_ALIASES.get(action, action)
        return normalized

    def _apply_isolation(self, action, target, request_body):
        target_ips, error_response = self._target_ips(target)
        if error_response:
            return error_response

        action_id = request_body.get("action_id") or self._new_action_id(action, target)
        cookie = self._cookie_for(action_id)
        flow_rules_installed = 0

        for datapath in self.datapaths.values():
            for ip_address in target_ips:
                flow_rules_installed += self._install_ip_drop(datapath, ip_address, cookie)

        self.active_actions[action_id] = {
            "action": action,
            "target": target,
            "target_ips": target_ips,
            "cookie": hex(cookie),
            "status": "installed",
            "rule": "drop ipv4 and arp traffic to/from target on all switches",
            "flow_rules_installed": flow_rules_installed,
            "flow_delete_commands": 0,
            "meters_added": 0,
        }
        return self.active_actions[action_id], 202

    def _apply_rate_limit(self, target, request_body):
        target_ips, error_response = self._target_ips(target)
        if error_response:
            return error_response

        kbps = int(request_body.get("kbps", 256))
        burst_size = int(request_body.get("burst_size", max(kbps, 1)))
        action_id = request_body.get("action_id") or self._new_action_id(
            "rate_limit",
            target,
        )
        cookie = self._cookie_for(action_id)
        meter_id = self._meter_id_for(action_id)
        flow_rules_installed = 0
        meters_added = 0

        for datapath in self.datapaths.values():
            meters_added += self._upsert_meter(datapath, meter_id, kbps, burst_size)
            for ip_address in target_ips:
                flow_rules_installed += self._install_metered_goto(
                    datapath,
                    ip_address,
                    meter_id,
                    cookie,
                )

        self.active_actions[action_id] = {
            "action": "rate_limit",
            "target": target,
            "target_ips": target_ips,
            "kbps": kbps,
            "burst_size": burst_size,
            "meter_id": meter_id,
            "cookie": hex(cookie),
            "status": "installed",
            "rule": "meter ipv4 source traffic, then continue to default forwarding",
            "flow_rules_installed": flow_rules_installed,
            "flow_delete_commands": 0,
            "meters_added": meters_added,
        }
        return self.active_actions[action_id], 202

    def _apply_dual_homed_reroute(self, target, request_body):
        if target not in NODE_INTERFACE_IPS:
            return {"error": f"unknown target: {target}"}, 404

        interfaces = NODE_INTERFACE_IPS[target]
        if len(interfaces) < 2:
            return {"error": f"{target} is not dual-homed"}, 409

        via = request_body.get("via") or request_body.get("preferred_switch")
        if not via:
            via = interfaces[-1][0]

        blocked_interfaces = [
            (switch_name, ip_address)
            for switch_name, ip_address in interfaces
            if switch_name != via
        ]
        if not blocked_interfaces:
            return {"error": f"{target} has no alternate interface outside {via}"}, 409

        action_id = request_body.get("action_id") or self._new_action_id(
            "reroute_traffic",
            target,
        )
        cookie = self._cookie_for(action_id)
        installed_on = []
        target_ips = list(node_ips(target))
        flow_rules_installed = 0

        for switch_name, cidr_address in blocked_interfaces:
            datapath = self._datapath_for_switch(switch_name)
            if datapath is None:
                continue

            ip_address = cidr_address.split("/", maxsplit=1)[0]
            flow_rules_installed += self._install_ip_drop(datapath, ip_address, cookie)
            entry = {
                "switch": switch_name,
                "blocked_ip": cidr_address,
                "ip_rule": "installed",
            }

            location = self._host_location_on_switch(ip_address, datapath.id)
            if location:
                flow_rules_installed += self._install_ingress_port_drop(
                    datapath,
                    location["port"],
                    cookie,
                )
                entry["port_rule"] = "installed"
                entry["blocked_port"] = location["port"]
                entry["blocked_mac"] = location["mac"]
            else:
                entry["port_rule"] = "pending_host_location"

            gateway_ip = self._gateway_ip_for_switch(switch_name)
            if gateway_ip:
                for source_ip in target_ips:
                    flow_rules_installed += self._install_endpoint_pair_drop(
                        datapath,
                        source_ip,
                        gateway_ip,
                        cookie,
                    )
                entry["blocked_gateway"] = gateway_ip
                entry["gateway_pair_rules"] = "installed"

            installed_on.append(entry)

        self.active_actions[action_id] = {
            "action": "reroute_traffic",
            "target": target,
            "preferred_switch": via,
            "installed_on": installed_on,
            "cookie": hex(cookie),
            "status": "installed" if installed_on else "pending_switch_connection",
            "rule": (
                "drop the non-preferred dual-homed sensor interface and "
                "block target traffic to the non-preferred gateway"
            ),
            "flow_rules_installed": flow_rules_installed,
            "flow_delete_commands": 0,
            "meters_added": 0,
        }
        return self.active_actions[action_id], 202

    def _clear_target_policy(self, target):
        if not target:
            return {"error": "missing target"}, 400

        removed = []
        flow_delete_commands = 0
        meters_deleted = 0
        for action_id, action_state in list(self.active_actions.items()):
            if action_state.get("target") != target:
                continue

            cookie = int(action_state["cookie"], 16)
            for datapath in self.datapaths.values():
                flow_delete_commands += self._delete_policy_cookie(datapath, cookie)
                meter_id = action_state.get("meter_id")
                if meter_id:
                    meters_deleted += self._delete_meter(datapath, int(meter_id))
            removed.append(action_id)
            del self.active_actions[action_id]

        return {
            "status": "cleared",
            "action": "clear_target_policy",
            "target": target,
            "removed_actions": removed,
            "flow_rules_installed": 0,
            "flow_delete_commands": flow_delete_commands,
            "meters_added": 0,
            "meters_deleted": meters_deleted,
        }, 202

    def _clear_all_policies(self):
        removed = []
        flow_delete_commands = 0
        meters_deleted = 0
        for action_id, action_state in list(self.active_actions.items()):
            cookie = int(action_state["cookie"], 16)
            meter_id = action_state.get("meter_id")
            for datapath in self.datapaths.values():
                flow_delete_commands += self._delete_policy_cookie(datapath, cookie)
                if meter_id:
                    meters_deleted += self._delete_meter(datapath, int(meter_id))
            removed.append(action_id)
            del self.active_actions[action_id]

        return {
            "status": "cleared",
            "action": "clear_all_policies",
            "removed_actions": removed,
            "flow_rules_installed": 0,
            "flow_delete_commands": flow_delete_commands,
            "meters_added": 0,
            "meters_deleted": meters_deleted,
        }, 202

    def _target_ips(self, target):
        if not target:
            return None, ({"error": "missing target"}, 400)

        if target in NODE_INTERFACE_IPS:
            return list(node_ips(target)), None

        if target.count(".") == 3:
            return [target], None

        return None, ({"error": f"unknown target: {target}"}, 404)

    def _datapath_for_switch(self, switch_name):
        for dpid, datapath in self.datapaths.items():
            if dpid_name(dpid) == switch_name:
                return datapath
        return None

    def _gateway_ip_for_switch(self, switch_name):
        for node_name, interfaces in NODE_INTERFACE_IPS.items():
            if not node_name.endswith("_gw"):
                continue
            for interface_switch, cidr_address in interfaces:
                if interface_switch == switch_name:
                    return cidr_address.split("/", maxsplit=1)[0]
        return None

    def _host_location_on_switch(self, ip_address, dpid):
        location = self.host_interface_locations.get((ip_address, dpid))
        if location is None:
            return None

        link_ports = self._link_ports_for_switch(dpid)
        if not link_ports:
            return None

        if location["port"] in link_ports:
            return None

        return location

    def _link_ports_for_switch(self, dpid):
        ports = set()
        for (src_dpid, dst_dpid), details in self.link_ports.items():
            if src_dpid == dpid:
                ports.add(details["src_port"])
            if dst_dpid == dpid:
                ports.add(details["dst_port"])
        return ports

    def _install_ip_drop(self, datapath, ip_address, cookie):
        parser = datapath.ofproto_parser
        matches = [
            parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=ip_address),
            parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_dst=ip_address),
            parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP, arp_spa=ip_address),
            parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP, arp_tpa=ip_address),
        ]
        installed = 0
        for match in matches:
            self.add_flow(
                datapath,
                priority=DEFENSE_PRIORITY,
                match=match,
                instructions=[],
                table_id=POLICY_TABLE,
                cookie=cookie,
            )
            installed += 1
        return installed

    def _install_ingress_port_drop(self, datapath, port, cookie):
        parser = datapath.ofproto_parser
        self.add_flow(
            datapath,
            priority=DEFENSE_PRIORITY + 10,
            match=parser.OFPMatch(in_port=port),
            instructions=[],
            table_id=POLICY_TABLE,
            cookie=cookie,
        )
        return 1

    def _install_endpoint_pair_drop(self, datapath, source_ip, destination_ip, cookie):
        parser = datapath.ofproto_parser
        matches = [
            parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=source_ip,
                ipv4_dst=destination_ip,
            ),
            parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=destination_ip,
                ipv4_dst=source_ip,
            ),
        ]
        for match in matches:
            self.add_flow(
                datapath,
                priority=DEFENSE_PRIORITY + 5,
                match=match,
                instructions=[],
                table_id=POLICY_TABLE,
                cookie=cookie,
            )
        return len(matches)

    def _install_metered_goto(self, datapath, ip_address, meter_id, cookie):
        parser = datapath.ofproto_parser
        match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=ip_address,
        )
        instructions = [
            parser.OFPInstructionMeter(meter_id, datapath.ofproto.OFPIT_METER),
            parser.OFPInstructionGotoTable(FORWARDING_TABLE),
        ]
        self.add_flow(
            datapath,
            priority=DEFENSE_PRIORITY - 100,
            match=match,
            instructions=instructions,
            table_id=POLICY_TABLE,
            cookie=cookie,
        )
        return 1

    def _upsert_meter(self, datapath, meter_id, kbps, burst_size):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        bands = [parser.OFPMeterBandDrop(rate=kbps, burst_size=burst_size)]

        mod = parser.OFPMeterMod(
            datapath=datapath,
            command=ofproto.OFPMC_ADD,
            flags=ofproto.OFPMF_KBPS,
            meter_id=meter_id,
            bands=bands,
        )
        datapath.send_msg(mod)
        return 1

    def _delete_policy_cookie(self, datapath, cookie):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        mod = parser.OFPFlowMod(
            datapath=datapath,
            cookie=cookie,
            cookie_mask=0xFFFFFFFFFFFFFFFF,
            table_id=POLICY_TABLE,
            command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY,
            out_group=ofproto.OFPG_ANY,
        )
        datapath.send_msg(mod)
        return 1

    def _delete_meter(self, datapath, meter_id):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        mod = parser.OFPMeterMod(
            datapath=datapath,
            command=ofproto.OFPMC_DELETE,
            flags=ofproto.OFPMF_KBPS,
            meter_id=meter_id,
            bands=[],
        )
        datapath.send_msg(mod)
        return 1

    def _new_action_id(self, action, target):
        return f"{action}:{target}:{uuid.uuid4().hex[:12]}"

    def _cookie_for(self, action_id):
        return POLICY_COOKIE_BASE | (uuid.uuid5(uuid.NAMESPACE_URL, action_id).int & 0x0000FFFFFFFFFFFF)

    def _meter_id_for(self, action_id):
        return 1 + (uuid.uuid5(uuid.NAMESPACE_DNS, action_id).int % 0xFFFF)


class MtdRestApi(ControllerBase):
    """REST facade for cloud_policy decisions."""

    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.controller_app = data[REST_CONTEXT_KEY]

    @route("llm_mtd", "/mtd/status", methods=["GET"])
    def status(self, _req, **_kwargs):
        return json_response(self.controller_app.controller_status())

    @route("llm_mtd", "/mtd/actions", methods=["GET"])
    def actions(self, _req, **_kwargs):
        return json_response({"active_actions": self.controller_app.active_actions})

    @route("llm_mtd", "/mtd/actions", methods=["DELETE"])
    def clear_actions(self, _req, **_kwargs):
        payload, status = self.controller_app._clear_all_policies()
        return json_response(payload, status=status)

    @route("llm_mtd", "/mtd/metrics", methods=["GET"])
    def metrics(self, _req, **_kwargs):
        return text_response("\n".join(self.controller_app.controller_metric_lines()) + "\n")

    @route("llm_mtd", "/mtd/action", methods=["POST"])
    def action(self, req, **_kwargs):
        try:
            request_body = json.loads(req.body.decode("utf-8") or "{}")
        except json.JSONDecodeError as error:
            return json_response({"error": f"invalid json: {error}"}, status=400)

        payload, status = self.controller_app.apply_defense_action(request_body)
        return json_response(payload, status=status)
