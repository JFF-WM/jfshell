"""Pure helpers for the Bluetooth plugin.

Turns the plain-dict form of BlueZ's ``GetManagedObjects`` reply into small
immutable records and groups them for display. No I/O and no GTK, so it can be
tested on its own.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
BATTERY_IFACE = "org.bluez.Battery1"

AUDIO_ICON_HINTS = ("audio", "headset", "headphones", "speaker", "card")

# {object path: {interface: {property: plain value}}}
ManagedObjects = Mapping[str, Mapping[str, Mapping[str, Any]]]


@dataclass(frozen=True)
class BtAdapter:
    """The Bluetooth controller."""

    path: str
    name: str
    powered: bool
    discovering: bool


@dataclass(frozen=True)
class BtDevice:
    """One remote device known to BlueZ."""

    path: str
    mac: str
    name: str
    icon: str
    paired: bool
    connected: bool
    trusted: bool
    has_name: bool
    battery: Optional[int] = None
    rssi: Optional[int] = None

    @property
    def is_audio(self) -> bool:
        """True for headsets, speakers and other audio sinks."""
        return is_audio_icon(self.icon)


def is_audio_icon(icon: str) -> bool:
    """True when a BlueZ icon name describes an audio device."""
    lowered = (icon or "").lower()
    return any(hint in lowered for hint in AUDIO_ICON_HINTS)


def battery_icon(percent: int) -> str:
    """Symbolic battery icon, rounded to the nearest 10 percent."""
    level = max(0, min(100, int(round(percent / 10.0)) * 10))
    return f"battery-level-{level}-symbolic"


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick_adapter(
    objects: ManagedObjects,
) -> Optional[Tuple[str, Mapping[str, Any]]]:
    """Prefer a powered adapter, then the first one by path."""
    found = sorted(
        (path, ifaces[ADAPTER_IFACE])
        for path, ifaces in objects.items()
        if ADAPTER_IFACE in ifaces
    )
    if not found:
        return None
    for path, props in found:
        if props.get("Powered"):
            return path, props
    return found[0]


def parse_objects(
    objects: ManagedObjects,
) -> Tuple[Optional[BtAdapter], List[BtDevice]]:
    """Build the adapter and its devices from ``GetManagedObjects`` data.

    Devices that belong to another adapter are left out. A device is
    considered unnamed when BlueZ only knows its address.
    """
    picked = _pick_adapter(objects)
    if picked is None:
        return None, []
    adapter_path, adapter_props = picked
    adapter = BtAdapter(
        path=adapter_path,
        name=str(adapter_props.get("Alias") or adapter_props.get("Name") or ""),
        powered=bool(adapter_props.get("Powered", False)),
        discovering=bool(adapter_props.get("Discovering", False)),
    )
    devices: List[BtDevice] = []
    for path, ifaces in objects.items():
        props = ifaces.get(DEVICE_IFACE)
        if props is None or props.get("Adapter", adapter_path) != adapter_path:
            continue
        mac = str(props.get("Address", ""))
        name = str(props.get("Alias") or props.get("Name") or mac)
        dashed = mac.replace(":", "-")
        battery = _as_int(ifaces.get(BATTERY_IFACE, {}).get("Percentage"))
        devices.append(
            BtDevice(
                path=path,
                mac=mac,
                name=name,
                icon=str(props.get("Icon", "")),
                paired=bool(props.get("Paired", False)),
                connected=bool(props.get("Connected", False)),
                trusted=bool(props.get("Trusted", False)),
                has_name=bool(props.get("Name")) or (name not in ("", mac, dashed)),
                battery=battery,
                rssi=_as_int(props.get("RSSI")),
            )
        )
    return adapter, devices


def update_seen(
    seen: Set[str], devices: Iterable[BtDevice], discovering: bool
) -> Set[str]:
    """Remember unpaired devices found during this visit to the page.

    Without this, a device would vanish from "Available" the moment scanning
    stops. Entries whose device no longer exists are dropped.
    """
    present = {d.path for d in devices}
    kept = {path for path in seen if path in present}
    if discovering:
        kept |= {d.path for d in devices if not d.paired and d.has_name}
    return kept


def group_devices(
    devices: Iterable[BtDevice], seen: Set[str]
) -> Tuple[List[BtDevice], List[BtDevice], List[BtDevice]]:
    """Split devices into ``(connected, paired, available)``.

    Available devices are unpaired, named ones seen this visit, strongest
    signal first.
    """
    connected: List[BtDevice] = []
    paired: List[BtDevice] = []
    available: List[BtDevice] = []
    for device in devices:
        if device.connected:
            connected.append(device)
        elif device.paired:
            paired.append(device)
        elif device.has_name and device.path in seen:
            available.append(device)
    connected.sort(key=lambda d: d.name.lower())
    paired.sort(key=lambda d: d.name.lower())
    available.sort(
        key=lambda d: (d.rssi is None, -(d.rssi if d.rssi is not None else 0), d.name.lower())
    )
    return connected, paired, available


_ERROR_MESSAGES = {
    "org.bluez.Error.AuthenticationFailed": "Pairing failed. The device may need a PIN",
    "org.bluez.Error.AuthenticationRejected": "Pairing was rejected",
    "org.bluez.Error.AuthenticationCanceled": "Pairing was cancelled",
    "org.bluez.Error.AuthenticationTimeout": "Pairing timed out",
    "org.bluez.Error.ConnectionAttemptFailed": "Could not reach the device",
    "org.bluez.Error.AlreadyConnected": "Already connected",
    "org.bluez.Error.InProgress": "Another operation is in progress",
    "org.bluez.Error.NotReady": "Bluetooth is not ready",
    "org.bluez.Error.Failed": "Operation failed",
}


def friendly_error(error_type: str, text: str = "") -> str:
    """Short, readable message for a BlueZ D-Bus error."""
    if "rfkill" in text.lower():
        return "Bluetooth is blocked (rfkill)"
    if error_type in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[error_type]
    cleaned = (text or error_type or "Operation failed").strip().splitlines()[0]
    return cleaned[:100]
