"""
D-Bus side of the global menu. Everything here runs on the global asyncio loop,
the GTK side (global_menu.py) hops to the GTK thread before touching widgets.

* Registrar       - owns ``com.canonical.AppMenu.Registrar``. Qt, GTK's appmenu module
                    and Chromium only export their menu bar over D-Bus while somebody
                    owns that name, so without it jff never gets an address.
* DBusMenuClient  - talks ``com.canonical.dbusmenu`` to one app.
"""

import typing
from typing import Callable, Dict, List, Optional, Set

from dbus_fast import BusType, NameFlag, RequestNameReply, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.introspection import Node
from dbus_fast.service import ServiceInterface, method

from ._menu_model import MENU_PROPS, MenuEntry, parse_layout

REGISTRAR_NAME = "com.canonical.AppMenu.Registrar"
REGISTRAR_PATH = "/com/canonical/AppMenu/Registrar"

# The menu object is introspected with this instead of asking the app: Chromium
# answers with an empty node, which left dbus-fast without an interface to call.
_DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>
"""


class Registrar(ServiceInterface):
    """``com.canonical.AppMenu.Registrar``: takes the apps' announcements and ignores them.

    jff already tells the panel where each window's menu lives, so nothing is
    looked up here. The name only has to exist (and answer) for apps to export a menu.
    """

    def __init__(self) -> None:
        super().__init__("com.canonical.AppMenu.Registrar")
        self.windows: Dict[int, str] = {}

    @method()
    def RegisterWindow(self, windowId: "u", menuObjectPath: "o"):  # pyright: ignore
        self.windows[windowId] = menuObjectPath

    @method()
    def UnregisterWindow(self, windowId: "u"):  # pyright: ignore
        self.windows.pop(windowId, None)


async def connect_session_bus(logger: typing.Any) -> MessageBus:
    """Connect to the session bus and own the registrar name on it.

    The bus is returned even when another host already owns the name: the menus
    are still read through it, only apps will keep registering with the other host.
    """
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bus.export(REGISTRAR_PATH, Registrar())
    reply = await bus.request_name(REGISTRAR_NAME, NameFlag.DO_NOT_QUEUE)
    if reply != RequestNameReply.PRIMARY_OWNER:
        logger.warning(
            f"{REGISTRAR_NAME} is owned by another global menu host (reply={reply!r})."
        )
    else:
        logger.info(f"Owning {REGISTRAR_NAME}.")
    return bus


class DBusMenuClient:
    """The dbusmenu of one app."""

    def __init__(
        self, bus: MessageBus, service_name: str, object_path: str, logger: typing.Any
    ) -> None:
        self.bus = bus
        self.service_name = service_name
        self.object_path = object_path
        self.logger = logger
        self._iface: typing.Any = None
        self._on_layout_updated: Optional[Callable[[int], None]] = None
        self._top_ids: Set[int] = set()

    async def connect(self, on_layout_updated: Callable[[int], None]) -> None:
        """Bind the interface. ``on_layout_updated(parent_id)`` runs on the asyncio loop."""
        proxy = self.bus.get_proxy_object(
            self.service_name, self.object_path, Node.parse(_DBUSMENU_XML)
        )
        self._iface = proxy.get_interface("com.canonical.dbusmenu")
        self._on_layout_updated = on_layout_updated
        self._iface.on_layout_updated(self._layout_updated)
        self._iface.on_items_properties_updated(self._items_updated)

    def _layout_updated(self, _revision: int, parent: int) -> None:
        if self._on_layout_updated is not None:
            self._on_layout_updated(parent)

    def _items_updated(
        self, updated: List[typing.Any], removed: List[typing.Any]
    ) -> None:
        """Report a property change (``enabled``, ``label``...) of a top level row.

        Apps flip these through ``ItemsPropertiesUpdated`` without ever emitting
        ``LayoutUpdated``, so the bar would keep showing the stale row. Changes
        deeper in the tree are ignored: the submenus are fetched on click.

        Args:
            updated: ``(id, properties)`` pairs of the rows that changed.
            removed: ``(id, property_names)`` pairs of the rows that lost properties.
        """
        changed = {item[0] for item in updated} | {item[0] for item in removed}
        if self._on_layout_updated is not None and changed & self._top_ids:
            self._on_layout_updated(0)

    def close(self) -> None:
        """Stop reporting layout changes."""
        self._on_layout_updated = None
        if self._iface is not None:
            try:
                self._iface.off_layout_updated(self._layout_updated)
                self._iface.off_items_properties_updated(self._items_updated)
            except Exception:
                pass
            self._iface = None

    async def fetch(self, parent_id: int = 0, depth: int = -1) -> List[MenuEntry]:
        """Children of ``parent_id`` (0 is the menu bar), down to ``depth`` levels."""
        if self._iface is None:
            return []
        try:
            await self._iface.call_about_to_show(parent_id)  # apps fill menus lazily
        except Exception:
            pass
        try:
            _revision, layout = await self._iface.call_get_layout(
                parent_id, depth, MENU_PROPS
            )
        except Exception as e:
            self.logger.warning(
                f"GetLayout({parent_id}) failed on {self.service_name}{self.object_path}: {e}"
            )
            return []
        entries = parse_layout(layout)
        if parent_id == 0:
            self._top_ids = {entry.id for entry in entries}
        return entries

    async def click(self, item_id: int) -> None:
        """Tell the app the row was clicked."""
        if self._iface is None:
            return
        try:
            await self._iface.call_event(item_id, "clicked", Variant("s", ""), 0)
        except Exception as e:
            self.logger.warning(f"Event({item_id}) failed on {self.service_name}: {e}")
