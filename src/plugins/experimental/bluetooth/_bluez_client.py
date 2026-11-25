"""Async D-Bus client for BlueZ.

All Bluetooth I/O of the plugin lives here; the plugin only calls these
methods and the model module only parses what they return.
"""

import asyncio
from typing import Awaitable, Callable, List, Optional, Tuple

from dbus_fast import DBusError, Message, MessageType, Variant

from ._bluetooth_model import (
    ADAPTER_IFACE,
    DEVICE_IFACE,
    BtAdapter,
    BtDevice,
    friendly_error,
    parse_objects,
)
from ._bluez_agent import AGENT_CAPABILITY, AGENT_PATH, PairingAgent

BUS_NAME = "org.bluez"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
CONNECT_TIMEOUT = 30
PAIR_TIMEOUT = 45
CHANGE_DEBOUNCE = 0.3
_WATCHED_MEMBERS = ("PropertiesChanged", "InterfacesAdded", "InterfacesRemoved")

Result = Tuple[bool, str]


class BluezClient:
    """Talks to ``org.bluez`` over the system bus."""

    def __init__(self, ensure_bus: Callable[[], Awaitable[object]], logger) -> None:
        """
        Args:
            ensure_bus: Coroutine function returning a connected system bus,
                or None when it is unavailable.
            logger: Logger with standard methods.
        """
        self._ensure_bus = ensure_bus
        self.logger = logger
        self._pair_lock = asyncio.Lock()
        self._watching = False
        self._pending = None

    async def _interface(self, bus, path: str, name: str):
        introspection = await bus.introspect(BUS_NAME, path)
        proxy = bus.get_proxy_object(BUS_NAME, path, introspection)
        return proxy.get_interface(name)

    async def _guard(self, label: str, call: Callable[[], Awaitable[None]]) -> Result:
        """Run ``call`` and turn any failure into ``(False, short message)``."""
        try:
            await call()
            return True, ""
        except DBusError as e:
            self.logger.error(f"Bluetooth: {label} failed: {e.type}: {e.text}")
            return False, friendly_error(e.type, e.text)
        except asyncio.TimeoutError:
            self.logger.error(f"Bluetooth: {label} timed out")
            return False, "Timed out"
        except Exception as e:
            self.logger.error(f"Bluetooth: {label} failed: {e}")
            return False, friendly_error("", str(e))

    async def get_state(self) -> Tuple[Optional[BtAdapter], List[BtDevice]]:
        """
        Read the adapter and every known device in a single D-Bus call.
        Returns:
            (adapter, devices); adapter is None when BlueZ or a controller
            is not available.
        """
        bus = await self._ensure_bus()
        if not bus:
            return None, []
        try:
            manager = await self._interface(
                bus, "/", "org.freedesktop.DBus.ObjectManager"
            )
            raw = await manager.call_get_managed_objects()
            plain = {
                path: {
                    iface: {key: value.value for key, value in props.items()}
                    for iface, props in ifaces.items()
                }
                for path, ifaces in raw.items()
            }
            return parse_objects(plain)
        except Exception as e:
            self.logger.error(f"Bluetooth: could not read BlueZ state: {e}")
            return None, []

    async def set_powered(self, adapter_path: str, powered: bool) -> Result:
        """Turn the adapter on or off."""

        async def call() -> None:
            bus = await self._ensure_bus()
            props = await self._interface(bus, adapter_path, PROPS_IFACE)
            await props.call_set(ADAPTER_IFACE, "Powered", Variant("b", powered))

        return await self._guard("set powered", call)

    async def start_discovery(self, adapter_path: str) -> Result:
        """Start scanning. Already scanning counts as success."""

        async def call() -> None:
            bus = await self._ensure_bus()
            adapter = await self._interface(bus, adapter_path, ADAPTER_IFACE)
            try:
                await adapter.call_start_discovery()
            except DBusError as e:
                if e.type != "org.bluez.Error.InProgress":
                    raise

        return await self._guard("start discovery", call)

    async def stop_discovery(self, adapter_path: str) -> Result:
        """Stop scanning. Not scanning counts as success."""

        async def call() -> None:
            bus = await self._ensure_bus()
            adapter = await self._interface(bus, adapter_path, ADAPTER_IFACE)
            try:
                await adapter.call_stop_discovery()
            except DBusError as e:
                if e.type not in (
                    "org.bluez.Error.Failed",
                    "org.bluez.Error.NotReady",
                ):
                    raise

        return await self._guard("stop discovery", call)

    async def connect(self, device_path: str) -> Result:
        """Connect a paired device."""

        async def call() -> None:
            bus = await self._ensure_bus()
            device = await self._interface(bus, device_path, DEVICE_IFACE)
            try:
                await asyncio.wait_for(device.call_connect(), CONNECT_TIMEOUT)
            except DBusError as e:
                if e.type != "org.bluez.Error.AlreadyConnected":
                    raise

        return await self._guard("connect", call)

    async def disconnect(self, device_path: str) -> Result:
        """Disconnect a device."""

        async def call() -> None:
            bus = await self._ensure_bus()
            device = await self._interface(bus, device_path, DEVICE_IFACE)
            await device.call_disconnect()

        return await self._guard("disconnect", call)

    async def pair(self, device_path: str) -> Result:
        """
        Pair with a device and mark it trusted so it reconnects by itself.
        A short-lived "Just Works" agent is registered for the duration.
        """

        async def call() -> None:
            bus = await self._ensure_bus()
            async with self._pair_lock:
                agent = PairingAgent()
                bus.export(AGENT_PATH, agent)
                manager = await self._interface(bus, "/org/bluez", "org.bluez.AgentManager1")
                try:
                    try:
                        await manager.call_register_agent(AGENT_PATH, AGENT_CAPABILITY)
                    except DBusError as e:
                        if e.type != "org.bluez.Error.AlreadyExists":
                            raise
                    device = await self._interface(bus, device_path, DEVICE_IFACE)
                    try:
                        await asyncio.wait_for(device.call_pair(), PAIR_TIMEOUT)
                    except DBusError as e:
                        if e.type != "org.bluez.Error.AlreadyExists":
                            raise
                    props = await self._interface(bus, device_path, PROPS_IFACE)
                    await props.call_set(DEVICE_IFACE, "Trusted", Variant("b", True))
                finally:
                    try:
                        await manager.call_unregister_agent(AGENT_PATH)
                    except Exception:
                        pass
                    bus.unexport(AGENT_PATH)

        return await self._guard("pair", call)

    async def remove(self, adapter_path: str, device_path: str) -> Result:
        """Forget a device: unpair it and delete its stored data."""

        async def call() -> None:
            bus = await self._ensure_bus()
            adapter = await self._interface(bus, adapter_path, ADAPTER_IFACE)
            await adapter.call_remove_device(device_path)

        return await self._guard("remove device", call)

    async def watch(self, on_change: Callable[[], None]) -> None:
        """
        Call ``on_change`` (debounced) whenever BlueZ reports a change:
        a device appearing or disappearing, connecting, a battery update,
        scanning starting or stopping. Safe to call more than once.
        """
        if self._watching:
            return
        bus = await self._ensure_bus()
        if not bus:
            return
        loop = asyncio.get_running_loop()

        def fire() -> None:
            self._pending = None
            on_change()

        def handler(message) -> None:
            if message.message_type != MessageType.SIGNAL:
                return
            if message.member not in _WATCHED_MEMBERS:
                return
            if message.member == "PropertiesChanged":
                relevant = (message.path or "").startswith("/org/bluez")
            else:
                relevant = bool(message.body) and str(message.body[0]).startswith(
                    "/org/bluez"
                )
            if relevant and self._pending is None:
                self._pending = loop.call_later(CHANGE_DEBOUNCE, fire)

        try:
            bus.add_message_handler(handler)
            await bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=["type='signal',sender='org.bluez'"],
                )
            )
            self._watching = True
        except Exception as e:
            self.logger.error(f"Bluetooth: could not watch BlueZ signals: {e}")
