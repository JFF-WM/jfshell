"""
Pure logic of the global menu: no GTK, no D-Bus.

* pick the dbusmenu address that belongs to the focused window,
* turn a ``GetLayout`` reply into plain entries,
* group entries into sections and clean up dbusmenu labels,
* decide whether the bar is shown in full or collapsed into one Menu button.
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DBUSMENU_INTERFACE = "com.canonical.dbusmenu"
"""Interface jff files the menu of a window under (xdg-dbus-annotation and KDE appmenu)."""

MENU_PROPS: List[str] = [
    "type",
    "label",
    "enabled",
    "visible",
    "toggle-type",
    "toggle-state",
    "children-display",
]
"""Properties asked for in ``GetLayout``, the rest is of no use to a text menu."""

MODES: Tuple[str, ...] = ("auto", "full", "compact")
"""How the bar is shown: ``auto`` falls back to ``compact`` when ``full`` does not fit."""

DEFAULT_MODE = "auto"


@dataclass(frozen=True)
class MenuEntry:
    """One row of a dbusmenu."""

    id: int
    label: str
    separator: bool = False
    enabled: bool = True
    toggle: str = ""
    """``""``, ``"checkmark"`` or ``"radio"``."""
    checked: bool = False
    has_submenu: bool = False
    """True when the app says the row opens a submenu, even if it sent no children yet."""
    children: Tuple["MenuEntry", ...] = ()


def select_menu_address(
    annotations: Iterable[Dict[str, Any]], window_id: Optional[int]
) -> Optional[Tuple[str, str]]:
    """``(service_name, object_path)`` of the dbusmenu of ``window_id``, or None.

    Annotations without an address yet and annotations of the app itself
    (``window`` null) are skipped. When a window has several, the newest wins.
    """
    if window_id is None:
        return None
    best: Optional[Dict[str, Any]] = None
    for a in annotations:
        if a.get("interface") != DBUSMENU_INTERFACE or a.get("window") != window_id:
            continue
        if not a.get("service_name") or not a.get("object_path"):
            continue
        if best is None or a["id"] > best["id"]:
            best = a
    if best is None:
        return None
    return best["service_name"], best["object_path"]


def normalize_label(label: str) -> str:
    """Strip dbusmenu mnemonics: ``_File`` -> ``File``, ``Save__As`` -> ``Save_As``."""
    out: List[str] = []
    i = 0
    while i < len(label):
        ch = label[i]
        if ch == "_":
            if label[i + 1 : i + 2] == "_":
                out.append("_")
                i += 2
                continue
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _unwrap(value: Any) -> Any:
    """The python value of a dbus-fast ``Variant``, other values unchanged."""
    return getattr(value, "value", value)


def _parse_node(node: Any) -> Tuple[MenuEntry, bool]:
    """One ``(ia{sv}av)`` struct as an entry, and whether the app wants it shown."""
    item_id, raw_props, raw_children = _unwrap(node)
    props = {k: _unwrap(v) for k, v in dict(raw_props).items()}
    parsed = [_parse_node(child) for child in (raw_children or [])]
    children = tuple(entry for entry, visible in parsed if visible)
    toggle = str(props.get("toggle-type", "") or "")
    entry = MenuEntry(
        id=int(item_id),
        label=normalize_label(str(props.get("label", "") or "")),
        separator=props.get("type", "standard") == "separator",
        enabled=props.get("enabled", True) is not False,
        toggle=toggle if toggle in ("checkmark", "radio") else "",
        checked=int(props.get("toggle-state", -1) or 0) == 1,
        has_submenu=props.get("children-display", "") == "submenu" or bool(children),
        children=children,
    )
    return entry, props.get("visible", True) is not False


def parse_layout(layout: Sequence[Any]) -> List[MenuEntry]:
    """Children of the root of a ``GetLayout`` reply (the ``(ia{sv}av)`` struct).

    Rows the app marked invisible are dropped.
    """
    return list(_parse_node(layout)[0].children)


def split_sections(entries: Sequence[MenuEntry]) -> List[List[MenuEntry]]:
    """Group rows between separators. Empty groups (leading, trailing, doubled) vanish."""
    sections: List[List[MenuEntry]] = [[]]
    for entry in entries:
        if entry.separator:
            sections.append([])
        else:
            sections[-1].append(entry)
    return [s for s in sections if s]


def entry_label(entry: MenuEntry) -> str:
    """Label with a marker for checked and radio rows, which Gio menus can't show stateless."""
    if entry.toggle == "checkmark" and entry.checked:
        return f"✓ {entry.label}"
    if entry.toggle == "radio" and entry.checked:
        return f"● {entry.label}"
    return entry.label


def normalize_mode(value: Any) -> str:
    """A valid mode from a config value, ``auto`` for anything else."""
    text = "" if value is None else str(value).strip().lower()
    return text if text in MODES else DEFAULT_MODE


def use_compact(mode: str, allocated_width: int, full_width: int) -> bool:
    """True when one Menu button should replace the row of top level entries.

    ``full_width`` is the natural width of the whole row, ``allocated_width`` the width
    the panel layout gave the bar.
    """
    if mode == "compact":
        return True
    if mode == "full":
        return False
    return allocated_width < full_width
