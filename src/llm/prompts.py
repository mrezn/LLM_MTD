import json


def build_macro_prompt(context):
    payload = {
        "active_keys": context["active_keys"],
        "q": context["q"],
        "pool_keys": context["pool_keys"],
        "attacker_p": context["p"],
        "xi": context["xi"],
        "xi_by_hop": context["xi_by_hop"],
        "sal_mean": context["sal_mean"],
        "sap_mean": context["sap_mean"],
        "dc_breakdown": context["dc_breakdown"],
        "ac_breakdown": context["ac_breakdown"],
        "recent_promotions": context["recent_promotions"],
        "recent_demotions": context["recent_demotions"],
        "constraints": context["constraints"],
        "output_schema": {
            "macro_probs": {"GD1": 0.34, "GD2": 0.33, "GD3": 0.33},
            "promote_key": "GD5",
            "demote_keys": ["GD2"],
            "mutation": [[0.9, 0.1, 0.0], [0.1, 0.8, 0.1], [0.0, 0.2, 0.8]],
            "notes": "short rationale",
        },
    }
    return (
        "Return ONLY valid JSON matching output_schema. "
        "Keys must match the active set exactly and rows must sum to 1.\n"
        + json.dumps(payload)
    )


def build_summary_prompt(context):
    return (
        "Write a concise episode summary.\n"
        f"episode: {context['episode']}\n"
        f"path: {context['path']}\n"
        f"state_sequence: {context['state_sequence']}\n"
        f"top_defenders: {context['top_defenders']}\n"
        f"chosen_defender: {context['chosen_defender']}\n"
        f"attacker_p: {context['attacker_p']}\n"
        f"xi_values: {context['xi_values']}\n"
        f"costs: {context['costs']}\n"
        f"payoffs: {context['payoffs']}\n"
    )
