from src.plugins.experimental.now_playing._player_format import (
    estimate_position,
    format_time,
    marquee_offset,
    player_name,
    progress_fraction,
)


def test_format_time_minutes_and_hours():
    assert format_time(0) == "0:00"
    assert format_time(65_000_000) == "1:05"
    assert format_time(3_725_000_000) == "1:02:05"


def test_format_time_handles_missing_and_negative():
    assert format_time(None) == "0:00"
    assert format_time(-5) == "0:00"


def test_progress_fraction_clamps():
    assert progress_fraction(50, 100) == 0.5
    assert progress_fraction(150, 100) == 1.0
    assert progress_fraction(-1, 100) == 0.0


def test_progress_fraction_unknown_length():
    assert progress_fraction(50, 0) == 0.0
    assert progress_fraction(50, None) == 0.0


def test_estimate_position_advances_only_while_playing():
    assert estimate_position(10_000_000, 2.0, True, 60_000_000) == 12_000_000
    assert estimate_position(10_000_000, 2.0, False, 60_000_000) == 10_000_000


def test_estimate_position_caps_at_length():
    assert estimate_position(59_000_000, 5.0, True, 60_000_000) == 60_000_000
    assert estimate_position(59_000_000, 5.0, True, None) == 64_000_000


def test_marquee_offset_rests_scrolls_and_returns():
    span, speed, pause = 100.0, 50.0, 1_000_000
    assert marquee_offset(0, span, speed, pause) == 0.0
    assert marquee_offset(2_000_000, span, speed, pause) == 50.0
    assert marquee_offset(3_000_000, span, speed, pause) == 100.0
    assert marquee_offset(5_000_000, span, speed, pause) == 50.0
    assert marquee_offset(6_000_000, span, speed, pause) == 0.0


def test_marquee_offset_without_overflow_stays_put():
    assert marquee_offset(123, 0.0, 28.0, 1_000_000) == 0.0
    assert marquee_offset(123, -4.0, 28.0, 1_000_000) == 0.0


def test_marquee_offset_stays_within_span():
    for t in range(0, 20_000_000, 137_000):
        assert 0.0 <= marquee_offset(t, 80.0, 28.0, 1_500_000) <= 80.0


def test_player_name_from_bus_names():
    assert player_name("org.mpris.MediaPlayer2.firefox.instance_1_92") == "Firefox"
    assert player_name("org.mpris.MediaPlayer2.spotify") == "Spotify"
    assert player_name("org.mpris.MediaPlayer2.io.bassi.Amberol") == "Amberol"
    assert player_name("org.mpris.MediaPlayer2.my-player") == "My player"
    assert player_name("org.mpris.MediaPlayer2.instance3") == ""
