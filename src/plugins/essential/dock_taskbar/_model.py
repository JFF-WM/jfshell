"""Grouping model for the dock taskbar.

Pure functions and plain data only: no GTK, no IPC and no config access, so
the module can be imported and tested without a compositor. The plugin feeds
it the pinned launchers and the window list and gets back the ordered
entries the bar has to show.

Every running window belongs to exactly one entry. A window goes to the first
pinned launcher it matches, otherwise to a group of its own application, so
the bar never shows a lone window and never shows a window title.
"""

import os
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_FIELD_CODE = re.compile(r"\s*%[fFuUdDnNickvm]")

SEPARATOR = "separator"
APP = "app"

# The two parts of the bar a drop can land in (see drop_slot).
PINNED = "pinned"
UNPINNED = "unpinned"


@dataclass
class Entry:
    """One button (or separator) of the bar."""

    key: str
    kind: str = APP
    # The pinned launcher config ({"id", "cmd", "icon", ...}); None when unpinned.
    item: dict | None = None
    # Position of ``item`` in the saved launcher list (-1 when unpinned).
    index: int = -1
    # App id of the running windows, or what the launcher stands for.
    app_id: str = ""
    views: list = field(default_factory=list)

    @property
    def pinned(self) -> bool:
        return self.item is not None

    @property
    def running(self) -> bool:
        return bool(self.views)

    @property
    def focused(self) -> bool:
        return any(v.get("is_focused") for v in self.views)


def norm(value: object) -> str:
    """Lower-cased identifier with everything but letters and digits removed."""
    if not isinstance(value, str):
        return ""
    return _NON_ALNUM.sub("", value.lower())


def key_variants(value: object) -> set[str]:
    """Keys one identifier can be matched by: the whole name and its last dotted part.

    ``org.gnome.Nautilus`` gives ``orggnomenautilus`` and ``nautilus``, so a
    launcher called ``nautilus`` finds the windows of that application.
    """
    if not isinstance(value, str) or not value.strip():
        return set()
    text = value.strip()
    if text.lower().endswith(".desktop"):
        text = text[: -len(".desktop")]
    keys = {norm(text)}
    if "." in text:
        keys.add(norm(text.rsplit(".", 1)[-1]))
    keys.discard("")
    return keys


def command_name(cmd: object) -> str:
    """The program a launcher command starts.

    Leading ``VAR=value`` assignments and ``env`` are skipped, and
    ``flatpak run [options] <app>`` gives the app id.
    """
    if not isinstance(cmd, str) or not cmd.strip():
        return ""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    i = 0
    while i < len(tokens) and (
        tokens[i] == "env" or _ENV_ASSIGNMENT.match(tokens[i]) is not None
    ):
        i += 1
    if i >= len(tokens):
        return ""
    program = os.path.basename(tokens[i])
    rest = tokens[i + 1 :]
    if program == "flatpak" and "run" in rest:
        after = rest[rest.index("run") + 1 :]
        return next((t for t in after if not t.startswith("-")), "")
    return program


def strip_field_codes(commandline: str) -> str:
    """Removes the ``%U``-style placeholders of a desktop entry ``Exec`` line."""
    return _FIELD_CODE.sub("", commandline or "").strip()


def view_app_id(view: dict) -> str:
    """The app id of a window dict (jff reports it as ``app_id`` and ``app-id``)."""
    return view.get("app_id") or view.get("app-id") or ""


def pinned_keys(item: dict) -> set[str]:
    """Keys that tie a launcher to its windows.

    An explicit ``app_id`` (a string or a list) wins. Without one the launcher
    id and the program of its command are used.
    """
    explicit = item.get("app_id")
    if explicit:
        names = explicit if isinstance(explicit, (list, tuple)) else [explicit]
        keys: set[str] = set()
        for name in names:
            keys |= key_variants(name)
        return keys
    return key_variants(item.get("id")) | key_variants(command_name(item.get("cmd")))


def build_entries(
    pinned: list,
    views: list,
    order: list,
    show_unpinned: bool = True,
    extra_keys: dict | None = None,
) -> tuple[list[Entry], list[str]]:
    """Builds the ordered entries of the bar.

    Args:
        pinned: Launcher configs in saved order; ``{"type": "separator"}`` items
            are kept as separators.
        views: Valid window dicts from the compositor.
        order: Keys of the unpinned groups from the previous call, so groups keep
            their place while apps come and go.
        show_unpinned: Whether running apps without a launcher get a button.
        extra_keys: ``app_id`` -> additional match keys (from desktop entries).

    Returns:
        ``(entries, order)``: the entries pinned first and unpinned groups after
        them (a separator between the two parts), and the order to pass back in.
    """
    extra = extra_keys or {}
    head: list[Entry] = []
    launchers: list[tuple[Entry, set[str]]] = []
    for index, item in enumerate(pinned):
        if not isinstance(item, dict):
            continue
        if item.get("type") == SEPARATOR:
            head.append(Entry(key=f"sep:{index}", kind=SEPARATOR, index=index))
            continue
        entry = Entry(
            key=f"pin:{index}:{item.get('id', '')}",
            item=item,
            index=index,
            app_id=str(item.get("app_id") or item.get("id") or ""),
        )
        head.append(entry)
        launchers.append((entry, pinned_keys(item)))

    groups: dict[str, Entry] = {}
    for view in views:
        app_id = view_app_id(view)
        keys = key_variants(app_id) | set(extra.get(app_id, ()))
        owner = next((e for e, k in launchers if keys & k), None)
        if owner is not None:
            owner.views.append(view)
            owner.app_id = app_id
        elif show_unpinned:
            key = f"app:{norm(app_id) or app_id}"
            group = groups.get(key)
            if group is None:
                group = groups[key] = Entry(key=key, app_id=app_id)
            group.views.append(view)

    new_order = [k for k in order if k in groups] + [k for k in groups if k not in order]
    tail = [groups[k] for k in new_order]

    entries = head
    if tail:
        if any(e.kind == APP for e in head) and head[-1].kind != SEPARATOR:
            entries = head + [Entry(key="sep:auto", kind=SEPARATOR)]
        entries = entries + tail
    return entries, new_order


def state_flags(views: list) -> tuple[bool, bool]:
    """Whether any window of a group is always on top, and whether any is sticky.

    jff reports both as the boolean keys ``always-on-top`` and ``sticky`` of a
    window dict. A missing key or a ``None`` value counts as off, and items
    that are not dicts are ignored, so an empty group gives ``(False, False)``.
    """
    atop = False
    sticky = False
    for view in views or ():
        if not isinstance(view, dict):
            continue
        atop = atop or bool(view.get("always-on-top"))
        sticky = sticky or bool(view.get("sticky"))
    return atop, sticky


def container_id(view):
    """The jff container id of a window dict, or None when it is in no container."""
    container = view.get("container") if isinstance(view, dict) else None
    if isinstance(container, dict):
        return container.get("id")
    return None


def preview_views(views, limit=None):
    """The windows to draw a thumbnail for, one per jff container.

    jff draws a whole container as one picture, so every window of a container
    would give the same thumbnail: only one of them is kept (the focused one
    when there is one, else the first). Windows outside any container keep
    their own thumbnail. Order follows the first window of each container;
    ``limit`` is applied after the grouping.
    """
    items = [v for v in views or () if isinstance(v, dict)]
    chosen = {}
    for view in items:
        cid = container_id(view)
        if cid is None:
            continue
        current = chosen.get(cid)
        if current is None or (view.get("is_focused") and not current.get("is_focused")):
            chosen[cid] = view
    result, seen = [], set()
    for view in items:
        cid = container_id(view)
        if cid is None:
            result.append(view)
        elif cid not in seen:
            seen.add(cid)
            result.append(chosen[cid])
    return result[:limit] if limit else result


def moved(items: list, index: int, step: int) -> list:
    """A copy of ``items`` with the item at ``index`` moved ``step`` places."""
    target = index + step
    if not (0 <= index < len(items) and 0 <= target < len(items)):
        return list(items)
    result = list(items)
    result.insert(target, result.pop(index))
    return result


def moved_to(items: list, index: int, slot: int) -> list:
    """A copy of ``items`` with the item at ``index`` moved to the gap ``slot``.

    Gaps count in the original list: ``0`` is before the first item and
    ``len(items)`` after the last one, so the gaps on both sides of the item
    itself leave the list as it is. Out of range slots are clamped.
    """
    if not 0 <= index < len(items):
        return list(items)
    slot = max(0, min(slot, len(items)))
    result = list(items)
    item = result.pop(index)
    if slot > index:
        slot -= 1
    result.insert(slot, item)
    return result


def drop_slot(
    entries: list[Entry],
    order: list,
    pinned_count: int,
    target_key: str,
    after: bool,
) -> tuple[str, int] | None:
    """Where a drop next to an entry lands.

    Args:
        entries: What the bar shows (from build_entries).
        order: The unpinned group order (from build_entries).
        pinned_count: Length of the launcher list ``entries`` was built from.
        target_key: Key of the entry the drop is next to.
        after: Whether the drop is after (right of) that entry.

    Returns:
        ``(PINNED, gap in the launcher list)`` or ``(UNPINNED, gap in order)``;
        None when the target is not on the bar.
    """
    target = next((e for e in entries if e.key == target_key), None)
    if target is None:
        return None
    step = 1 if after else 0
    if target.index >= 0:
        # A launcher or a separator saved in the launcher list.
        return PINNED, target.index + step
    if target.kind == SEPARATOR:
        # The automatic separator between the launchers and the other apps.
        return (UNPINNED, 0) if after else (PINNED, pinned_count)
    if target_key in order:
        return UNPINNED, order.index(target_key) + step
    return UNPINNED, len(order)


def apply_drop(
    pinned: list,
    order: list,
    entries: list[Entry],
    source_key: str,
    target_key: str,
    after: bool,
    make_item: Callable[[Entry], dict | None] | None = None,
) -> tuple[list, list]:
    """The launcher list and group order after an icon is dropped somewhere.

    * A launcher moves within the launchers. Dropped among the other apps it
      stays pinned and goes last, so a drag never unpins anything.
    * An unpinned app dropped among the launchers is pinned at that place,
      when ``make_item`` gives a launcher for it; otherwise (no command to
      start it, or a launcher that already matches it) it goes first among
      the other apps.
    * An unpinned app dropped among the other apps changes their order.

    Args:
        pinned: The launcher list ``entries`` was built from.
        order: The unpinned group order ``entries`` was built with.
        entries: What the bar shows.
        source_key: Key of the dragged entry.
        target_key: Key of the entry it was dropped next to.
        after: Whether it was dropped after that entry.
        make_item: Builds the launcher config of an unpinned entry, or None.

    Returns:
        ``(pinned, order)``: new lists; equal to the inputs when nothing moves.
    """
    source = next((e for e in entries if e.key == source_key), None)
    slot = drop_slot(entries, order, len(pinned), target_key, after)
    if source is None or source.kind != APP or slot is None:
        return list(pinned), list(order)
    part, gap = slot
    if source.pinned:
        if part == UNPINNED:
            gap = len(pinned)
        return moved_to(pinned, source.index, gap), list(order)
    if part == PINNED:
        item = make_item(source) if make_item is not None else None
        if item is not None:
            added = with_pinned(pinned, item, at=gap)
            if len(added) > len(pinned):
                return added, [k for k in order if k != source_key]
        gap = 0
    if source_key not in order:
        return list(pinned), list(order)
    return list(pinned), moved_to(order, order.index(source_key), gap)


def without(items: list, index: int) -> list:
    """A copy of ``items`` without the item at ``index``."""
    return [item for i, item in enumerate(items) if i != index]


def with_pinned(items: list, item: dict, at: int | None = None) -> list:
    """A copy of ``items`` with ``item`` added, unless a launcher already matches it.

    The item is appended, or inserted at index ``at`` when it is given.
    """
    keys = pinned_keys(item)
    for existing in items:
        if (
            isinstance(existing, dict)
            and existing.get("type") != SEPARATOR
            and keys & pinned_keys(existing)
        ):
            return list(items)
    if at is None:
        return [*items, item]
    result = list(items)
    result.insert(max(0, min(at, len(result))), item)
    return result
