"""Phase 5 placeholder for logical active/pool promotion and demotion."""

from __future__ import annotations

from ..types import ActivePoolState, ActivePoolUpdate


def apply_active_pool_update(
    state: ActivePoolState,
    update: ActivePoolUpdate,
) -> ActivePoolState:
    if not update.enabled:
        return state
    active = list(state.active_strategies)
    pool = list(state.pool_strategies)
    for promote in update.promote:
        if promote not in active:
            active.append(promote)
        if promote in pool:
            pool.remove(promote)
    for demote in update.demote:
        if demote in active:
            active.remove(demote)
        if demote not in pool:
            pool.append(demote)
    return ActivePoolState(
        enabled=True,
        active_strategies=active,
        pool_strategies=pool,
    )
