"""Pure helpers for the holidays shown on the calendar page.

The country comes from the weather location: the nearest entry of the tz
database ``zone.tab`` (country code and coordinates of each zone) decides it.
Nothing in this module performs I/O; the caller reads ``zone.tab`` and builds
the holiday mapping.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, List, Mapping, Optional, Tuple

_ISO6709 = re.compile(
    r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$"
)
_EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class ZoneSpot:
    """A time zone's reference point and the country it belongs to."""

    country: str
    latitude: float
    longitude: float


def parse_iso6709(text: str) -> Optional[Tuple[float, float]]:
    """Parse ``+DDMM+DDDMM`` or ``+DDMMSS+DDDMMSS`` into (latitude, longitude)."""
    match = _ISO6709.match(text.strip())
    if match is None:
        return None
    lat_sign, lat_d, lat_m, lat_s, lon_sign, lon_d, lon_m, lon_s = match.groups()
    latitude = int(lat_d) + int(lat_m) / 60 + int(lat_s or 0) / 3600
    longitude = int(lon_d) + int(lon_m) / 60 + int(lon_s or 0) / 3600
    if lat_sign == "-":
        latitude = -latitude
    if lon_sign == "-":
        longitude = -longitude
    return latitude, longitude


def parse_zone_tab(text: str) -> List[ZoneSpot]:
    """Read the rows of a ``zone.tab`` file; comments and bad rows are skipped."""
    spots: List[ZoneSpot] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) < 3:
            continue
        country = columns[0].strip().upper()
        point = parse_iso6709(columns[1])
        if len(country) != 2 or not country.isalpha() or point is None:
            continue
        spots.append(ZoneSpot(country, point[0], point[1]))
    return spots


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lat = p2 - p1
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lon / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def nearest_country(
    spots: List[ZoneSpot], latitude: float, longitude: float
) -> Optional[str]:
    """Country code of the zone closest to a point; None when there are no zones."""
    best: Optional[str] = None
    best_distance = math.inf
    for spot in spots:
        distance = _distance_km(latitude, longitude, spot.latitude, spot.longitude)
        if distance < best_distance:
            best, best_distance = spot.country, distance
    return best


def clean_country(value: Any) -> str:
    """Two letter country code from a setting; empty when it is not one."""
    if not isinstance(value, str):
        return ""
    code = value.strip().upper()
    return code if len(code) == 2 and code.isalpha() else ""


def holiday_on(holidays: Mapping[date, str], day: date) -> str:
    """Name of the holiday on ``day``, empty when there is none."""
    return holidays.get(day, "")


def next_holiday(
    holidays: Mapping[date, str], today: date
) -> Optional[Tuple[date, str, int]]:
    """The first holiday on or after ``today`` as (date, name, days away)."""
    upcoming = [day for day in holidays if day >= today]
    if not upcoming:
        return None
    day = min(upcoming)
    return day, holidays[day], (day - today).days


def format_days_until(days: int) -> str:
    """``today``, ``tomorrow`` or ``in N days``."""
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days"
