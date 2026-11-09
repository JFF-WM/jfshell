"""
Click-to-open logic shared by the popup toast (_notify_server_ui) and the
panel history (notify_client).

What a click does, in order:
  1. An explicit ``uri``/``url`` hint is opened with xdg-open.
  2. If the sender registered a "default" action, ``ActionInvoked(id,
     "default")`` is emitted so the app handles its own activation (this is
     how Telegram, Discord, Thunderbird, ... open the right chat/mail).
  3. The app's window is focused (matched by desktop-entry / app_name against
     the compositor's app_id).
  4. If no window exists and the app did not register a default action, the
     app is launched from its .desktop file (only when one is found).
  5. Last resort: a URL found in the summary/body is opened.
"""

import re
import shlex
from typing import Any, Dict, List, Optional

URL_REGEX = re.compile(r"(?:https?|ftp|file)://\S+|www\.\S+")


def unwrap(value: Any) -> Any:
    """Strip dbus-fast Variants / bytes so hints can be treated as plain data."""
    while hasattr(value, "value") and not isinstance(value, (str, bytes)):
        value = value.value
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return None
    return value


def get_hint(hints: Any, *keys: str) -> Optional[str]:
    """First non-empty string hint among ``keys`` (None if absent)."""
    if not isinstance(hints, dict):
        return None
    for key in keys:
        value = unwrap(hints.get(key))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_first_uri(text: str) -> Optional[str]:
    matches = URL_REGEX.findall(text or "")
    if not matches:
        return None
    uri = matches[0].rstrip(".,;)>\"'")
    if uri.startswith("www.") and "://" not in uri:
        uri = "http://" + uri
    return uri


def parse_actions(actions: Any) -> List[str]:
    """Actions as a flat [id, label, id, label, ...] list. History rows come
    back from SQLite as a JSON string (or a legacy comma-joined string)."""
    actions = unwrap(actions)
    if isinstance(actions, str):
        text = actions.strip()
        if text.startswith("["):
            try:
                import json

                actions = json.loads(text)
            except ValueError:
                actions = text.split(",")
        else:
            actions = text.split(",") if text else []
    if not isinstance(actions, (list, tuple)):
        return []
    return [str(unwrap(a)) for a in actions]


def has_default_action(actions: Any) -> bool:
    actions = parse_actions(actions)
    return "default" in actions[::2]


def _norm(name: str) -> str:
    name = (name or "").lower().strip()
    if name.endswith(".desktop"):
        name = name[: -len(".desktop")]
    return name


def app_candidates(notification: Dict[str, Any]) -> List[str]:
    """Identifiers that could name the sending app, best guess first."""
    hints = notification.get("hints") or {}
    out: List[str] = []
    for raw in (
        get_hint(hints, "desktop-entry", "desktop_entry"),
        notification.get("app_name"),
    ):
        name = _norm(raw or "")
        if not name or name in out:
            continue
        out.append(name)
    return out


def _find_view(panel, candidates: List[str]) -> Optional[dict]:
    try:
        views = panel.ipc.list_views() or []
    except Exception:
        return None
    best = None
    for cand in candidates:
        for view in views:
            app_id = _norm(view.get("app_id") or "")
            if not app_id or app_id == "nil":
                continue
            if app_id == cand:
                return view  # exact match wins immediately
            if best is None and len(cand) >= 3 and (
                cand in app_id or app_id in cand
            ):
                best = view
    return best


def _focus_view(plugin, view: dict) -> bool:
    try:
        plugin.ipc.set_focus(view["id"])
        try:
            plugin.compositor_helper.move_cursor_middle(view["id"])
        except Exception:
            pass
        return True
    except Exception as e:
        plugin.logger.error(f"Could not focus view {view.get('id')}: {e}")
        return False


def _launch_app(plugin, notification, candidates: List[str]) -> bool:
    """Start the app from its .desktop file (or, failing that, by name)."""
    try:
        from gi.repository import Gio
    except Exception:
        Gio = None

    app_info = None
    if Gio is not None:
        for cand in candidates:
            app_info = Gio.DesktopAppInfo.new(f"{cand}.desktop")
            if app_info:
                break
        if not app_info:
            wanted = {c.lower() for c in candidates}
            for info in Gio.AppInfo.get_all():
                ids = {_norm(info.get_id() or ""), (info.get_name() or "").lower()}
                if ids & wanted:
                    app_info = info
                    break
    if app_info and app_info.get_id():
        plugin.cmd.run(f"gtk-launch {shlex.quote(app_info.get_id())}")
        return True
    return False  # no .desktop file: don't guess a shell command


def activate_notification(plugin, notification: Dict[str, Any], emit_action=None) -> bool:
    """
    Perform the click action for ``notification``.

    ``plugin`` is any BasePlugin (needs .ipc, .cmd, .logger, .compositor_helper).
    ``emit_action(id, key)`` sends org.freedesktop.Notifications.ActionInvoked.
    Returns True when something was opened/triggered.
    """
    hints = notification.get("hints") or {}
    nid = notification.get("id", 0)

    # 1. explicit link from the sender
    uri = get_hint(hints, "uri", "url")
    if uri:
        plugin.cmd.open_url(uri)
        return True

    handled = False
    default_registered = has_default_action(notification.get("actions"))

    # 2. let the app handle its own activation
    if default_registered and emit_action is not None:
        try:
            emit_action(nid, "default")
            handled = True
        except Exception as e:
            plugin.logger.error(f"Could not emit default action for {nid}: {e}")

    # 3. bring the app's window to the front
    candidates = app_candidates(notification)
    view = _find_view(plugin, candidates) if candidates else None
    if view and _focus_view(plugin, view):
        handled = True

    # 4. nothing running: start it (an app that registered "default" is alive
    #    and handles it itself, so never double-launch that case)
    if not handled and not default_registered and candidates:
        handled = _launch_app(plugin, notification, candidates)

    # 5. a link in the text is better than doing nothing
    if not handled:
        text_uri = extract_first_uri(
            f"{notification.get('summary', '')} {notification.get('body', '')}"
        )
        if text_uri:
            plugin.cmd.open_url(text_uri)
            handled = True

    return handled
