from __future__ import annotations

from math import exp, sqrt


def vo2_at_velocity(v: float) -> float:
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def pct_vo2max(t: float) -> float:
    return 0.8 + 0.1894393 * exp(-0.012778 * t) + 0.2989558 * exp(-0.1932605 * t)


def vdot_from_race(distance_m: float, time_s: float) -> float:
    t = time_s / 60.0
    v = distance_m / t
    return vo2_at_velocity(v) / pct_vo2max(t)


def velocity_at_vo2(vo2: float) -> float:
    a = 0.000104
    b = 0.182258
    c = -4.60 - vo2
    discriminant = float(b * b - 4 * a * c)
    numerator = float(-b + sqrt(discriminant))
    return numerator / (2.0 * float(a))


def riegel_time(t1_s: float, d1_m: float, d2_m: float, exponent: float = 1.06) -> float:
    if d1_m <= 0:
        raise ValueError("d1_m must be positive")
    return float(t1_s * (d2_m / d1_m) ** exponent)
