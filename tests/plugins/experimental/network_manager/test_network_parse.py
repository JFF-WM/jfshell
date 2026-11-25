from src.plugins.experimental.network_manager._network_parse import (
    ActiveConnection,
    SavedConnection,
    WifiNetwork,
    band_label,
    details_text,
    group_entries,
    parse_saved_wifi,
    parse_wifi_list,
    parse_wifi_radio,
    pick_active_device,
    property_value,
    signal_icon,
    split_terse,
    with_ssid,
)

WIFI_LIST = "\n".join(
    [
        r"*:Matrix:100:WPA2:5180 MHz:AA\:BB\:CC\:DD\:EE\:01",
        r" :Matrix:70:WPA2:2437 MHz:AA\:BB\:CC\:DD\:EE\:02",
        r" :NEO 5G:54:WPA2:5200 MHz:AA\:BB\:CC\:DD\:EE\:03",
        r" :NEO 2.4G:62:WPA2:2412 MHz:AA\:BB\:CC\:DD\:EE\:04",
        r" :Cafe\: Free:39:--:2462 MHz:AA\:BB\:CC\:DD\:EE\:05",
        r" ::50:WPA2:2412 MHz:AA\:BB\:CC\:DD\:EE\:06",
        "garbage line",
    ]
)


def test_split_terse_handles_escaped_colons_and_backslashes():
    assert split_terse(r"a\:b:c") == ["a:b", "c"]
    assert split_terse(r"a\\b:c") == ["a\\b", "c"]
    assert split_terse("") == [""]
    assert split_terse("x:") == ["x", ""]


def test_band_label():
    assert band_label(2437) == "2.4 GHz"
    assert band_label(5180) == "5 GHz"
    assert band_label(5955) == "6 GHz"
    assert band_label(0) == ""


def test_signal_icon_thresholds():
    assert signal_icon(100).endswith("excellent-symbolic")
    assert signal_icon(70).endswith("good-symbolic")
    assert signal_icon(50).endswith("ok-symbolic")
    assert signal_icon(10).endswith("weak-symbolic")


def test_parse_wifi_radio():
    assert parse_wifi_radio("enabled\n") is True
    assert parse_wifi_radio("disabled") is False
    assert parse_wifi_radio("") is False


def test_parse_wifi_list_merges_hides_and_sorts():
    networks = parse_wifi_list(WIFI_LIST)
    assert [n.ssid for n in networks] == [
        "Matrix",
        "NEO 2.4G",
        "NEO 5G",
        "Cafe: Free",
    ]
    matrix = networks[0]
    assert matrix.in_use and matrix.signal == 100 and matrix.band == "5 GHz"
    assert matrix.bssid == "AA:BB:CC:DD:EE:01"
    assert matrix.secured


def test_parse_wifi_list_open_network_is_not_secured():
    cafe = [n for n in parse_wifi_list(WIFI_LIST) if n.ssid == "Cafe: Free"][0]
    assert not cafe.secured


def test_parse_wifi_list_in_use_beats_stronger_duplicate():
    raw = "\n".join(
        [
            r"*:Home:40:WPA2:2412 MHz:AA\:00",
            r" :Home:90:WPA2:5180 MHz:AA\:01",
        ]
    )
    (home,) = parse_wifi_list(raw)
    assert home.in_use and home.signal == 40


def test_parse_saved_wifi_keeps_only_wifi_profiles():
    raw = "\n".join(
        [
            "Matrix:uuid-1:802-11-wireless",
            "NEO 5G:uuid-2:802-11-wireless",
            "Wired connection 1:uuid-3:802-3-ethernet",
            "lo:uuid-4:loopback",
        ]
    )
    saved = parse_saved_wifi(raw)
    assert [(s.name, s.uuid, s.ssid) for s in saved] == [
        ("Matrix", "uuid-1", "Matrix"),
        ("NEO 5G", "uuid-2", "NEO 5G"),
    ]


def test_with_ssid_ignores_empty():
    profile = SavedConnection("Home profile", "u", "Home profile")
    assert with_ssid(profile, "Home").ssid == "Home"
    assert with_ssid(profile, "").ssid == "Home profile"


def test_group_entries_splits_active_saved_available():
    networks = parse_wifi_list(WIFI_LIST)
    saved = [
        SavedConnection("Matrix", "uuid-1", "Matrix"),
        SavedConnection("NEO 5G", "uuid-2", "NEO 5G"),
        SavedConnection("Old Place", "uuid-9", "Old Place"),
    ]
    active, saved_rows, available = group_entries(networks, saved)
    assert active is not None and active.ssid == "Matrix"
    assert active.saved_uuid == "uuid-1"
    assert [e.ssid for e in saved_rows] == ["NEO 5G", "Old Place"]
    assert saved_rows[0].in_range and not saved_rows[1].in_range
    assert [e.ssid for e in available] == ["NEO 2.4G", "Cafe: Free"]
    assert all(e.saved_uuid is None for e in available)


def test_group_entries_without_active_network():
    networks = [WifiNetwork("A", 80, "WPA2", 2412, "b", False)]
    active, saved_rows, available = group_entries(networks, [])
    assert active is None and saved_rows == []
    assert [e.ssid for e in available] == ["A"]


STATUS = "\n".join(
    [
        "wlan0:wifi:connected:Matrix",
        "enp3s0:ethernet:connected:Wired connection 1",
        "p2p-dev-wlan0:wifi-p2p:disconnected:",
        "lo:loopback:unmanaged:",
    ]
)


def test_pick_active_device_prefers_default_route():
    assert pick_active_device(STATUS, "wlan0") == ("wlan0", "wifi", "Matrix")


def test_pick_active_device_prefers_wired_without_hint():
    assert pick_active_device(STATUS) == (
        "enp3s0",
        "ethernet",
        "Wired connection 1",
    )


def test_pick_active_device_none_when_nothing_connected():
    assert pick_active_device("wlan0:wifi:disconnected:\n") is None
    assert pick_active_device("") is None


def test_property_value_strips_index_and_mask():
    props = {"IP4.ADDRESS[1]": "192.168.50.150/24", "IP4.GATEWAY": "192.168.50.1"}
    assert property_value(props, "IP4.ADDRESS") == "192.168.50.150"
    assert property_value(props, "IP4.GATEWAY") == "192.168.50.1"
    assert property_value(props, "IP4.DNS") == ""


def test_details_text_skips_empty_values():
    active = ActiveConnection(
        kind="wifi", device="wlan0", name="Matrix", gateway="192.168.50.1"
    )
    assert details_text(active) == "Device: wlan0\nGateway: 192.168.50.1"
