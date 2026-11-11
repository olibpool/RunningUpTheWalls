from runcoach.engine.vdot import vdot_from_race, velocity_at_vo2, vo2_at_velocity


def test_vdot_5k_and_10k_approx() -> None:
    assert 49.0 < vdot_from_race(5000, 20 * 60) < 50.5
    assert 37.5 < vdot_from_race(5000, 25 * 60) < 39.5
    assert 50.0 < vdot_from_race(10000, 40 * 60) < 53.5


def test_velocity_inverts_vo2() -> None:
    v = 200.0
    assert abs(velocity_at_vo2(vo2_at_velocity(v)) - v) < 1e-6
