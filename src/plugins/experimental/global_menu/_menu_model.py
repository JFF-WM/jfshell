"""
Pure logic of the global menu: no GTK, no D-Bus.

* pick the dbusmenu address that belongs to the focused window,
* turn a ``GetLayout`` reply into plain entries,
* group entries into sections and clean up dbusmenu labels,
* decide whether the bar is shown in full or collapsed into one Menu button,
* turn the GMenu of a GTK app (``org.gtk.Menus`` + ``org.gtk.Actions``) into the same entries.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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

GTK_ACTIONS_APPLICATION = "org.gtk.Actions:application"
"""Annotation of the GApplication object, the ``menus/menubar`` and ``menus/appmenu`` objects hang off its path."""

GTK_ACTIONS_WINDOW = "org.gtk.Actions:window"
"""Annotation of the window object, where the ``win.`` actions live."""

APP_ROW_KEY: Tuple[Any, ...] = ("app",)
"""Key of the one row that stands for a whole app menu."""

APP_ROW_LABEL = "Application"
"""Label of that row."""

GMenus = Dict[Tuple[int, int], List[Dict[str, Any]]]
"""``org.gtk.Menus`` content: ``(group, menu)`` -> items, each item a dict of attributes and links."""

IdOf = Callable[[Tuple[Any, ...]], int]
"""Gives the stable row id of a key: ``(group, menu, index)`` for an item, longer or shorter for the rest."""

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
    """The python value of a dbus-fast ``Variant``, other values unchanged.

    Variants are peeled until none is left: some apps send ``v`` inside ``v``
    (the children of a ``GetLayout`` row, a property value), and a single peel
    would hand a ``Variant`` to code that expects a struct or a string.
    """
    for _ in range(16):  # bounded: never loop on a self referencing object
        if isinstance(value, (str, bytes, int, float, bool, list, tuple, dict)):
            return value
        inner = getattr(value, "value", value)
        if inner is value:
            return value
        value = inner
    return value


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


@dataclass(frozen=True)
class GAction:
    """State of one ``org.gtk.Actions`` entry."""

    enabled: bool = True
    state: Any = None
    """Python value of the action state, None for a stateless action."""


def select_gmenu_address(
    annotations: Iterable[Dict[str, Any]], window_id: Optional[int]
) -> Optional[Tuple[str, str, Optional[str]]]:
    """``(service_name, application_path, window_path)`` of the GTK menus of ``window_id``, or None.

    GTK apps do not annotate their menus, only their action groups. The menus
    sit at ``<application_path>/menus/...`` on the same name. ``window_path`` is None
    when the window annotated no action group of its own. The newest annotation wins.
    """
    if window_id is None:
        return None
    newest: Dict[str, Dict[str, Any]] = {}
    for a in annotations:
        interface = a.get("interface")
        if interface not in (GTK_ACTIONS_APPLICATION, GTK_ACTIONS_WINDOW):
            continue
        if a.get("window") != window_id:
            continue
        if not a.get("service_name") or not a.get("object_path"):
            continue
        best = newest.get(interface)
        if best is None or a["id"] > best["id"]:
            newest[interface] = a
    app = newest.get(GTK_ACTIONS_APPLICATION)
    if app is None:
        return None
    window = newest.get(GTK_ACTIONS_WINDOW)
    window_path = None
    if window is not None and window["service_name"] == app["service_name"]:
        window_path = window["object_path"]
    return app["service_name"], app["object_path"], window_path


def _link(item: Dict[str, Any], name: str) -> Optional[Tuple[int, int]]:
    """``(group, menu)`` an item links to as ``:section`` or ``:submenu``, or None."""
    value = item.get(name)
    if value is None:
        return None
    group, menu = _unwrap(value)
    return int(group), int(menu)


def _gmenu_row(
    menus: GMenus,
    item: Dict[str, Any],
    key: Tuple[Any, ...],
    actions: Dict[str, GAction],
    id_of: IdOf,
    depth: int,
) -> Optional[MenuEntry]:
    """One non section item as an entry, None when it has no label or is hidden."""
    label = normalize_label(str(_unwrap(item["label"]))) if "label" in item else ""
    submenu = _link(item, ":submenu")
    if submenu is not None:
        children: Tuple[MenuEntry, ...] = ()
        if depth != 1:
            below = depth - 1 if depth > 0 else depth
            children = tuple(gmenu_entries(menus, submenu, actions, id_of, below))
        return MenuEntry(
            id=id_of(key), label=label, has_submenu=True, children=children
        )
    if not label:
        return None
    name = str(_unwrap(item["action"])) if "action" in item else ""
    action = actions.get(name)
    hidden_when = str(_unwrap(item["hidden-when"])) if "hidden-when" in item else ""
    if hidden_when == "action-missing" and action is None:
        return None
    if hidden_when == "action-disabled" and (action is None or not action.enabled):
        return None
    state = action.state if action is not None else None
    target = _unwrap(item["target"]) if "target" in item else None
    toggle, checked = "", False
    if isinstance(state, bool):
        toggle, checked = "checkmark", state
    elif state is not None and target is not None:
        toggle, checked = "radio", state == target
    return MenuEntry(
        id=id_of(key),
        label=label,
        enabled=action is not None and action.enabled,
        toggle=toggle,
        checked=checked,
    )


def gmenu_entries(
    menus: GMenus,
    menu: Tuple[int, int],
    actions: Dict[str, GAction],
    id_of: IdOf,
    depth: int = -1,
) -> List[MenuEntry]:
    """Rows of one GMenu as entries, sections flattened between separators.

    ``depth`` counts levels like the dbusmenu ``GetLayout``: 1 is the rows alone
    (submenus come without children), -1 is everything below.
    """
    entries: List[MenuEntry] = []
    for index, item in enumerate(menus.get(menu, [])):
        key = (menu[0], menu[1], index)
        section = _link(item, ":section")
        if section is not None:
            entries.append(
                MenuEntry(id=id_of(key + ("before",)), label="", separator=True)
            )
            entries.extend(gmenu_entries(menus, section, actions, id_of, depth))
            entries.append(
                MenuEntry(id=id_of(key + ("after",)), label="", separator=True)
            )
            continue
        row = _gmenu_row(menus, item, key, actions, id_of, depth)
        if row is not None:
            entries.append(row)
    return entries


def gmenu_top(
    menus: GMenus, actions: Dict[str, GAction], id_of: IdOf, depth: int = -1
) -> List[MenuEntry]:
    """Top level rows of a GTK app's menu.

    A menubar is a list of submenus and is returned as it is. An app menu is a list
    of plain rows, they are put under one ``Application`` row instead.
    """
    rows = gmenu_entries(menus, (0, 0), actions, id_of, depth)
    shown = [row for row in rows if not row.separator]
    if not shown or any(row.has_submenu for row in shown):
        return rows
    children = () if depth == 1 else tuple(rows)
    return [
        MenuEntry(
            id=id_of(APP_ROW_KEY),
            label=APP_ROW_LABEL,
            has_submenu=True,
            children=children,
        )
    ]


def gmenu_children(
    menus: GMenus,
    key: Tuple[Any, ...],
    actions: Dict[str, GAction],
    id_of: IdOf,
    depth: int = -1,
) -> List[MenuEntry]:
    """Rows below the row ``key``, empty for a row that has no submenu."""
    if key == APP_ROW_KEY:
        return gmenu_entries(menus, (0, 0), actions, id_of, depth)
    if len(key) != 3:
        return []
    items = menus.get((key[0], key[1]), [])
    if key[2] >= len(items):
        return []
    submenu = _link(items[key[2]], ":submenu")
    if submenu is None:
        return []
    return gmenu_entries(menus, submenu, actions, id_of, depth)
