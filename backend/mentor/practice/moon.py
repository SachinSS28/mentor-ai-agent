"""New and full moon times (Meeus, Astronomical Algorithms, 2nd ed., chapter 49), for Uposatha.

Accurate to about a minute, which is plenty to tell which calendar day a phase falls on.
Monastery calendars follow their own lunar reckoning and can differ by a day.
"""

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Literal

Phase = Literal["new moon", "full moon"]

J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)  # Julian Ephemeris Day 2451545.0
TT_MINUS_UTC = timedelta(seconds=69)  # Terrestrial Time runs ahead of UTC (2020s)

# Periodic terms: (coefficient for new moon, coefficient for full moon, E power, argument).
# Arguments use M (Sun's anomaly), Mp (Moon's anomaly), F (latitude), N (ascending node).
type Argument = Callable[[float, float, float, float], float]

TERMS: list[tuple[float, float, int, Argument]] = [
    (-0.40720, -0.40614, 0, lambda M, Mp, F, N: Mp),
    (0.17241, 0.17302, 1, lambda M, Mp, F, N: M),
    (0.01608, 0.01614, 0, lambda M, Mp, F, N: 2 * Mp),
    (0.01039, 0.01043, 0, lambda M, Mp, F, N: 2 * F),
    (0.00739, 0.00734, 1, lambda M, Mp, F, N: Mp - M),
    (-0.00514, -0.00515, 1, lambda M, Mp, F, N: Mp + M),
    (0.00208, 0.00209, 2, lambda M, Mp, F, N: 2 * M),
    (-0.00111, -0.00111, 0, lambda M, Mp, F, N: Mp - 2 * F),
    (-0.00057, -0.00057, 0, lambda M, Mp, F, N: Mp + 2 * F),
    (0.00056, 0.00056, 1, lambda M, Mp, F, N: 2 * Mp + M),
    (-0.00042, -0.00042, 0, lambda M, Mp, F, N: 3 * Mp),
    (0.00042, 0.00042, 1, lambda M, Mp, F, N: M + 2 * F),
    (0.00038, 0.00038, 1, lambda M, Mp, F, N: M - 2 * F),
    (-0.00024, -0.00024, 1, lambda M, Mp, F, N: 2 * Mp - M),
    (-0.00017, -0.00017, 0, lambda M, Mp, F, N: N),
    (-0.00007, -0.00007, 0, lambda M, Mp, F, N: Mp + 2 * M),
    (0.00004, 0.00004, 0, lambda M, Mp, F, N: 2 * Mp - 2 * F),
    (0.00004, 0.00004, 0, lambda M, Mp, F, N: 3 * M),
    (0.00003, 0.00003, 0, lambda M, Mp, F, N: Mp + M - 2 * F),
    (0.00003, 0.00003, 0, lambda M, Mp, F, N: 2 * Mp + 2 * F),
    (-0.00003, -0.00003, 0, lambda M, Mp, F, N: Mp + M + 2 * F),
    (0.00003, 0.00003, 0, lambda M, Mp, F, N: Mp - M + 2 * F),
    (-0.00002, -0.00002, 0, lambda M, Mp, F, N: Mp - M - 2 * F),
    (-0.00002, -0.00002, 0, lambda M, Mp, F, N: 3 * Mp + M),
    (0.00002, 0.00002, 0, lambda M, Mp, F, N: 4 * Mp),
]

# Planetary arguments (degrees: base, rate per lunation) and their coefficients.
PLANETARY = [
    (299.77, 0.107408, 0.000325),
    (251.88, 0.016321, 0.000165),
    (251.83, 26.651886, 0.000164),
    (349.42, 36.412478, 0.000126),
    (84.66, 18.206239, 0.000110),
    (141.74, 53.303771, 0.000062),
    (207.14, 2.453732, 0.000060),
    (154.84, 7.306860, 0.000056),
    (34.52, 27.261239, 0.000047),
    (207.19, 0.121824, 0.000042),
    (291.34, 1.844379, 0.000040),
    (161.72, 24.198154, 0.000037),
    (239.56, 25.513099, 0.000035),
    (331.55, 3.592518, 0.000023),
]


def phase_time(k: float) -> datetime:
    """UTC time of the phase with lunation number k (integer: new moon, +0.5: full moon;
    k = 0 is the new moon of 6 January 2000)."""
    t = k / 1236.85
    jde = (
        2451550.09766
        + 29.530588861 * k
        + 0.00015437 * t**2
        - 0.000000150 * t**3
        + 0.00000000073 * t**4
    )
    e = 1 - 0.002516 * t - 0.0000074 * t**2
    M = math.radians(2.5534 + 29.10535670 * k - 0.0000014 * t**2 - 0.00000011 * t**3)
    Mp = math.radians(
        201.5643 + 385.81693528 * k + 0.0107582 * t**2 + 0.00001238 * t**3 - 0.000000058 * t**4
    )
    F = math.radians(
        160.7108 + 390.67050284 * k - 0.0016118 * t**2 - 0.00000227 * t**3 + 0.000000011 * t**4
    )
    N = math.radians(124.7746 - 1.56375588 * k + 0.0020672 * t**2 + 0.00000215 * t**3)
    full = k % 1 != 0
    jde += sum(
        (full_c if full else new_c) * e**power * math.sin(argument(M, Mp, F, N))
        for new_c, full_c, power, argument in TERMS
    )
    jde += math.sin(math.radians(299.77 + 0.107408 * k - 0.009173 * t**2)) * PLANETARY[0][2]
    jde += sum(
        coefficient * math.sin(math.radians(base + rate * k))
        for base, rate, coefficient in PLANETARY[1:]
    )
    return J2000 + timedelta(days=jde - 2451545.0) - TT_MINUS_UTC


def uposatha_on(day: date, tz: tzinfo) -> Phase | None:
    """'full moon' or 'new moon' if that phase falls on `day` in the given time zone."""
    years_since_2000 = (day - date(2000, 1, 6)).days / 365.25
    around = math.floor(years_since_2000 * 12.3685)
    for k in range(around - 1, around + 3):
        for offset, phase in ((0.0, "new moon"), (0.5, "full moon")):
            if phase_time(k + offset).astimezone(tz).date() == day:
                return phase  # type: ignore[return-value]
    return None
