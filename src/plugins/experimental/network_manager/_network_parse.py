"""Pure helpers for the network manager plugin.

Everything here turns nmcli text into small immutable records and groups them
for display. There is no I/O and no GTK, so it can be tested on its own.
"""

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

WIFI_PROFILE_TYPE = "802-11-wireless"
_DIGITS = re.compile(r"\d+")


@dataclass(frozen=True)
class WifiNetwork:
    """One access point as reported by ``nmcli device wifi list``."""

    ssid: str
    signal: int
    security: str
    freq_mhz: int
    bssid: str
    in_use: bool

    @property
    def secured(self) -> bool:
        """True when the network needs a password."""
        return self.security.strip() not in ("", "--")

    @property
    def band(self) -> str:
        """Human readable band, e.g. ``5 GHz``; empty when unknown."""
        return band_label(self.freq_mhz)


@dataclass(frozen=True)
class SavedConnection:
    """A stored Wi-Fi profile."""

    name: str
    uuid: str
    ssid: str


@dataclass(frozen=True)
class NetworkEntry:
    """One row of the Wi-Fi list: a network, its profile and its range state."""

    ssid: str
    signal: int
    secured: bool
    band: str
    in_use: bool
    saved_uuid: Optional[str]
    in_range: bool


@dataclass(frozen=True)
class ActiveConnection:
    """What the "Current connection" card shows."""

    kind: str  # "wifi" or "ethernet"
    device: str
    name: str
    ip: str = ""
    gateway: str = ""
    dns: str = ""
    mac: str = ""
    signal: Optional[int] = None
    band: str = ""
    security: str = ""


def split_terse(line: str) -> List[str]:
    """Split one ``nmcli -t`` line on unescaped colons.

    In terse mode nmcli escapes ``:`` and ``\\`` inside values with a
    backslash, so BSSIDs and SSIDs containing colons survive the split.
    """
    fields: List[str] = []
    current: List[str] = []
    i = 0
    while i < len(line):
        char = line[i]
        if char == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    fields.append("".join(current))
    return fields


def _to_int(value: str) -> int:
    match = _DIGITS.search(value or "")
    return int(match.group()) if match else 0


def band_label(freq_mhz: int) -> str:
    """Map a frequency in MHz to ``2.4 GHz``, ``5 GHz`` or ``6 GHz``."""
    if freq_mhz <= 0:
        return ""
    if freq_mhz < 3000:
        return "2.4 GHz"
    if freq_mhz < 5925:
        return "5 GHz"
    return "6 GHz"


def signal_icon(signal: int) -> str:
    """Symbolic icon name for a signal strength in percent."""
    if signal > 80:
        return "network-wireless-signal-excellent-symbolic"
    if signal > 60:
        return "network-wireless-signal-good-symbolic"
    if signal > 40:
        return "network-wireless-signal-ok-symbolic"
    return "network-wireless-signal-weak-symbolic"


def parse_wifi_radio(raw: str) -> bool:
    """Parse the output of ``nmcli radio wifi``."""
    return raw.strip().lower() == "enabled"


def parse_wifi_list(raw: str) -> List[WifiNetwork]:
    """Parse ``nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY,FREQ,BSSID device wifi list``.

    Hidden networks (empty SSID) are dropped. Access points that share an SSID
    are merged into one: the one in use wins, otherwise the strongest.
    Result is sorted in-use first, then by signal, then by name.
    """
    best: Dict[str, WifiNetwork] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = split_terse(line)
        if len(fields) < 6:
            continue
        in_use, ssid, signal, security, freq, bssid = fields[:6]
        if not ssid:
            continue
        network = WifiNetwork(
            ssid=ssid,
            signal=max(0, min(100, _to_int(signal))),
            security=security.strip(),
            freq_mhz=_to_int(freq),
            bssid=bssid,
            in_use=in_use.strip() == "*",
        )
        current = best.get(ssid)
        if current is None or (network.in_use, network.signal) > (
            current.in_use,
            current.signal,
        ):
            best[ssid] = network
    return sorted(
        best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.lower())
    )


def parse_saved_wifi(raw: str) -> List[SavedConnection]:
    """Parse ``nmcli -t -f NAME,UUID,TYPE connection show`` into Wi-Fi profiles.

    The SSID defaults to the profile name; the backend refines it afterwards
    because a profile may be named differently from its network.
    """
    saved: List[SavedConnection] = []
    for line in raw.splitlines():
        fields = split_terse(line)
        if len(fields) < 3 or fields[2].strip() != WIFI_PROFILE_TYPE:
            continue
        name, uuid = fields[0].strip(), fields[1].strip()
        if name and uuid:
            saved.append(SavedConnection(name=name, uuid=uuid, ssid=name))
    return saved


def with_ssid(connection: SavedConnection, ssid: str) -> SavedConnection:
    """Return ``connection`` with its SSID replaced; empty values are ignored."""
    return replace(connection, ssid=ssid) if ssid else connection


def group_entries(
    networks: Sequence[WifiNetwork], saved: Sequence[SavedConnection]
) -> Tuple[Optional[NetworkEntry], List[NetworkEntry], List[NetworkEntry]]:
    """Split the Wi-Fi list into ``(active, saved, available)``.

    Saved profiles that are out of range still appear in the saved group so
    they can be forgotten; they sort after the ones in range.
    """
    saved_by_ssid: Dict[str, SavedConnection] = {}
    for connection in saved:
        saved_by_ssid.setdefault(connection.ssid, connection)

    active: Optional[NetworkEntry] = None
    saved_in_range: List[NetworkEntry] = []
    available: List[NetworkEntry] = []
    seen = set()

    for network in networks:
        seen.add(network.ssid)
        profile = saved_by_ssid.get(network.ssid)
        entry = NetworkEntry(
            ssid=network.ssid,
            signal=network.signal,
            secured=network.secured,
            band=network.band,
            in_use=network.in_use,
            saved_uuid=profile.uuid if profile else None,
            in_range=True,
        )
        if network.in_use and active is None:
            active = entry
        elif profile:
            saved_in_range.append(entry)
        else:
            available.append(entry)

    saved_in_range.sort(key=lambda e: (-e.signal, e.ssid.lower()))
    available.sort(key=lambda e: (-e.signal, e.ssid.lower()))

    out_of_range = [
        NetworkEntry(
            ssid=connection.ssid,
            signal=0,
            secured=True,
            band="",
            in_use=False,
            saved_uuid=connection.uuid,
            in_range=False,
        )
        for ssid, connection in saved_by_ssid.items()
        if ssid not in seen
    ]
    out_of_range.sort(key=lambda e: e.ssid.lower())
    return active, saved_in_range + out_of_range, available


def pick_active_device(
    raw_status: str, preferred: Optional[str] = None
) -> Optional[Tuple[str, str, str]]:
    """Choose the device the "Current connection" card describes.

    ``raw_status`` is ``nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status``.
    Returns ``(device, kind, connection_name)`` or None. The device that owns
    the default route wins; otherwise wired beats wireless.
    """
    candidates: List[Tuple[str, str, str]] = []
    for line in raw_status.splitlines():
        fields = split_terse(line)
        if len(fields) < 4:
            continue
        device, type_, state, connection = (f.strip() for f in fields[:4])
        if not state.lower().startswith("connected"):
            continue
        if type_ == "wifi":
            candidates.append((device, "wifi", connection))
        elif type_ == "ethernet":
            candidates.append((device, "ethernet", connection))
    if not candidates:
        return None
    if preferred:
        for candidate in candidates:
            if candidate[0] == preferred:
                return candidate
    for candidate in candidates:
        if candidate[1] == "ethernet":
            return candidate
    return candidates[0]


def property_value(props: Dict[str, str], prefix: str) -> str:
    """First value whose key is ``prefix`` or ``prefix[N]``, address mask removed."""
    for key in sorted(props):
        if key == prefix or key.startswith(prefix + "["):
            return props[key].split("/")[0].strip()
    return ""


def details_text(active: ActiveConnection) -> str:
    """Multi-line tooltip text with the details that do not fit on the card."""
    rows = [
        ("Device", active.device),
        ("Gateway", active.gateway),
        ("DNS", active.dns),
        ("MAC", active.mac),
        ("Security", active.security),
    ]
    return "\n".join(f"{label}: {value}" for label, value in rows if value)
