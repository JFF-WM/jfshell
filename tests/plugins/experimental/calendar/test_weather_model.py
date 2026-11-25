from datetime import datetime, timedelta, timezone

from src.plugins.experimental.calendar._weather_model import (
    DEFAULT_LATITUDE,
    DEFAULT_LONGITUDE,
    DEFAULT_USER_AGENT,
    build_settings,
    compass_point,
    describe_rain_soon,
    forecast_url,
    format_precipitation,
    format_temperature,
    format_uv,
    format_wind,
    geocode_url,
    legacy_coordinates,
    parse_altitude,
    parse_http_date,
    parse_latitude,
    parse_longitude,
    parse_places,
    summarize_forecast,
    symbol_emoji,
    symbol_label,
)

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))
START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _entry(hour, temp, rain=None, symbol="cloudy_day", **extra):
    details = {"air_temperature": temp}
    details.update(extra)
    data = {"instant": {"details": details}}
    if rain is not None:
        data["next_1_hours"] = {
            "summary": {"symbol_code": symbol},
            "details": {"precipitation_amount": rain},
        }
    time = (START + timedelta(hours=hour)).isoformat().replace("+00:00", "Z")
    return {"time": time, "data": data}


def _response(entries):
    return {
        "properties": {
            "meta": {"updated_at": "2026-11-24T11:50:00Z"},
            "timeseries": entries,
        }
    }


# --- settings --------------------------------------------------------------


def test_coordinates_accept_numbers_and_strings():
    assert parse_latitude("-23.5505") == -23.5505
    assert parse_longitude(-46.6333) == -46.6333
    assert parse_latitude("12,5") == 12.5


def test_coordinates_reject_garbage_and_out_of_range():
    assert parse_latitude("abc") is None
    assert parse_latitude("91") is None
    assert parse_longitude("-181") is None
    assert parse_latitude(None) is None
    assert parse_latitude(True) is None
    assert parse_latitude("nan") is None


def test_coordinates_are_rounded_to_four_decimals():
    assert parse_latitude("52.5200066") == 52.52


def test_altitude_is_optional():
    assert parse_altitude("") is None
    assert parse_altitude(None) is None
    assert parse_altitude("760.4") == 760
    assert parse_altitude("99999") is None


def test_legacy_coordinates_need_a_valid_pair():
    assert legacy_coordinates(["-23.5505", "-46.6333"]) == (-23.5505, -46.6333)
    assert legacy_coordinates(["-23.5505"]) is None
    assert legacy_coordinates(["x", "y"]) is None
    assert legacy_coordinates("-23.5,-46.6") is None


def test_build_settings_defaults_when_values_are_missing():
    settings = build_settings({})
    assert (settings.latitude, settings.longitude) == (
        DEFAULT_LATITUDE,
        DEFAULT_LONGITUDE,
    )
    assert settings.temperature_unit == "celsius"
    assert settings.user_agent == DEFAULT_USER_AGENT
    assert settings.altitude is None


def test_build_settings_ignores_a_half_valid_location():
    settings = build_settings({"latitude": "10", "longitude": "oops"})
    assert (settings.latitude, settings.longitude) == (
        DEFAULT_LATITUDE,
        DEFAULT_LONGITUDE,
    )


def test_build_settings_clamps_and_validates():
    settings = build_settings(
        {
            "latitude": 52.52,
            "longitude": 13.405,
            "location_name": " Berlin ",
            "temperature_unit": "Fahrenheit",
            "wind_unit": "furlongs",
            "refresh_minutes": 1,
            "hours_shown": 99,
        }
    )
    assert settings.location_name == "Berlin"
    assert settings.temperature_unit == "fahrenheit"
    assert settings.wind_unit == "m/s"
    assert settings.refresh_minutes == 10
    assert settings.hours_shown == 24


def test_clock_format_setting_accepts_text_booleans():
    assert build_settings({}).clock_24h is True
    assert build_settings({"clock_24h": "false"}).clock_24h is False
    assert build_settings({"clock_24h": False}).clock_24h is False
    assert build_settings({"clock_24h": "maybe"}).clock_24h is True


def test_place_label_falls_back_to_coordinates():
    assert build_settings({"location_name": ""}).place_label == "-23.55, -46.63"
    assert build_settings({"location_name": "Rio"}).place_label == "Rio"


# --- urls and headers ------------------------------------------------------


def test_forecast_url_includes_altitude_only_when_set():
    assert forecast_url(-23.5505, -46.6333, None).endswith("lat=-23.5505&lon=-46.6333")
    assert forecast_url(1.0, 2.0, 760).endswith("lat=1.0000&lon=2.0000&altitude=760")


def test_geocode_url_encodes_the_query():
    url = geocode_url("São Paulo", count=50)
    assert "name=S%C3%A3o+Paulo" in url
    assert "count=10" in url


def test_parse_http_date():
    parsed = parse_http_date("Tue, 24 Nov 2026 12:30:00 GMT")
    assert parsed == datetime(2026, 11, 24, 12, 30, tzinfo=UTC)
    assert parse_http_date("not a date") is None
    assert parse_http_date(None) is None


# --- geocoding -------------------------------------------------------------


def test_parse_places_skips_bad_entries_and_builds_labels():
    data = {
        "results": [
            {
                "name": "Berlin",
                "admin1": "Berlin",
                "country": "Germany",
                "latitude": 52.52437,
                "longitude": 13.41053,
                "elevation": 74.0,
            },
            {"name": "Broken", "latitude": "x", "longitude": 1},
            "junk",
        ]
    }
    places = parse_places(data)
    assert len(places) == 1
    assert places[0].label == "Berlin, Germany"
    assert places[0].elevation == 74


def test_parse_places_without_results():
    assert parse_places({"generationtime_ms": 0.5}) == []
    assert parse_places(None) == []


# --- formatting ------------------------------------------------------------


def test_temperature_formatting_and_units():
    assert format_temperature(23.7, "celsius") == "24°"
    assert format_temperature(23.7, "celsius", digits=1, with_unit=True) == "23.7°C"
    assert format_temperature(0.0, "fahrenheit", with_unit=True) == "32°F"
    assert format_temperature(-0.2, "celsius") == "0°"
    assert format_temperature(None, "celsius") == "–"


def test_wind_precipitation_and_uv_formatting():
    assert format_wind(3.9, "m/s") == "3.9 m/s"
    assert format_wind(10.0, "km/h", 90) == "36.0 km/h E"
    assert format_precipitation(25.4, "in") == "1.00 in"
    assert format_precipitation(0.5, "mm") == "0.5 mm"
    assert format_uv(7.2) == "7 · High"


def test_compass_points_wrap_around():
    assert compass_point(0) == "N"
    assert compass_point(359) == "N"
    assert compass_point(225) == "SW"
    assert compass_point(None) == ""


def test_symbols_map_to_emoji_and_labels():
    assert symbol_emoji("clearsky_night") == "🌙"
    assert symbol_emoji("heavyrainandthunder") == "⛈️"
    assert symbol_emoji("") == "🌡️"
    assert symbol_label("lightrainshowers_day") == "Light rain showers"
    assert symbol_label("rainandthunder") == "Rain and thunder"
    assert symbol_label("partlycloudy_night") == "Partly cloudy"
    assert symbol_label("mystery") == "Unknown"


# --- forecast --------------------------------------------------------------


def _day():
    entries = [
        _entry(
            0,
            20.0,
            rain=0.0,
            symbol="partlycloudy_day",
            wind_speed=3.0,
            wind_from_direction=180.0,
            relative_humidity=80.0,
            air_pressure_at_sea_level=1013.0,
            cloud_area_fraction=40.0,
            ultraviolet_index_clear_sky=6.0,
        ),
        _entry(1, 22.0, rain=0.4, symbol="rain"),
        _entry(2, 25.0, rain=0.0),
        _entry(7, 18.0, rain=1.0),
        _entry(30, 5.0, rain=9.0),
    ]
    return _response(entries)


def test_summary_reads_current_conditions():
    forecast = summarize_forecast(_day(), tz=UTC, now=START)
    assert forecast.temperature == 20.0
    assert forecast.symbol == "partlycloudy_day"
    assert forecast.wind_speed == 3.0
    assert forecast.humidity == 80.0
    assert forecast.pressure == 1013.0
    assert forecast.uv_index == 6.0
    assert forecast.updated == datetime(2026, 11, 24, 11, 50, tzinfo=UTC)


def test_summary_window_is_24_hours():
    forecast = summarize_forecast(_day(), tz=UTC, now=START)
    assert forecast.low == 18.0
    assert forecast.high == 25.0
    assert forecast.rain_total == 1.4


def test_rain_soon_only_covers_six_hours_and_uses_the_given_zone():
    forecast = summarize_forecast(_day(), tz=BRT, now=START)
    assert [(m.hour, amount) for m, amount in forecast.rain_soon] == [(10, 0.4)]


def test_stale_slots_are_dropped():
    later = START + timedelta(hours=1, minutes=10)
    forecast = summarize_forecast(_day(), tz=UTC, now=later)
    assert forecast.temperature == 22.0
    assert forecast.hours[0].time == START + timedelta(hours=1)


def test_strip_length_is_limited():
    forecast = summarize_forecast(_day(), tz=UTC, hours=2, now=START)
    assert len(forecast.hours) == 2
    assert forecast.hours[1].precipitation == 0.4


def test_unusable_responses_give_none():
    assert summarize_forecast(None) is None
    assert summarize_forecast({}) is None
    assert summarize_forecast(_response([])) is None
    assert summarize_forecast(_response([{"time": "bad"}])) is None


def test_missing_fields_do_not_raise():
    forecast = summarize_forecast(_response([_entry(0, 20.0)]), tz=UTC, now=START)
    assert forecast.wind_speed is None
    assert forecast.rain_total == 0.0
    assert forecast.symbol == ""


def test_rain_description():
    assert describe_rain_soon([], "mm") == "No rain expected in the next 6 hours"
    soon = [(START + timedelta(hours=h), 0.5) for h in range(1, 7)]
    text = describe_rain_soon(soon, "mm")
    assert text.startswith("Rain expected: 13:00 0.5 mm")
    assert text.endswith("+2 more")
