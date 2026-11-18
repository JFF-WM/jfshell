"""HDR requests to the compositor: command building and reply parsing, no GTK.

``HdrClient`` gets the function that talks to the compositor socket, so the
parsing stays free of I/O. Every call blocks, run them off the GTK thread.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_TONE_MAP = "knee"
DEFAULT_GAMUT_MAP = "compress"
DEFAULT_CONTRAST = 1.0
DEFAULT_BLACK_LEVEL = 0.0
DEFAULT_SATURATION = 1.0
DEFAULT_WHITE_BALANCE: Tuple[float, float, float] = (1.0, 1.0, 1.0)


class HdrError(RuntimeError):
    """A request the compositor refused, or a reply that could not be read."""


@dataclass
class HdrState:
    """HDR of one monitor: what is asked for, what it really does, the settings."""

    name: str
    requested: bool = False
    active: bool = False
    reason: Optional[str] = None
    eotf: Optional[str] = None
    peak: Optional[int] = None
    sdr_white: int = 203
    bits: Optional[int] = None
    scanout_active: bool = False
    scanout_reason: Optional[str] = None
    max_cll: Optional[int] = None
    max_fall: Optional[int] = None
    tone_map: str = DEFAULT_TONE_MAP
    gamut_map: str = DEFAULT_GAMUT_MAP
    contrast: float = DEFAULT_CONTRAST
    black_level: float = DEFAULT_BLACK_LEVEL
    saturation: float = DEFAULT_SATURATION
    white_balance: Tuple[float, float, float] = DEFAULT_WHITE_BALANCE
    changed: List[str] = field(default_factory=list)
    kdl: Optional[str] = None


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _balance(value: Any) -> Tuple[float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return (
            _number(value[0], 1.0),
            _number(value[1], 1.0),
            _number(value[2], 1.0),
        )
    return DEFAULT_WHITE_BALANCE


def parse_state(output: Dict[str, Any], settings_reply: Dict[str, Any]) -> HdrState:
    """Build an HdrState from an ``outputs`` entry and an ``hdr-get`` reply.

    ``null`` in the settings is the default, and for ``peak`` it is the EDID's,
    so the peak the output reports is used then.
    """
    hdr = output.get("hdr") or {}
    settings = settings_reply.get("settings") or {}
    scanout = hdr.get("scanout") or {}
    light = hdr.get("content_light") or {}
    peak = settings.get("peak")
    if peak is None:
        peak = hdr.get("peak")
    changed = settings_reply.get("changed")
    return HdrState(
        name=str(output.get("name", "")),
        requested=bool(hdr.get("requested", False)),
        active=bool(hdr.get("active", False)),
        reason=hdr.get("reason"),
        eotf=hdr.get("eotf"),
        peak=int(peak) if isinstance(peak, (int, float)) else None,
        sdr_white=int(_number(hdr.get("sdr_white"), 203)),
        bits=hdr.get("bits"),
        scanout_active=bool(scanout.get("active", False)),
        scanout_reason=scanout.get("reason"),
        max_cll=light.get("max_cll"),
        max_fall=light.get("max_fall"),
        tone_map=settings.get("tone_map") or DEFAULT_TONE_MAP,
        gamut_map=settings.get("gamut_map") or DEFAULT_GAMUT_MAP,
        contrast=_number(settings.get("contrast"), DEFAULT_CONTRAST),
        black_level=_number(settings.get("black_level"), DEFAULT_BLACK_LEVEL),
        saturation=_number(settings.get("saturation"), DEFAULT_SATURATION),
        white_balance=_balance(settings.get("white_balance")),
        changed=list(changed) if isinstance(changed, list) else [],
        kdl=settings_reply.get("kdl"),
    )


def describe(state: HdrState) -> str:
    """One line for the status row: what the monitor does, or why it does not."""
    if state.active:
        parts = ["Active"]
        if state.eotf:
            parts.append(state.eotf)
        if state.peak:
            parts.append(f"peak {state.peak} nits")
        if state.bits:
            parts.append(f"{state.bits} bit")
        return " · ".join(parts)
    if state.requested:
        return state.reason or "Requested, not active"
    return "Off"


class HdrClient:
    """Reads and changes the HDR of the monitors jff drives."""

    def __init__(self, send: Callable[[str], Dict[str, Any]]) -> None:
        self._send = send

    def _request(self, line: str) -> Dict[str, Any]:
        reply = self._send(line)
        if not isinstance(reply, dict):
            raise HdrError("unreadable reply from the compositor")
        if not reply.get("ok"):
            raise HdrError(str(reply.get("error") or "the compositor refused it"))
        return reply

    @staticmethod
    def _monitor(name: str) -> str:
        if not name or any(ch.isspace() for ch in name):
            raise HdrError(f"{name!r} is not a monitor name")
        return name

    def monitors(self) -> List[Dict[str, Any]]:
        """Every monitor, as the ``outputs`` request describes them."""
        outputs = self._request("outputs").get("outputs")
        return [o for o in outputs if isinstance(o, dict)] if outputs else []

    def state(self, name: str, output: Optional[Dict[str, Any]] = None) -> HdrState:
        """The HDR state of a monitor, from ``output`` when the caller has it."""
        name = self._monitor(name)
        if output is None:
            output = next((o for o in self.monitors() if o.get("name") == name), None)
            if output is None:
                raise HdrError(f"no monitor named {name}")
        return parse_state(output, self._request(f"hdr-get {name}"))

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Turn HDR on or off for a monitor."""
        self._request(f"hdr {'on' if enabled else 'off'} {self._monitor(name)}")

    def set_sdr_white(self, name: str, nits: int) -> None:
        """SDR white of a monitor in cd/m²."""
        self._request(f"sdr-white {int(nits)} {self._monitor(name)}")

    def set_value(self, name: str, key: str, *values: Any) -> None:
        """One ``hdr-set`` setting, a float is sent with two decimals."""
        words = [f"{v:.2f}" if isinstance(v, float) else str(v) for v in values]
        self._request(f"hdr-set {self._monitor(name)} {key} {' '.join(words)}")

    def reset(self, name: str) -> None:
        """Give the monitor the HDR settings of the config file."""
        self._request(f"hdr-reset {self._monitor(name)}")
