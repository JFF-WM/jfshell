"""
GTK side of the global menu: the GMenu of one GTK app, read from ``org.gtk.Menus``
and driven through ``org.gtk.Actions``. It answers like DBusMenuClient so the
plugin can use either one. Everything here runs on the global asyncio loop.

* ``<application path>/menus/menubar`` (or ``appmenu``) holds the menu model,
* ``<application path>`` holds the ``app.`` actions, the window object the ``win.`` ones.
"""

import asyncio
import typing
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from dbus_fast.aio import MessageBus
from dbus_fast.introspection import Node

from ._menu_model import GAction, MenuEntry, gmenu_children, gmenu_top

MENU_NAMES: Tuple[str, ...] = ("menubar", "appmenu")
"""Objects tried under ``<application path>/menus/``, in this order."""

_MENUS_XML = """
<node>
  <interface name="org.gtk.Menus">
    <method name="Start">
      <arg type="au" name="subscriptions" direction="in"/>
      <arg type="a(uuaa{sv})" name="content" direction="out"/>
    </method>
    <method name="End">
      <arg type="au" name="subscriptions" direction="in"/>
    </method>
    <signal name="Changed">
      <arg type="a(uuuuaa{sv})" name="changes"/>
    </signal>
  </interface>
</node>
"""

_ACTIONS_XML = """
<node>
  <interface name="org.gtk.Actions">
    <method name="DescribeAll">
      <arg type="a{s(bgav)}" name="descriptions" direction="out"/>
    </method>
    <method name="Activate">
      <arg type="s" name="action_name" direction="in"/>
      <arg type="av" name="parameter" direction="in"/>
      <arg type="a{sv}" name="platform_data" direction="in"/>
    </method>
  </interface>
</node>
"""


def _value(variant: Any) -> Any:
    """The python value of a dbus-fast ``Variant``, other values unchanged."""
    return getattr(variant, "value", variant)


class GMenuClient:
    """The ``org.gtk.Menus`` model of one GTK app."""

    def __init__(
        self,
        bus: MessageBus,
        service_name: str,
        app_path: str,
        window_path: Optional[str],
        logger: typing.Any,
    ) -> None:
        self.bus = bus
        self.service_name = service_name
        self.app_path = app_path
        self.window_path = window_path
        self.logger = logger
        self._menus_iface: typing.Any = None
        self._action_ifaces: Dict[str, typing.Any] = {}
        self._menus: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        self._groups: Set[int] = set()
        self._ids: Dict[Tuple[Any, ...], int] = {}
        self._keys: Dict[int, Tuple[Any, ...]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_layout_updated: Optional[Callable[[int], None]] = None

    def _interface(self, path: str, xml: str, name: str) -> typing.Any:
        proxy = self.bus.get_proxy_object(self.service_name, path, Node.parse(xml))
        return proxy.get_interface(name)

    def _store(self, content: List[Any]) -> None:
        for group, menu, items in content:
            self._menus[(int(group), int(menu))] = [dict(item) for item in items]

    def _linked_groups(self) -> Set[int]:
        """Groups that sections and submenus point to."""
        groups: Set[int] = set()
        for items in self._menus.values():
            for item in items:
                for name in (":section", ":submenu"):
                    if name in item:
                        groups.add(int(_value(item[name])[0]))
        return groups

    async def _load_links(self) -> None:
        """Subscribe to every group reachable from the root, they come in one reply each."""
        while self._menus_iface is not None:
            wanted = self._linked_groups() - self._groups
            if not wanted:
                return
            self._groups |= wanted
            self._store(await self._menus_iface.call_start(sorted(wanted)))

    async def _stop(self) -> None:
        """Drop the subscriptions and forget the model."""
        iface, groups = self._menus_iface, sorted(self._groups)
        self._menus_iface = None
        self._menus = {}
        self._groups = set()
        if iface is not None and groups:
            try:
                await iface.call_end(groups)
            except Exception:
                pass

    async def connect(self, on_layout_updated: Callable[[int], None]) -> None:
        """Find the app's menu and subscribe to it.

        ``on_layout_updated(0)`` runs on the asyncio loop when a top level row changes.
        An app that exports no menu is not an error: ``fetch`` just returns nothing.
        """
        self._loop = asyncio.get_running_loop()
        self._on_layout_updated = on_layout_updated
        base = self.app_path.rstrip("/")
        for name in MENU_NAMES:
            iface = self._interface(f"{base}/menus/{name}", _MENUS_XML, "org.gtk.Menus")
            try:
                content = await iface.call_start([0])
            except Exception:
                continue
            self._menus_iface = iface
            self._groups = {0}
            self._store(content)
            if self._menus.get((0, 0)):
                break
            await self._stop()
        if self._menus_iface is None:
            return
        await self._load_links()
        self._action_ifaces = {
            "app": self._interface(self.app_path, _ACTIONS_XML, "org.gtk.Actions")
        }
        if self.window_path:
            self._action_ifaces["win"] = self._interface(
                self.window_path, _ACTIONS_XML, "org.gtk.Actions"
            )
        self._menus_iface.on_changed(self._changed)

    def _top_menus(self) -> Set[Tuple[int, int]]:
        """The root and the sections that are flattened into it: they hold the top level rows."""
        top = {(0, 0)}
        pending = [(0, 0)]
        while pending:
            for item in self._menus.get(pending.pop(), []):
                if ":section" in item:
                    group, menu = _value(item[":section"])
                    link = (int(group), int(menu))
                    if link not in top:
                        top.add(link)
                        pending.append(link)
        return top

    def _changed(self, changes: List[Any]) -> None:
        """Apply the ``Changed`` signal to the model: replace ``removed`` items at ``position``."""
        touched = False
        top = self._top_menus()
        for group, menu, position, removed, added in changes:
            key = (int(group), int(menu))
            items = self._menus.setdefault(key, [])
            items[position : position + removed] = [dict(item) for item in added]
            touched = touched or key in top
        asyncio.ensure_future(self._load_links())
        if touched and self._on_layout_updated is not None:
            self._on_layout_updated(0)

    def _id_of(self, key: Tuple[Any, ...]) -> int:
        """Stable row id of ``key``, numbered from 1 (0 is the menu bar)."""
        item_id = self._ids.get(key)
        if item_id is None:
            item_id = len(self._ids) + 1
            self._ids[key] = item_id
            self._keys[item_id] = key
        return item_id

    async def _describe_actions(self) -> Dict[str, GAction]:
        """Every action of the app and the window by its prefixed name (``win.save``)."""
        described: Dict[str, GAction] = {}
        for prefix, iface in self._action_ifaces.items():
            try:
                raw = await iface.call_describe_all()
            except Exception as e:
                self.logger.warning(
                    f"DescribeAll failed on {self.service_name} ({prefix}): {e}"
                )
                continue
            for name, (enabled, _parameter_type, state) in raw.items():
                described[f"{prefix}.{name}"] = GAction(
                    enabled=bool(enabled),
                    state=_value(state[0]) if state else None,
                )
        return described

    def close(self) -> None:
        """Stop reporting changes and give the subscriptions back."""
        self._on_layout_updated = None
        iface = self._menus_iface
        if iface is not None:
            try:
                iface.off_changed(self._changed)
            except Exception:
                pass
        loop = self._loop
        if loop is not None and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._stop(), loop)

    async def fetch(self, parent_id: int = 0, depth: int = -1) -> List[MenuEntry]:
        """Children of ``parent_id`` (0 is the menu bar), down to ``depth`` levels."""
        if self._menus_iface is None:
            return []
        actions = await self._describe_actions()
        if parent_id == 0:
            return gmenu_top(self._menus, actions, self._id_of, depth)
        key = self._keys.get(parent_id)
        if key is None:
            return []
        return gmenu_children(self._menus, key, actions, self._id_of, depth)

    async def click(self, item_id: int) -> None:
        """Activate the action of the row."""
        key = self._keys.get(item_id)
        if self._menus_iface is None or key is None or len(key) != 3:
            return
        items = self._menus.get((key[0], key[1]), [])
        if key[2] >= len(items):
            return
        item = items[key[2]]
        if "action" not in item:
            return
        prefix, _, name = str(_value(item["action"])).partition(".")
        iface = self._action_ifaces.get(prefix)
        if iface is None or not name:
            return
        target = item.get("target")
        try:
            await iface.call_activate(name, [] if target is None else [target], {})
        except Exception as e:
            self.logger.warning(f"Activate({name}) failed on {self.service_name}: {e}")
