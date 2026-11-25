from src.plugins.experimental.bluetooth._bluetooth_model import (
    BtDevice,
    battery_icon,
    friendly_error,
    group_devices,
    is_audio_icon,
    parse_objects,
    update_seen,
)

ADAPTER = "/org/bluez/hci0"


def _device(mac, **props):
    base = {"Address": mac, "Adapter": ADAPTER, "Paired": False, "Connected": False}
    base.update(props)
    return {"org.bluez.Device1": base}


def _objects():
    return {
        ADAPTER: {
            "org.bluez.Adapter1": {"Alias": "laptop", "Powered": True, "Discovering": False}
        },
        ADAPTER + "/dev_AA_00": {
            **_device(
                "AA:00",
                Name="Buds",
                Alias="Buds",
                Icon="audio-headset",
                Paired=True,
                Connected=True,
                Trusted=True,
            ),
            "org.bluez.Battery1": {"Percentage": 82},
        },
        ADAPTER + "/dev_BB_00": _device("BB:00", Name="Speaker", Icon="audio-card", Paired=True),
        ADAPTER + "/dev_CC_00": _device("CC:00", Name="Phone", Icon="phone", RSSI=-60),
        ADAPTER + "/dev_DD_00": _device("DD:00", Alias="DD-00", RSSI=-40),
        ADAPTER + "/dev_EE_00": _device("EE:00", Name="Mouse", RSSI=-80),
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Powered": False}},
        "/org/bluez/hci1/dev_FF_00": _device("FF:00", Name="Other", Adapter="/org/bluez/hci1"),
    }


def test_parse_objects_picks_powered_adapter_and_its_devices():
    adapter, devices = parse_objects(_objects())
    assert adapter.path == ADAPTER and adapter.powered and not adapter.discovering
    assert adapter.name == "laptop"
    assert sorted(d.mac for d in devices) == ["AA:00", "BB:00", "CC:00", "DD:00", "EE:00"]


def test_parse_objects_reads_battery_and_flags():
    _, devices = parse_objects(_objects())
    buds = {d.mac: d for d in devices}["AA:00"]
    assert buds.battery == 82 and buds.connected and buds.paired and buds.trusted
    assert buds.is_audio


def test_parse_objects_marks_unnamed_devices():
    _, devices = parse_objects(_objects())
    by_mac = {d.mac: d for d in devices}
    assert not by_mac["DD:00"].has_name
    assert by_mac["CC:00"].has_name


def test_parse_objects_without_adapter():
    assert parse_objects({}) == (None, [])
    assert parse_objects({"/x": {"org.bluez.Device1": {}}}) == (None, [])


def test_parse_objects_falls_back_to_first_adapter_when_none_powered():
    objects = {
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Powered": False}},
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": False}},
    }
    adapter, _ = parse_objects(objects)
    assert adapter.path == "/org/bluez/hci0" and not adapter.powered


def test_group_devices_hides_unseen_and_unnamed():
    _, devices = parse_objects(_objects())
    connected, paired, available = group_devices(devices, set())
    assert [d.name for d in connected] == ["Buds"]
    assert [d.name for d in paired] == ["Speaker"]
    assert available == []


def test_group_devices_available_sorted_by_signal():
    _, devices = parse_objects(_objects())
    seen = {ADAPTER + "/dev_CC_00", ADAPTER + "/dev_EE_00", ADAPTER + "/dev_DD_00"}
    _, _, available = group_devices(devices, seen)
    assert [d.name for d in available] == ["Phone", "Mouse"]


def test_update_seen_tracks_named_unpaired_while_discovering():
    _, devices = parse_objects(_objects())
    seen = update_seen(set(), devices, True)
    assert seen == {ADAPTER + "/dev_CC_00", ADAPTER + "/dev_EE_00"}


def test_update_seen_keeps_when_scan_stops_and_drops_gone_devices():
    _, devices = parse_objects(_objects())
    seen = {ADAPTER + "/dev_CC_00", "/gone"}
    assert update_seen(seen, devices, False) == {ADAPTER + "/dev_CC_00"}


def test_is_audio_icon():
    assert is_audio_icon("audio-headset")
    assert is_audio_icon("Audio-Card")
    assert not is_audio_icon("input-keyboard")
    assert not is_audio_icon("")


def test_battery_icon_rounds_and_clamps():
    assert battery_icon(82) == "battery-level-80-symbolic"
    assert battery_icon(100) == "battery-level-100-symbolic"
    assert battery_icon(3) == "battery-level-0-symbolic"
    assert battery_icon(250) == "battery-level-100-symbolic"


def test_friendly_error_known_unknown_and_rfkill():
    assert friendly_error("org.bluez.Error.AuthenticationFailed").startswith("Pairing failed")
    assert friendly_error("x", "Blocked through rfkill") == "Bluetooth is blocked (rfkill)"
    assert friendly_error("x", "Something odd\nsecond line") == "Something odd"
    assert friendly_error("", "") == "Operation failed"


def test_btdevice_is_hashable_for_display_keys():
    device = BtDevice("/p", "AA", "n", "", False, False, False, True)
    assert hash(device) == hash(BtDevice("/p", "AA", "n", "", False, False, False, True))
