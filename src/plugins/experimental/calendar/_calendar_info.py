"""Pure helpers for the header and the extras of the calendar page.

Day facts, sunrise and sunset, the moon phase and the world clocks are
computed here so they can be tested without GTK. Nothing in this module
performs I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SYNODIC_MONTH = 29.530588853
# A known new moon, the moon age is counted from it.
_REFERENCE_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

_PHASE_NAMES = (
    "New moon",
    "Waxing crescent",
    "First quarter",
    "Waxing gibbous",
    "Full moon",
    "Waning gibbous",
    "Last quarter",
    "Waning crescent",
)
_PHASE_EMOJI = ("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘")

DEFAULT_WORLD_CLOCKS = ("America/New_York", "Europe/London", "Asia/Tokyo")
MAX_WORLD_CLOCKS = 4


# --------------------------------------------------------------------------- #
# Day facts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DayFacts:
    """Where a date falls in its year."""

    week: int
    day_of_year: int
    days_left: int


def day_facts(day: date) -> DayFacts:
    """ISO week number, day of the year and days remaining after ``day``."""
    return DayFacts(
        week=day.isocalendar()[1],
        day_of_year=day.timetuple().tm_yday,
        days_left=(date(day.year, 12, 31) - day).days,
    )


def format_day_summary(facts: DayFacts) -> str:
    """One line such as ``Week 48 · Day 328 · 37 days left``."""
    unit = "day" if facts.days_left == 1 else "days"
    return f"Week {facts.week} · Day {facts.day_of_year} · {facts.days_left} {unit} left"


# --------------------------------------------------------------------------- #
# Sun
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SunTimes:
    """Sunrise and sunset as aware UTC datetimes.

    ``state`` is ``normal``, ``polar_day`` (the sun does not set) or
    ``polar_night`` (the sun does not rise); the times are None unless normal.
    """

    sunrise: Optional[datetime]
    sunset: Optional[datetime]
    state: str = "normal"


def sun_times(day: date, latitude: float, longitude: float) -> SunTimes:
    """Sunrise and sunset for a date and place (NOAA approximation).

    Accurate to a few minutes, which is enough for a panel. East positive
    longitude, latitude in degrees from -90 to 90.
    """
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    lat = math.radians(latitude)
    cos_ha = math.cos(math.radians(90.833)) / (
        math.cos(lat) * math.cos(decl)
    ) - math.tan(lat) * math.tan(decl)
    if cos_ha > 1:
        return SunTimes(None, None, "polar_night")
    if cos_ha < -1:
        return SunTimes(None, None, "polar_day")
    hour_angle = math.degrees(math.acos(cos_ha))
    midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    sunrise = midnight + timedelta(minutes=720 - 4 * (longitude + hour_angle) - eqtime)
    sunset = midnight + timedelta(minutes=720 - 4 * (longitude - hour_angle) - eqtime)
    return SunTimes(sunrise, sunset)


# --------------------------------------------------------------------------- #
# Moon
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MoonPhase:
    """Phase of the moon at a moment."""

    age_days: float
    illumination: float
    name: str
    emoji: str


def moon_phase(moment: datetime) -> MoonPhase:
    """Moon phase from the mean synodic month (within about a day)."""
    elapsed = (moment - _REFERENCE_NEW_MOON).total_seconds() / 86400
    age = elapsed % SYNODIC_MONTH
    fraction = age / SYNODIC_MONTH
    index = int(fraction * 8 + 0.5) % 8
    return MoonPhase(
        age_days=age,
        illumination=(1 - math.cos(2 * math.pi * fraction)) / 2,
        name=_PHASE_NAMES[index],
        emoji=_PHASE_EMOJI[index],
    )


# --------------------------------------------------------------------------- #
# World clocks
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorldClock:
    """One zone ready to show: a label, the time and a day note."""

    label: str
    time: str
    note: str


def clean_zone_list(value: Any) -> List[str]:
    """Zone names from a setting; anything that is not a list of names is dropped."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    zones = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return zones[:MAX_WORLD_CLOCKS]


def zone_label(zone: str) -> str:
    """``America/Sao_Paulo`` -> ``Sao Paulo``."""
    return zone.rsplit("/", 1)[-1].replace("_", " ")


def world_clocks(zones: Sequence[str], now: datetime) -> List[WorldClock]:
    """The time in each zone at ``now`` (aware).

    Unknown zones are skipped. The note says ``yesterday`` or ``tomorrow``
    when the date there differs from the date of ``now``.
    """
    clocks: List[WorldClock] = []
    for zone in zones:
        try:
            there = now.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
        offset = (there.date() - now.date()).days
        note = {-1: "yesterday", 1: "tomorrow"}.get(offset, "")
        clocks.append(WorldClock(zone_label(zone), there.strftime("%H:%M"), note))
    return clocks
