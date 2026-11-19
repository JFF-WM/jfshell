def get_plugin_metadata(_):
    about = (
        "A plugin that provides a dashboard for managing Bluetooth devices.",
        "It displays a list of paired devices, indicates their connection status",
        "and allows the user to connect or disconnect them with a single click.",
        "Shown as a page of the clock popover, with its own icon in the sidebar.",
    )

    return {
        "id": "org.jfshell.plugin.bluetooth",
        "name": "Bluetooth Manager",
        "version": "1.0.0",
        "enabled": True,
        "deps": ["css_generator", "calendar"],
        "description": about,
    }


def get_plugin_class():
    import re
    import pulsectl
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from src.plugins.core._base import BasePlugin

    PAGE_ID = "bluetooth"

    class Bluetooth(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            """Hook called by BasePlugin after successful initialization."""
            self.calendar = None
            self.page_root = None
            self.page_visible = False
            self.bluetooth_buttons = {}
            self.main_icon = self.get_plugin_setting(
                "main_icon", "bluetooth-active-symbolic"
            )
            self.fallback_main_icons = self.get_plugin_setting(
                ["fallback_main_icons"],
                ["org.gnome.Settings-bluetooth-symbolic", "bluetooth"],
            )
            self.connect_devices = self.get_plugin_setting_add_hint(
                ["auto_connect"],
                ["B4:B7:42:F7:9B:AD"],
                (
                    "A list of **Bluetooth MAC addresses** (e.g., "
                    "['00:1A:7D:XX:XX:XX']) for devices JFShell should "
                    "automatically attempt to connect to when it starts."
                ),
            )
            self.bus = None
            self._bus_lock = self.asyncio.Lock()
            self.init_page()
            self.run_in_async_task(self._init_dbus_and_auto_connect())
            self.plugins["css_generator"].install_css("bluetooth.css")

        def init_page(self) -> None:
            """Build the page and add it to the clock popover's sidebar."""
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Bluetooth page."
                )
                return
            self.calendar = self.plugins["calendar"]
            self.page_root = self.gtk.Box.new(self.gtk.Orientation.VERTICAL, 10)
            for side in ["top", "bottom", "start", "end"]:
                getattr(self.page_root, f"set_margin_{side}")(10)
            actual_icon = self.gtk_helper.icon_exist(
                self.main_icon, self.fallback_main_icons
            )
            self.calendar.add_page(
                PAGE_ID,
                [actual_icon, "bluetooth-active-symbolic"],
                "Bluetooth",
                self.page_root,
                on_show=self.on_page_show,
                on_hide=self.on_page_hide,
            )

        def on_page_show(self) -> None:
            """The page became visible: show a loading state and fetch the devices."""
            self.page_visible = True
            self._set_page_children([self.gtk.Label(label="Loading...")])
            self.run_in_async_task(self._fetch_and_update_bluetooth_info())

        def on_page_hide(self) -> None:
            """The page is no longer visible."""
            self.page_visible = False

        def on_disable(self) -> None:
            """Take the page out of the clock popover."""
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

        def _set_page_children(self, children) -> None:
            """Replace everything in the page with the given widgets."""
            while child := self.page_root.get_first_child():
                self.page_root.remove(child)
            for widget in children:
                self.page_root.append(widget)

        async def _ensure_bus(self):
            async with self._bus_lock:
                if self.bus is None:
                    try:
                        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
                    except Exception as e:
                        self.logger.error(f"Failed to connect to System Bus: {e}")
                return self.bus

        async def _init_dbus_and_auto_connect(self):
            if await self._ensure_bus():
                await self._auto_connect_devices()

        def _extract_mac_from_string(self, entry_string):
            """
            Extracts a MAC address (e.g., B4:B7:42:F7:9B:AD) from a string.
            Handles PA sink names (B4_B7_42_F7_9B_AD) and standard MAC format.
            """
            mac_pattern = r"([0-9A-F]{2}[_:]?){5}[0-9A-F]{2}"
            match = re.search(mac_pattern, entry_string, re.IGNORECASE)
            if match:
                mac = match.group(0).replace("_", ":").upper()
                if mac.count(":") == 5:
                    return mac
            return None

        async def _auto_connect_devices(self):
            """Reads config and attempts to connect specified Bluetooth devices."""
            if not self.connect_devices:
                return

            known_devices = await self._get_devices()
            macs_to_connect = set()
            for entry in self.connect_devices:
                mac = self._extract_mac_from_string(entry)
                if mac:
                    macs_to_connect.add(mac)
                    continue
                for device in known_devices:
                    if device.get("name") == entry:
                        macs_to_connect.add(device["mac"])
                        break

            for mac in macs_to_connect:
                info = await self._get_device_info(mac)
                if info and not info.get("Connected"):
                    await self._connect_device_and_set_sink(mac, info)

        async def _get_devices(self):
            bus = await self._ensure_bus()
            if not bus:
                return []
            try:
                introspection = await bus.introspect("org.bluez", "/")
                proxy = bus.get_proxy_object("org.bluez", "/", introspection)
                manager = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
                objects = await manager.call_get_managed_objects()

                device_list = []
                for path, interfaces in objects.items():
                    if "org.bluez.Device1" in interfaces:
                        props = interfaces["org.bluez.Device1"]
                        mac = props["Address"].value
                        name = (
                            props.get("Name", {}).value
                            or props.get("Alias", {}).value
                            or "Unknown"
                        )
                        device_list.append({"mac": mac, "name": name, "path": path})
                return device_list
            except Exception as e:
                self.logger.exception(f"Error getting devices: {e}")
                return []

        async def _get_device_info(self, mac_address):
            bus = await self._ensure_bus()
            if not bus:
                return None
            try:
                obj_path = f"/org/bluez/hci0/dev_{mac_address.replace(':', '_')}"
                introspection = await bus.introspect("org.bluez", obj_path)
                proxy = bus.get_proxy_object("org.bluez", obj_path, introspection)
                properties = proxy.get_interface("org.freedesktop.DBus.Properties")
                all_props = await properties.call_get_all("org.bluez.Device1")

                info = {k: v.value for k, v in all_props.items()}
                info["mac"] = mac_address
                info["path"] = obj_path
                return info
            except Exception:
                return None

        async def _get_pa_sink_for_device(self, mac_address):
            """Retrieves the PulseAudio sink Info object using pulsectl."""
            mac_upper = mac_address.upper().replace(":", "_")

            def _sync_get_sink():
                with pulsectl.Pulse("JFShell Bluetooth") as pulse:
                    sinks = pulse.sink_list()
                    for sink in sinks:
                        if sink.name.startswith("bluez_") and mac_upper in sink.name:
                            return sink
                    return None

            try:
                return await self.asyncio.to_thread(_sync_get_sink)
            except Exception as e:
                self.logger.exception(f"PulseAudio error for {mac_address}: {e}")
                return None

        async def _set_default_sink(self, sink_info, device_name):
            """Sets the default PulseAudio sink using pulsectl."""
            if not sink_info:
                return

            def _sync_set_default(sink):
                with pulsectl.Pulse("JFShell Bluetooth") as pulse:
                    pulse.sink_default_set(sink)

            try:
                await self.asyncio.to_thread(_sync_set_default, sink_info)
                self.notifier.notify_send(
                    "Bluetooth Audio",
                    f"Default audio set to {device_name}",
                    "audio-volume-high-symbolic",
                )
            except Exception as e:
                self.logger.exception(f"Failed to set default sink: {e}")

        async def _fetch_and_update_bluetooth_info(self):
            devices_list = await self._get_devices()
            device_details = []
            for device in devices_list:
                info = await self._get_device_info(device["mac"])
                if info:
                    device_details.append(info)
            self.schedule_in_gtk_thread(self._update_popover_buttons, device_details)

        def _update_popover_buttons(self, device_details):
            if not self.page_visible:
                return False
            self._set_page_children([])

            self.bluetooth_buttons.clear()
            if not device_details:
                self.page_root.append(
                    self.gtk.Label(label="No Bluetooth devices found.")
                )
            else:
                for device in device_details:
                    btn = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, 6)
                    btn.add_css_class("bluetooth-dashboard-buttons")
                    self.add_cursor_effect(btn)

                    name = device.get("Name", device["mac"])
                    btn.append(self.gtk.Label(label=f" {name}"))

                    spacer = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, 0)
                    spacer.set_hexpand(True)
                    btn.append(spacer)

                    icon = self.gtk.Image.new_from_icon_name(
                        device.get("Icon", "audio-card")
                    )
                    icon.set_pixel_size(24)
                    btn.append(icon)

                    mac = device["mac"]
                    gesture = self.gtk.GestureClick.new()
                    gesture.connect(
                        "released",
                        lambda *_, m=mac: self.run_in_async_task(
                            self._handle_bluetooth_click(m)
                        ),
                    )
                    btn.add_controller(gesture)

                    if device.get("Connected"):
                        btn.add_css_class("bluetooth-dashboard-buttons-connected")

                    self.bluetooth_buttons[mac] = btn
                    self.page_root.append(btn)
            return False

        async def _connect_device_and_set_sink(self, device_id, device_info):
            """Handles connection and PulseAudio sink setup."""
            device_name = device_info.get("Name", device_id)
            icon = device_info.get("Icon", "bluetooth")
            is_audio = any(
                s in icon.lower() for s in ["audio", "headset", "speaker", "card"]
            )

            await self.connect_bluetooth_device(device_id)

            if is_audio:
                pa_sink = None
                for _ in range(10):
                    pa_sink = await self._get_pa_sink_for_device(device_id)
                    if pa_sink:
                        break
                    await self.asyncio.sleep(0.5)
                if pa_sink:
                    await self._set_default_sink(pa_sink, device_name)

        async def _handle_bluetooth_click(self, device_id):
            info = await self._get_device_info(device_id)
            if not info:
                return

            name = info.get("Name", device_id)
            icon = info.get("Icon", "bluetooth")

            if info.get("Connected"):
                self.notifier.notify_send("Bluetooth", f"Disconnecting: {name}", icon)
                await self.disconnect_bluetooth_device(device_id)
            else:
                self.notifier.notify_send("Bluetooth", f"Connecting: {name}", icon)
                await self._connect_device_and_set_sink(device_id, info)

            if self.page_visible:
                await self._update_single_button_state(device_id)

        async def _update_single_button_state(self, device_id):
            if device_id in self.bluetooth_buttons:
                info = await self._get_device_info(device_id)
                if not info:
                    return
                btn = self.bluetooth_buttons[device_id]
                conn = info.get("Connected")
                self.schedule_in_gtk_thread(
                    lambda: btn.add_css_class("bluetooth-dashboard-buttons-connected")
                    if conn
                    else self.safe_remove_css_class(
                        btn, "bluetooth-dashboard-buttons-connected"
                    )
                )

        async def disconnect_bluetooth_device(self, device_id):
            bus = await self._ensure_bus()
            if not bus:
                return
            try:
                obj_path = f"/org/bluez/hci0/dev_{device_id.replace(':', '_')}"
                introspection = await bus.introspect("org.bluez", obj_path)
                proxy = bus.get_proxy_object("org.bluez", obj_path, introspection)
                await proxy.get_interface("org.bluez.Device1").call_disconnect()
            except Exception as e:
                self.logger.error(f"D-Bus Disconnect failed: {e}")

        async def connect_bluetooth_device(self, device_id):
            bus = await self._ensure_bus()
            if not bus:
                return
            try:
                obj_path = f"/org/bluez/hci0/dev_{device_id.replace(':', '_')}"
                introspection = await bus.introspect("org.bluez", obj_path)
                proxy = bus.get_proxy_object("org.bluez", obj_path, introspection)
                await proxy.get_interface("org.bluez.Device1").call_connect()
            except Exception as e:
                self.logger.error(f"D-Bus Connect failed: {e}")

    return Bluetooth
