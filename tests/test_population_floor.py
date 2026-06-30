from game.game_model import apply_population_floor, replicator_update


def test_population_floor_prevents_silent_extinction():
    floored = apply_population_floor({"A1": 1.0, "A2": 0.0}, epsilon=0.01)
    assert floored["A2"] >= 0.01
    updated, _ = replicator_update(floored, {"A1": 10, "A2": -10}, eta=1.0, epsilon=0.01)
    assert updated["A2"] >= 0.01
    assert abs(sum(updated.values()) - 1.0) < 1e-9
