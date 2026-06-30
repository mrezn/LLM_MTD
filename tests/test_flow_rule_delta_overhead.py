from game.game_model import dc_proxy
from game.state_builder import build_state, query_ryu_defense_state


DEFENDER = {"base_cost": 0.2, "defense_cost": {"resource": 0.2}}


def test_active_flow_count_is_separate_from_cumulative_total_and_costed():
    defense = query_ryu_defense_state(
        mtd_status_data={"active_actions": [{
            "action": "quarantine_sensor", "target": "sen4", "flow_rules_installed": 4
        }]},
        controller_metrics={"ryu_controller_flow_rules_installed_total": 40},
    )
    assert defense["flow_rules_installed"] == 40
    assert defense["active_mtd_flow_count"] == 4

    cumulative_only = dc_proxy(DEFENDER, {"overhead": {"flow_rules_installed": 40}})
    active_cost = dc_proxy(DEFENDER, {"overhead": {
        "flow_rules_installed": 40,
        "flow_rules_installed_delta": 0,
        "active_mtd_flow_count": 4,
    }})
    assert active_cost > cumulative_only


def test_state_builder_reports_per_stage_counter_deltas():
    state = build_state(
        core_data={},
        mtd_metrics_text=(
            "ryu_controller_flow_rules_installed_total 40\n"
            "ryu_controller_meters_added_total 3\n"
        ),
        mtd_status_data={},
        constraints={
            "flow_rules_installed_baseline": 20,
            "meters_added_baseline": 1,
        },
    )
    assert state["overhead"]["flow_rules_installed_total"] == 40
    assert state["overhead"]["flow_rules_installed_delta"] == 20
    assert state["overhead"]["meters_added_total"] == 3
    assert state["overhead"]["meters_added_delta"] == 2
