"""Pure helpers for the weather plugin.

Parsing, unit conversion and forecast summarising live here so they can be
tested without GTK or the network. Nothing in this module performs I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from typing import Any, List, Mapping, Optional, Tuple
from urllib.parse import urlencode

FORECAST_ENDPOINT = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
GEOCODE_ENDPOINT = "https://geocoding-api.open-meteo.com/v1/search"

DEFAULT_LOCATION_NAME = "São Paulo"
DEFAULT_LATITUDE = -23.5505
DEFAULT_LONGITUDE = -46.6333
DEFAULT_USER_AGENT = "JFShellWeatherPlugin/2.0 github.com/killown/jfshell"

TEMPERATURE_UNITS = ("celsius", "fahrenheit")
WIND_UNITS = ("m/s", "km/h", "mph", "kn")
PRECIPITATION_UNITS = ("mm", "in")

MIN_REFRESH_MINUTES = 10
MAX_REFRESH_MINUTES = 360
DEFAULT_REFRESH_MINUTES = 30
MIN_HOURS_SHOWN = 4
MAX_HOURS_SHOWN = 24
DEFAULT_HOURS_SHOWN = 12

_WIND_FACTORS = {"m/s": 1.0, "km/h": 3.6, "mph": 2.2369363, "kn": 1.9438445}
_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
_PLACEHOLDER = "–"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WeatherSettings:
    """Sanitised user settings for the weather plugin."""

    location_name: str = DEFAULT_LOCATION_NAME
    latitude: float = DEFAULT_LATITUDE
    longitude: float = DEFAULT_LONGITUDE
    altitude: Optional[int] = None
    temperature_unit: str = "celsius"
    wind_unit: str = "m/s"
    precipitation_unit: str = "mm"
    refresh_minutes: int = DEFAULT_REFRESH_MINUTES
    hours_shown: int = DEFAULT_HOURS_SHOWN
    clock_24h: bool = True
    user_agent: str = DEFAULT_USER_AGENT

    @property
    def place_label(self) -> str:
        """Name to show in the header, falling back to the coordinates."""
        if self.location_name:
            return self.location_name
        return f"{self.latitude:.2f}, {self.longitude:.2f}"


def parse_coordinate(value: Any, lower: float, upper: float) -> Optional[float]:
    """Parse a coordinate from a number or string; None when invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if number != number or not lower <= number <= upper:
        return None
    # MET Norway asks for at most four decimals; it also keeps the cache useful.
    return round(number, 4)


def parse_latitude(value: Any) -> Optional[float]:
    return parse_coordinate(value, -90.0, 90.0)


def parse_longitude(value: Any) -> Optional[float]:
    return parse_coordinate(value, -180.0, 180.0)


def parse_altitude(value: Any) -> Optional[int]:
    """Parse an altitude in metres. Empty or invalid input means 'not set'."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        metres = int(round(float(text.replace(",", "."))))
    except ValueError:
        return None
    if not -500 <= metres <= 9000:
        return None
    return metres


def legacy_coordinates(raw: Any) -> Optional[Tuple[float, float]]:
    """Read the old ``[lat, lon]`` pair stored under the calendar plugin."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    latitude = parse_latitude(raw[0])
    longitude = parse_longitude(raw[1])
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _pick(value: Any, allowed: Tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else default


def _clamp_int(value: Any, lower: int, upper: int, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, number))


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    return default


def build_settings(raw: Mapping[str, Any]) -> WeatherSettings:
    """Turn raw config values into valid settings.

    Unknown or invalid values fall back to defaults instead of raising, so a
    typo in the config file never breaks the plugin.
    """
    latitude = parse_latitude(raw.get("latitude"))
    longitude = parse_longitude(raw.get("longitude"))
    if latitude is None or longitude is None:
        latitude, longitude = DEFAULT_LATITUDE, DEFAULT_LONGITUDE
    name = raw.get("location_name")
    agent = str(raw.get("user_agent") or "").strip()
    return WeatherSettings(
        location_name=str(name).strip() if name is not None else "",
        latitude=latitude,
        longitude=longitude,
        altitude=parse_altitude(raw.get("altitude")),
        temperature_unit=_pick(raw.get("temperature_unit"), TEMPERATURE_UNITS, "celsius"),
        wind_unit=_pick(raw.get("wind_unit"), WIND_UNITS, "m/s"),
        precipitation_unit=_pick(raw.get("precipitation_unit"), PRECIPITATION_UNITS, "mm"),
        refresh_minutes=_clamp_int(
            raw.get("refresh_minutes"),
            MIN_REFRESH_MINUTES,
            MAX_REFRESH_MINUTES,
            DEFAULT_REFRESH_MINUTES,
        ),
        hours_shown=_clamp_int(
            raw.get("hours_shown"), MIN_HOURS_SHOWN, MAX_HOURS_SHOWN, DEFAULT_HOURS_SHOWN
        ),
        clock_24h=_parse_bool(raw.get("clock_24h"), True),
        user_agent=agent or DEFAULT_USER_AGENT,
    )


# --------------------------------------------------------------------------- #
# URLs and HTTP dates
# --------------------------------------------------------------------------- #


def forecast_url(latitude: float, longitude: float, altitude: Optional[int]) -> str:
    """MET Norway locationforecast URL; altitude is optional."""
    query = {"lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}"}
    if altitude is not None:
        query["altitude"] = str(int(altitude))
    return f"{FORECAST_ENDPOINT}?{urlencode(query)}"


def geocode_url(query: str, count: int = 6, language: str = "en") -> str:
    """Open-Meteo geocoding URL for a free-text place search."""
    params = {
        "name": query.strip(),
        "count": str(max(1, min(count, 10))),
        "language": language,
        "format": "json",
    }
    return f"{GEOCODE_ENDPOINT}?{urlencode(params)}"


def parse_http_date(text: Optional[str]) -> Optional[datetime]:
    """Parse an HTTP date header such as Expires; None when missing/invalid."""
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------- #
# Geocoding
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Place:
    """A geocoding match."""

    name: str
    region: str
    country: str
    latitude: float
    longitude: float
    elevation: Optional[int] = None

    @property
    def label(self) -> str:
        parts: List[str] = []
        for part in (self.name, self.region, self.country):
            if part and part not in parts:
                parts.append(part)
        return ", ".join(parts)


def parse_places(data: Any) -> List[Place]:
    """Parse an Open-Meteo geocoding response. Bad entries are skipped."""
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    places: List[Place] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        latitude = parse_latitude(item.get("latitude"))
        longitude = parse_longitude(item.get("longitude"))
        name = str(item.get("name") or "").strip()
        if latitude is None or longitude is None or not name:
            continue
        places.append(
            Place(
                name=name,
                region=str(item.get("admin1") or "").strip(),
                country=str(item.get("country") or "").strip(),
                latitude=latitude,
                longitude=longitude,
                elevation=parse_altitude(item.get("elevation")),
            )
        )
    return places


# --------------------------------------------------------------------------- #
# Units and formatting
# --------------------------------------------------------------------------- #


def to_temperature(celsius: float, unit: str) -> float:
    return celsius * 9.0 / 5.0 + 32.0 if unit == "fahrenheit" else celsius


def to_wind(metres_per_second: float, unit: str) -> float:
    return metres_per_second * _WIND_FACTORS.get(unit, 1.0)


def to_precipitation(millimetres: float, unit: str) -> float:
    return millimetres / 25.4 if unit == "in" else millimetres


def temperature_suffix(unit: str) -> str:
    return "°F" if unit == "fahrenheit" else "°C"


def _number(value: float, digits: int) -> str:
    text = f"{value:.{digits}f}"
    # Avoid "-0" after rounding.
    if text.startswith("-") and float(text) == 0:
        return text[1:]
    return text


def format_temperature(
    celsius: Optional[float], unit: str, digits: int = 0, with_unit: bool = False
) -> str:
    if celsius is None:
        return _PLACEHOLDER
    text = _number(to_temperature(celsius, unit), digits)
    return f"{text}{temperature_suffix(unit)}" if with_unit else f"{text}°"


def format_wind(
    metres_per_second: Optional[float], unit: str, direction: Optional[float] = None
) -> str:
    if metres_per_second is None:
        return _PLACEHOLDER
    text = f"{_number(to_wind(metres_per_second, unit), 1)} {unit}"
    point = compass_point(direction)
    return f"{text} {point}" if point else text


def format_precipitation(millimetres: Optional[float], unit: str) -> str:
    if millimetres is None:
        return _PLACEHOLDER
    digits = 2 if unit == "in" else 1
    return f"{_number(to_precipitation(millimetres, unit), digits)} {unit}"


def format_percent(value: Optional[float]) -> str:
    return _PLACEHOLDER if value is None else f"{value:.0f}%"


def format_pressure(hectopascal: Optional[float]) -> str:
    return _PLACEHOLDER if hectopascal is None else f"{hectopascal:.0f} hPa"


def format_uv(index: Optional[float]) -> str:
    if index is None:
        return _PLACEHOLDER
    if index < 3:
        band = "Low"
    elif index < 6:
        band = "Moderate"
    elif index < 8:
        band = "High"
    elif index < 11:
        band = "Very high"
    else:
        band = "Extreme"
    return f"{index:.0f} · {band}"


def compass_point(degrees: Optional[float]) -> str:
    """Eight-point compass label for a direction in degrees ('' if unknown)."""
    if degrees is None:
        return ""
    return _COMPASS[int(((degrees % 360) + 22.5) // 45) % 8]


def format_clock(moment: datetime, twenty_four_hour: bool = True) -> str:
    if twenty_four_hour:
        return moment.strftime("%H:%M")
    return moment.strftime("%I %p").lstrip("0")


# --------------------------------------------------------------------------- #
# Condition symbols
# --------------------------------------------------------------------------- #

_BASE_LABELS = {
    "clearsky": "Clear sky",
    "fair": "Fair",
    "partlycloudy": "Partly cloudy",
    "cloudy": "Cloudy",
    "fog": "Fog",
    "rain": "Rain",
    "sleet": "Sleet",
    "snow": "Snow",
}


def split_symbol(code: Optional[str]) -> Tuple[str, str]:
    """Split ``rain_night`` into ``("rain", "night")``."""
    base, _, variant = (code or "").partition("_")
    return base, variant


def symbol_emoji(code: Optional[str]) -> str:
    base, variant = split_symbol(code)
    night = variant == "night"
    if not base:
        return "🌡️"
    if "thunder" in base:
        return "⛈️"
    if "snow" in base:
        return "❄️" if base.startswith("heavy") else "🌨️"
    if "sleet" in base:
        return "🌨️"
    if "rain" in base:
        return "🌦️" if "showers" in base and not night else "🌧️"
    if base == "clearsky":
        return "🌙" if night else "☀️"
    if base == "fair":
        return "🌙" if night else "🌤️"
    if base == "partlycloudy":
        return "☁️" if night else "⛅"
    if base == "cloudy":
        return "☁️"
    if base == "fog":
        return "🌫️"
    return "🌡️"


def symbol_label(code: Optional[str]) -> str:
    """Readable condition text for a MET symbol code."""
    base = split_symbol(code)[0]
    thunder = base.endswith("andthunder")
    if thunder:
        base = base[: -len("andthunder")]
    showers = base.endswith("showers")
    if showers:
        base = base[: -len("showers")]
    intensity = ""
    for prefix in ("light", "heavy"):
        if base.startswith(prefix):
            intensity = prefix
            base = base[len(prefix) :]
            break
    core = _BASE_LABELS.get(base)
    if core is None:
        return "Unknown"
    text = f"{intensity.capitalize()} {core.lower()}" if intensity else core
    if showers:
        text += " showers"
    if thunder:
        text += " and thunder"
    return text


# --------------------------------------------------------------------------- #
# Forecast summary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HourPoint:
    """One hourly slot of the forecast strip."""

    time: datetime
    temperature: Optional[float]
    symbol: str
    precipitation: float


@dataclass(frozen=True)
class Forecast:
    """Everything the weather page shows, in SI units (°C, m/s, mm, hPa)."""

    temperature: Optional[float]
    symbol: str
    wind_speed: Optional[float]
    wind_direction: Optional[float]
    humidity: Optional[float]
    pressure: Optional[float]
    cloud_cover: Optional[float]
    uv_index: Optional[float]
    low: Optional[float]
    high: Optional[float]
    rain_total: float
    rain_soon: List[Tuple[datetime, float]] = field(default_factory=list)
    hours: List[HourPoint] = field(default_factory=list)
    updated: Optional[datetime] = None


def parse_time(text: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp as an aware datetime (UTC if no zone)."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _details(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    data = entry.get("data")
    instant = data.get("instant") if isinstance(data, dict) else None
    details = instant.get("details") if isinstance(instant, dict) else None
    return details if isinstance(details, dict) else {}


def _period(entry: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    data = entry.get("data")
    period = data.get(name) if isinstance(data, dict) else None
    return period if isinstance(period, dict) else {}


def _entry_symbol(entry: Mapping[str, Any]) -> str:
    for name in ("next_1_hours", "next_6_hours", "next_12_hours"):
        summary = _period(entry, name).get("summary")
        if isinstance(summary, dict) and summary.get("symbol_code"):
            return str(summary["symbol_code"])
    return ""


def _entry_rain(entry: Mapping[str, Any]) -> float:
    details = _period(entry, "next_1_hours").get("details")
    amount = details.get("precipitation_amount") if isinstance(details, dict) else None
    return float(amount) if isinstance(amount, (int, float)) else 0.0


def summarize_forecast(
    data: Any,
    tz: Optional[tzinfo] = None,
    hours: int = DEFAULT_HOURS_SHOWN,
    now: Optional[datetime] = None,
) -> Optional[Forecast]:
    """Reduce a MET Norway locationforecast response to what the page shows.

    Args:
        data: Parsed JSON response.
        tz: Zone used for the returned times; the system zone when None.
        hours: Number of hourly slots for the strip.
        now: Current time, injectable for tests. Slots that ended before it
            are dropped, so a cached response never shows the past.
    Returns:
        A Forecast, or None when the response has no usable time series.
    """
    properties = data.get("properties") if isinstance(data, dict) else None
    series = properties.get("timeseries") if isinstance(properties, dict) else None
    if not isinstance(series, list):
        return None
    slots: List[Tuple[datetime, Mapping[str, Any]]] = []
    for entry in series:
        moment = parse_time(entry.get("time")) if isinstance(entry, dict) else None
        if moment is not None:
            slots.append((moment, entry))
    if not slots:
        return None
    slots.sort(key=lambda slot: slot[0])

    reference = now or datetime.now(timezone.utc)
    start = 0
    for index, (moment, _entry) in enumerate(slots):
        if moment <= reference:
            start = index
    slots = slots[start:]

    first_time, first = slots[0]
    now_details = _details(first)
    window = [slot for slot in slots if slot[0] < first_time + timedelta(hours=24)]
    soon_limit = first_time + timedelta(hours=6)

    temps = [
        t
        for _m, e in window
        if isinstance(t := _details(e).get("air_temperature"), (int, float))
    ]
    rain_total = 0.0
    rain_soon: List[Tuple[datetime, float]] = []
    for moment, entry in window:
        amount = _entry_rain(entry)
        rain_total += amount
        if amount > 0 and moment < soon_limit:
            rain_soon.append((moment.astimezone(tz), amount))

    strip: List[HourPoint] = []
    for moment, entry in slots[: max(1, hours)]:
        temperature = _details(entry).get("air_temperature")
        strip.append(
            HourPoint(
                time=moment.astimezone(tz),
                temperature=temperature if isinstance(temperature, (int, float)) else None,
                symbol=_entry_symbol(entry),
                precipitation=_entry_rain(entry),
            )
        )

    def number(key: str) -> Optional[float]:
        value = now_details.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    meta = properties.get("meta") if isinstance(properties, dict) else None
    updated = parse_time(meta.get("updated_at")) if isinstance(meta, dict) else None
    return Forecast(
        temperature=number("air_temperature"),
        symbol=_entry_symbol(first),
        wind_speed=number("wind_speed"),
        wind_direction=number("wind_from_direction"),
        humidity=number("relative_humidity"),
        pressure=number("air_pressure_at_sea_level"),
        cloud_cover=number("cloud_area_fraction"),
        uv_index=number("ultraviolet_index_clear_sky"),
        low=min(temps) if temps else None,
        high=max(temps) if temps else None,
        rain_total=rain_total,
        rain_soon=rain_soon,
        hours=strip,
        updated=updated.astimezone(tz) if updated else None,
    )


def describe_rain_soon(
    rain_soon: List[Tuple[datetime, float]], unit: str, twenty_four_hour: bool = True
) -> str:
    """One-line summary of rain expected in the next six hours."""
    if not rain_soon:
        return "No rain expected in the next 6 hours"
    parts = [
        f"{format_clock(moment, twenty_four_hour)} {format_precipitation(amount, unit)}"
        for moment, amount in rain_soon[:4]
    ]
    extra = len(rain_soon) - len(parts)
    text = "Rain expected: " + " · ".join(parts)
    return f"{text} · +{extra} more" if extra > 0 else text
