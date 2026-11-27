"""Tests for the pure helpers of the home plugin."""

import importlib.util
import sys
from pathlib import Path

_INFO = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "plugins"
    / "experimental"
    / "home"
    / "_home_info.py"
)
_spec = importlib.util.spec_from_file_location("home_info", _INFO)
info = importlib.util.module_from_spec(_spec)
sys.modules["home_info"] = info
_spec.loader.exec_module(info)


def test_parse_uptime():
    assert info.parse_uptime("35942.51 140233.12\n") == 35942.51
    assert info.parse_uptime("") is None
    assert info.parse_uptime("garbage") is None


def test_format_uptime_units():
    assert info.format_uptime(9 * 3600 + 59 * 60 + 30) == "9 hours 59 minutes"
    assert info.format_uptime(3600 + 60) == "1 hour 1 minute"
    assert info.format_uptime(5 * 60) == "5 minutes"
    assert info.format_uptime(0) == "0 minutes"
    assert info.format_uptime(-10) == "0 minutes"


def test_format_uptime_days_keeps_two_units():
    assert info.format_uptime(2 * 86400 + 3 * 3600 + 5 * 60) == "2 days 3 hours"
    assert info.format_uptime(86400 + 5 * 60) == "1 day 5 minutes"


def test_uptime_line():
    assert info.uptime_line(3600) == "Uptime – 1 hour"
    assert info.uptime_line(None) == "Uptime unknown"


def test_parse_radio_enabled():
    assert info.parse_radio_enabled("enabled\n") is True
    assert info.parse_radio_enabled("disabled\n") is False
    assert info.parse_radio_enabled("") is False


def test_parse_active_ssid():
    text = "no:Neighbour\nyes:Matrix\nno:Other\n"
    assert info.parse_active_ssid(text) == "Matrix"


def test_parse_active_ssid_unescapes_colons_and_backslashes():
    assert info.parse_active_ssid("yes:Cafe\\: 5G\n") == "Cafe: 5G"
    assert info.parse_active_ssid("yes:A\\\\B\n") == "A\\B"


def test_parse_active_ssid_without_connection():
    assert info.parse_active_ssid("no:Neighbour\n") == ""
    assert info.parse_active_ssid("") == ""


def test_parse_bluetooth_powered():
    on = "Controller AA:BB:CC:DD:EE:FF (public)\n\tName: matrix\n\tPowered: yes\n"
    off = "Controller AA:BB:CC:DD:EE:FF (public)\n\tPowered: no\n"
    assert info.parse_bluetooth_powered(on) is True
    assert info.parse_bluetooth_powered(off) is False
    assert info.parse_bluetooth_powered("No default controller available\n") is False


PROFILES_OUTPUT = """  performance:
    CpuDriver:\tamd_pstate
    PlatformDriver:\tplatform_profile

* balanced:
    CpuDriver:\tamd_pstate

  power-saver:
    CpuDriver:\tamd_pstate
"""


def test_parse_profile_list_uses_cycle_order():
    assert info.parse_profile_list(PROFILES_OUTPUT) == [
        "power-saver",
        "balanced",
        "performance",
    ]


def test_parse_profile_list_partial_and_empty():
    assert info.parse_profile_list("* balanced:\n  power-saver:\n") == [
        "power-saver",
        "balanced",
    ]
    assert info.parse_profile_list("") == []


def test_next_profile_wraps_around():
    available = ["power-saver", "balanced", "performance"]
    assert info.next_profile("power-saver", available) == "balanced"
    assert info.next_profile("performance", available) == "power-saver"
    assert info.next_profile("unknown", available) == "power-saver"
    assert info.next_profile("balanced", []) is None


def test_profile_label():
    assert info.profile_label("balanced") == "Balanced"
    assert info.profile_label("power-saver") == "Power Saver"
    assert info.profile_label("custom-mode") == "Custom Mode"
    assert info.profile_label("") == "Power Profile"


def _player(title, status, artist="Artist"):
    return {"title": title, "status": status, "artist": artist}


def test_pick_player_prefers_playing_over_paused():
    paused = _player("Old", "Paused")
    playing = _player("Now", "Playing")
    assert info.pick_player([paused, playing]) is playing


def test_pick_player_falls_back_to_first_paused():
    first = _player("A", "Paused")
    second = _player("B", "Paused")
    assert info.pick_player([first, second]) is first


def test_pick_player_skips_stopped_and_untitled():
    players = [
        _player("A", "Stopped"),
        _player("", "Playing"),
        _player("Unknown", "Playing"),
    ]
    assert info.pick_player(players) is None
    assert info.pick_player([]) is None


def test_media_texts():
    assert info.media_texts(None) == ("Nothing playing", "Idle")
    assert info.media_texts(_player("Song", "Playing")) == ("Song", "Artist")
    assert info.media_texts(_player("Song", "Playing", "Unknown")) == (
        "Song",
        "Playing",
    )
    assert info.media_texts(_player("Song", "Paused")) == ("Song", "Paused · Artist")
    assert info.media_texts(_player("Song", "Paused", "")) == ("Song", "Paused")


def test_location_text():
    assert info.location_text("São Paulo") == "São Paulo"
    assert info.location_text("  ") == "Location not set"
    assert info.location_text("") == "Location not set"
