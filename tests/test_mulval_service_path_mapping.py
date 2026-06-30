from attacker.mulval.parser import expand_scenario_service_paths


def test_direct_gateway_cloud_path_expands_through_assigned_worker():
    topology = {"connectivity": [
        {"src": "edge2_gw", "dst": "edge2_vm_s4", "reason": "gateway_to_worker_for_sen4"},
        {"src": "edge2_vm_s4", "dst": "cloud_db", "reason": "edge_to_cloud_via_core"},
    ]}
    assert expand_scenario_service_paths(
        [["sen4", "edge2_gw", "cloud_db"]], topology
    ) == [["sen4", "edge2_gw", "edge2_vm_s4", "cloud_db"]]
