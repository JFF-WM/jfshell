"""
StatusNotifier (system tray) D-Bus side.

* StatusNotifierWatcher  - the org.kde.StatusNotifierWatcher service apps
                           register their tray items with.
* StatusNotifierItem     - proxy for one registered item (icon, tooltip,
                           menu, click methods) that announces changes to the
                           tray widget through the IPC event bus.

Everything in here runs on the global asyncio loop thread. The GTK side
(client.py) must hop to the GTK thread before touching widgets.
"""

import asyncio
import re
import typing
from typing import Any, Dict, List, Optional

from dbus_fast import BusType, DBusError, Message, PropertyAccess, Variant
from dbus_fast import RequestNameReply
from dbus_fast.aio import MessageBus
from dbus_fast.constants import MessageType
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

from src.plugins.core._base import BasePlugin
from src.shared.concurrency_helper import ConcurrencyHelper

WATCHER_BUS_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_OBJECT_PATH = "/StatusNotifierWatcher"
DEFAULT_ITEM_PATH = "/StatusNotifierItem"

_NAME_OWNER_CHANGED_RULE = (
    "type='signal',sender='org.freedesktop.DBus',"
    "interface='org.freedesktop.DBus',member='NameOwnerChanged'"
)


class StatusNotifierWatcher(ServiceInterface):
    """
    The central service that listens for applications registering tray icons.
    """

    def __init__(self, service: str, panel_instance):
        super().__init__(service)
        self.obj = panel_instance
        self.logger = self.obj.logger
        self.bus: Optional[MessageBus] = None
        self.concurrency_helper = ConcurrencyHelper(panel_instance)
        # item_id ("<bus name><object path>") -> StatusNotifierItem
        self.items: Dict[str, "StatusNotifierItem"] = {}
        # object path -> bus name of the last RegisterStatusNotifierItem caller
        self._register_senders: Dict[str, str] = {}
        self._registering: typing.Set[str] = set()

    # ------------------------------------------------------------------ run
    def run_server_in_background(self, panel_instance=None):
        """Connect to the session bus and export this watcher."""
        self.concurrency_helper.run_in_async_task(self._run())

    async def _run(self):
        try:
            self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
            self.bus.add_message_handler(self.handle_message)
            # NameOwnerChanged is only delivered to connections that asked
            # for it. Without this match rule vanished apps were never noticed
            # and their icons stayed in the tray forever.
            await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[_NAME_OWNER_CHANGED_RULE],
                )
            )
            self.bus.export(WATCHER_OBJECT_PATH, self)
            reply = await self.bus.request_name(WATCHER_BUS_NAME)
            if reply != RequestNameReply.PRIMARY_OWNER:
                self.logger.warning(
                    f"{WATCHER_BUS_NAME} is owned by another tray host "
                    f"(reply={reply!r}); apps will register with it instead."
                )
                return
            self.logger.info("StatusNotifierWatcher is running.")
            self.StatusNotifierHostRegistered()
        except Exception as e:
            self.logger.error(f"Failed to start StatusNotifierWatcher: {e}")

    # ------------------------------------------------------- bus messages
    def handle_message(self, message):
        """
        Called for every message the connection sees. We only care about
        * RegisterStatusNotifierItem(path) calls - to learn who the caller is
          (apps that register by object path give no bus name), and
        * NameOwnerChanged - to drop items whose app went away.
        """
        try:
            if (
                message.message_type == MessageType.METHOD_CALL
                and message.member == "RegisterStatusNotifierItem"
                and message.sender
                and message.body
                and isinstance(message.body[0], str)
                and message.body[0].startswith("/")
            ):
                self._register_senders[message.body[0]] = message.sender
            elif (
                message.message_type == MessageType.SIGNAL
                and message.member == "NameOwnerChanged"
                and message.interface == "org.freedesktop.DBus"
            ):
                name, _old_owner, new_owner = message.body
                if not new_owner:
                    self.handle_name_vanished(name)
        except Exception as e:
            self.logger.error(f"Error handling DBus message: {e}")

    def handle_name_vanished(self, bus_name: str):
        gone = [i for i in self.items.values() if i.service_name == bus_name]
        for item in gone:
            self.unregister_item(item.item_id)

    # -------------------------------------------------------- registration
    @staticmethod
    def parse_registration(
        service_or_path: str, sender_for_path: Optional[str]
    ) -> typing.Tuple[Optional[str], str]:
        """
        Spec: the argument is a bus name, or (libappindicator) an object path
        whose owner is the caller. Real world also sends "<name>/<path>".
        Returns (bus_name, object_path); bus_name None if it cannot be found.
        """
        if service_or_path.startswith("/"):
            return sender_for_path, service_or_path
        if "/" in service_or_path:
            name, path = service_or_path.split("/", 1)
            return name, "/" + path
        return service_or_path, DEFAULT_ITEM_PATH

    @method()
    async def RegisterStatusNotifierItem(self, service_or_path: "s"):  # pyright: ignore
        if not service_or_path or service_or_path == "org.freedesktop.DBus":
            self.logger.warning(f"Ignoring invalid registration: {service_or_path!r}")
            return
        service_name, object_path = self.parse_registration(
            service_or_path, self._register_senders.pop(service_or_path, None)
        )
        if not service_name:
            self.logger.warning(
                f"Cannot find the caller of path registration {service_or_path}"
            )
            return
        item_id = f"{service_name}{object_path}"
        if item_id in self.items or item_id in self._registering:
            return  # apps often register twice
        self._registering.add(item_id)
        try:
            item = StatusNotifierItem(
                self.bus,  # pyright: ignore
                service_name,
                object_path,
                self.obj,
            )
            if not await item.initialize():
                self.logger.warning(f"Failed to initialize tray item {item_id}")
                return
            self.items[item_id] = item
            self.StatusNotifierItemRegistered(item_id)
            self._emit_items_changed()
            await item.announce()
        except Exception as e:
            self.logger.error(f"Error registering tray item {item_id}: {e}")
        finally:
            self._registering.discard(item_id)

    def unregister_item(self, item_id: str):
        item = self.items.pop(item_id, None)
        if item is None:
            return
        self.logger.info(f"Removing tray icon {item_id}")
        self.StatusNotifierItemUnregistered(item_id)
        self._emit_items_changed()
        item.announce_removed()

    def _emit_items_changed(self):
        try:
            self.emit_properties_changed(
                {"RegisteredStatusNotifierItems": self.RegisteredStatusNotifierItems}
            )
        except Exception as e:
            self.logger.error(f"Could not emit PropertiesChanged: {e}")

    @method()
    def RegisterStatusNotifierHost(self, service: "s"):  # pyright: ignore
        self.logger.info(f"StatusNotifierHost registered: {service}")

    # ------------------------------------------------------------- signals
    @signal()
    def StatusNotifierItemRegistered(self, service: "s") -> "s":  # pyright: ignore
        return service

    @signal()
    def StatusNotifierItemUnregistered(self, service: "s") -> "s":  # pyright: ignore
        return service

    @signal()
    def StatusNotifierHostRegistered(self):  # pyright: ignore
        pass

    # ---------------------------------------------------------- properties
    @dbus_property(access=PropertyAccess.READ)
    def RegisteredStatusNotifierItems(self) -> "as":  # pyright: ignore
        return list(self.items.keys())

    @dbus_property(access=PropertyAccess.READ)
    def IsStatusNotifierHostRegistered(self) -> "b":  # pyright: ignore
        return True

    @dbus_property(access=PropertyAccess.READ)
    def ProtocolVersion(self) -> "i":  # pyright: ignore
        return 0


class StatusNotifierItem(BasePlugin):
    """
    Proxy for one application's tray icon. Properties are fetched leniently:
    only a few are mandatory in practice, many apps omit the rest.
    """

    IFACES: typing.Final[typing.List[str]] = [
        "org.kde.StatusNotifierItem",
        "org.freedesktop.StatusNotifierItem",
    ]
    MAX_ATTEMPTS: typing.Final[int] = 10
    RETRY_DELAY: typing.Final[float] = 0.5
    MENU_PROPS: typing.Final[typing.List[str]] = [
        "label",
        "enabled",
        "visible",
        "icon-name",
        "icon-data",
        "type",
        "toggle-type",
        "toggle-state",
    ]
    # (SNI signal handler name, what changed)
    SIGNALS: typing.Final[typing.List[str]] = [
        "on_new_icon",
        "on_new_attention_icon",
        "on_new_status",
        "on_new_title",
        "on_new_tool_tip",
    ]

    def __init__(
        self,
        bus: MessageBus,
        service_name: str,
        object_path: str,
        panel_instance: typing.Any,
    ):
        super().__init__(panel_instance)
        self.bus: MessageBus = bus
        self.service_name: str = service_name
        self.object_path: str = object_path
        self.item_id: str = f"{service_name}{object_path}"
        self.item: typing.Any = None
        self.proxy_object: typing.Any = None
        # cached properties
        self.icon_name: str = ""
        self.icon_pixmap: typing.Optional[list] = None
        self.attention_icon_name: str = ""
        self.attention_icon_pixmap: typing.Optional[list] = None
        self.icon_theme_path: str = ""
        self.status: str = "Active"
        self.title: str = ""
        self.tooltip: typing.Tuple[str, str] = ("", "")
        self.item_is_menu: bool = False
        self.menu_path: str = ""
        # dbusmenu (created on first use, subscribed once)
        self._dbusmenu: typing.Any = None
        self.on_layout_changed: typing.Optional[typing.Callable[[], None]] = None
        self._removed = False

    # ------------------------------------------------------------ messages
    def get_message(self) -> typing.Dict[str, typing.Any]:
        return {
            "event": "tray_icon_name_updated",
            "data": {
                "item_id": self.item_id,
                "service_name": self.service_name,
                "object_path": self.object_path,
                "item": self,
                "icon_name": self.icon_name,
                "icon_pixmap": self.icon_pixmap,
                "attention_icon_name": self.attention_icon_name,
                "attention_icon_pixmap": self.attention_icon_pixmap,
                "icon_theme_path": self.icon_theme_path,
                "status": self.status,
                "title": self.title,
                "tooltip": self.tooltip,
                "item_is_menu": self.item_is_menu,
            },
        }

    async def announce(self):
        """Tell the tray widget about this item (new item or changed icon)."""
        if self._removed:
            return
        try:
            self.ipc_server.handle_msg(self.get_message())
        except Exception as e:
            self.logger.error(f"Failed to broadcast tray update: {e}")

    def announce_removed(self):
        self._removed = True
        try:
            self.ipc_server.handle_msg(
                {
                    "event": "tray_icon_removed",
                    "data": {
                        "item_id": self.item_id,
                        "service_name": self.service_name,
                        "object_path": self.object_path,
                    },
                }
            )
        except Exception as e:
            self.logger.error(f"Failed to broadcast tray removal: {e}")

    # --------------------------------------------------------- properties
    async def _get(self, prop: str, default: Any = None) -> Any:
        """Read optional property ``prop`` (snake_case) from the item."""
        getter = getattr(self.item, f"get_{prop}", None)
        if getter is None:
            return default
        try:
            return await getter()
        except Exception:
            return default

    @staticmethod
    def _strip_markup(text: str) -> str:
        return re.sub(r"<[^>]*>", "", text or "").strip()

    async def refresh_properties(self):
        self.icon_name = await self._get("icon_name", "") or ""
        self.icon_pixmap = await self._get("icon_pixmap") or None
        self.attention_icon_name = await self._get("attention_icon_name", "") or ""
        self.attention_icon_pixmap = await self._get("attention_icon_pixmap") or None
        self.icon_theme_path = await self._get("icon_theme_path", "") or ""
        self.status = await self._get("status", "Active") or "Active"
        self.title = await self._get("title", "") or ""
        self.item_is_menu = bool(await self._get("item_is_menu", False))
        menu_path = await self._get("menu", "") or ""
        self.menu_path = str(menu_path)
        tip = await self._get("tool_tip")
        if tip and len(tip) >= 4:
            self.tooltip = (self._strip_markup(tip[2]), self._strip_markup(tip[3]))
        else:
            self.tooltip = (self.title, "")

    async def _is_service_name_valid(self) -> bool:
        """The name must still have an owner, otherwise retrying is pointless."""
        try:
            reply = await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="NameHasOwner",
                    signature="s",
                    body=[self.service_name],
                )
            )
            return bool(reply.message_type == MessageType.METHOD_RETURN and reply.body[0])
        except Exception as e:
            self.logger.error(f"Service validation failed for {self.service_name}: {e}")
            return False

    async def initialize(self) -> bool:
        """Connect to the item (retrying: apps register before they are ready)."""
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            if not await self._is_service_name_valid():
                self.logger.info(f"{self.service_name} is gone, aborting init.")
                return False
            try:
                introspection = await self.bus.introspect(
                    self.service_name, self.object_path
                )
                self.proxy_object = self.bus.get_proxy_object(
                    self.service_name, self.object_path, introspection=introspection
                )
                self.item = None
                for interface in self.IFACES:
                    try:
                        self.item = self.proxy_object.get_interface(interface)
                        break
                    except Exception:
                        continue
                if self.item is None:
                    raise ValueError("No StatusNotifierItem interface found.")
                await self.refresh_properties()
                self._connect_signals()
                self.logger.info(
                    f"Initialized tray item {self.item_id} on attempt {attempt}"
                )
                return True
            except Exception as e:
                self.logger.warning(
                    f"Init attempt {attempt} failed for {self.item_id}: "
                    f"{e.__class__.__name__}"
                )
            if attempt < self.MAX_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY)
        self.logger.error(f"Giving up on tray item {self.item_id}")
        return False

    def _connect_signals(self):
        """Icons/status/tooltips change at runtime (unread badges, sync state)."""
        for name in self.SIGNALS:
            subscribe = getattr(self.item, name, None)
            if subscribe is not None:
                subscribe(self._on_item_changed)

    def _on_item_changed(self, *_args):
        asyncio.ensure_future(self._refresh_and_announce())

    async def _refresh_and_announce(self):
        if self._removed:
            return
        try:
            await self.refresh_properties()
            await self.announce()
        except Exception as e:
            self.logger.error(f"Could not refresh {self.item_id}: {e}")

    # ------------------------------------------------------------- clicks
    async def _call(self, method_name: str, *args) -> bool:
        func = getattr(self.item, f"call_{method_name}", None)
        if func is None:
            return False
        try:
            await func(*args)
            return True
        except Exception as e:
            self.logger.warning(f"{method_name} failed for {self.item_id}: {e}")
            return False

    async def activate(self, x: int = 0, y: int = 0) -> bool:
        return await self._call("activate", x, y)

    async def secondary_activate(self, x: int = 0, y: int = 0) -> bool:
        return await self._call("secondary_activate", x, y)

    async def context_menu(self, x: int = 0, y: int = 0) -> bool:
        return await self._call("context_menu", x, y)

    async def scroll(self, delta: int, orientation: str = "vertical") -> bool:
        return await self._call("scroll", delta, orientation)

    # ----------------------------------------------------------- dbusmenu
    async def _get_dbusmenu(self):
        if self._dbusmenu is not None:
            return self._dbusmenu
        if not self.menu_path or self.menu_path == "/":
            return None
        introspection = await self.bus.introspect(self.service_name, self.menu_path)
        proxy = self.bus.get_proxy_object(
            self.service_name, self.menu_path, introspection
        )
        iface = proxy.get_interface("com.canonical.dbusmenu")
        # Subscribe exactly once; re-subscribing on every rebuild stacked up
        # handlers and caused rebuild storms.
        iface.on_layout_updated(self._on_layout_updated)
        self._dbusmenu = iface
        return iface

    def _on_layout_updated(self, *_args):
        if self.on_layout_changed:
            self.on_layout_changed()

    async def fetch_menu_layout(self) -> typing.List[dict]:
        """Current menu as a nested list of dicts ([] if the item has none)."""
        iface = await self._get_dbusmenu()
        if iface is None:
            return []
        try:
            await iface.call_about_to_show(0)  # apps fill menus lazily
        except Exception:
            pass
        _revision, layout = await iface.call_get_layout(0, -1, self.MENU_PROPS)
        return self.parse_layout(layout)

    @classmethod
    def parse_layout(cls, layout) -> typing.List[dict]:
        """(ia{sv}av) root -> list of child item dicts."""
        _id, _props, children = layout
        return cls._parse_children(children)

    @classmethod
    def _parse_children(cls, children) -> typing.List[dict]:
        items: typing.List[dict] = []
        for child in children or []:
            value = child.value if isinstance(child, Variant) else child
            item_id, properties, grand_children = value
            props = {
                k: (v.value if isinstance(v, Variant) else v)
                for k, v in properties.items()
            }
            entry = {
                "id": item_id,
                "type": props.get("type", "standard"),
                "label": props.get("label", ""),
                "enabled": props.get("enabled", True),
                "visible": props.get("visible", True),
                "icon_name": props.get("icon-name"),
                "icon_data": props.get("icon-data"),
                "toggle_type": props.get("toggle-type", ""),
                "toggle_state": props.get("toggle-state", -1),
            }
            if grand_children:
                entry["submenu"] = cls._parse_children(grand_children)
            items.append(entry)
        return items

    async def menu_event(self, menu_item_id: int, event: str = "clicked") -> bool:
        iface = await self._get_dbusmenu()
        if iface is None:
            return False
        try:
            await iface.call_event(menu_item_id, event, Variant("s", ""), 0)
            return True
        except DBusError as e:
            self.logger.warning(f"Menu event failed for {self.item_id}: {e}")
            return False
