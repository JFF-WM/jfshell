def get_plugin_metadata(_):
    about = (
        "A plugin that provides a dashboard for managing Bluetooth devices.",
        "It displays paired devices with their battery level and connection status,",
        "and allows the user to connect, disconnect, pair new devices, forget old ones",
        "and switch the adapter on or off.",
        "Shown as a page of the clock popover, with its own icon in the sidebar.",
    )

    return {
        "id": "org.jfshell.plugin.bluetooth",
        "name": "Bluetooth Manager",
        "version": "1.1.0",
        "enabled": True,
        "deps": ["css_generator", "calendar"],
        "description": about,
    }


def get_plugin_class():
    import re
    import pulsectl
    from dataclasses import replace
    from typing import Dict, List, Optional, Set
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    from gi.repository import Pango
    from src.plugins.core._base import BasePlugin
    from ._bluetooth_model import (
        BtAdapter,
        BtDevice,
        battery_icon,
        group_devices,
        update_seen,
    )
    from ._bluez_client import BluezClient

    PAGE_ID = "bluetooth"
    TRAILING_SLOT_WIDTH = 28

    class Bluetooth(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)

        def on_start(self):
            """Hook called by BasePlugin after successful initialization."""
            self.calendar = None
            self.page_root = None
            self.page_visible = False
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
            self.scan_on_open = self.get_plugin_setting_add_hint(
                ["scan_on_open"],
                True,
                "Look for new devices every time the Bluetooth page is opened.",
            )
            self.scan_duration = self.get_plugin_setting_add_hint(
                ["scan_duration"],
                20,
                "Seconds a scan runs before it stops by itself.",
            )
            # Model: everything the page shows is derived from this state.
            self.adapter: Optional[BtAdapter] = None
            self.devices: List[BtDevice] = []
            self.seen: Set[str] = set()
            self.busy_path: Optional[str] = None
            self.row_errors: Dict[str, str] = {}
            self.power_error = ""
            self._display_key = None
            self._icon_cache: Dict[str, str] = {}
            self._scan_task = None
            self._syncing_switch = False
            self.refresh_lock = self.asyncio.Lock()
            # View: built once, updated in place.
            self.power_switch = None
            self.scan_button = None
            self.scan_stack = None
            self.scan_spinner = None
            self.content_stack = None
            self.list_box = None
            self.message_icon = None
            self.message_label = None
            self.bus = None
            self._bus_lock = self.asyncio.Lock()
            self.client = BluezClient(self._ensure_bus, self.logger)
            self.icon_trash = self.gtk_helper.icon_exist(
                "user-trash-symbolic", ["edit-delete-symbolic"]
            )
            self.icon_disconnect = self.gtk_helper.icon_exist(
                "network-wireless-offline-symbolic",
                ["process-stop-symbolic", "window-close-symbolic"],
            )
            self.icon_settings = self.gtk_helper.icon_exist(
                "emblem-system-symbolic",
                ["preferences-system-symbolic", "gnome-control-center-symbolic"],
            )
            self.init_page()
            self.run_in_async_task(self._init_dbus_and_auto_connect())
            self.plugins["css_generator"].install_css("bluetooth.css")

        # ------------------------------------------------------------------ #
        # View construction (runs once)
        # ------------------------------------------------------------------ #

        def init_page(self) -> None:
            """Build the page and add it to the clock popover's sidebar."""
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Bluetooth page."
                )
                return
            self.calendar = self.plugins["calendar"]
            self.page_root = self.gtk.Box(
                orientation=self.gtk.Orientation.VERTICAL, spacing=12
            )
            self.page_root.add_css_class("bt-page")
            self.page_root.append(self._build_card())
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

        def _build_card(self):
            """The card: header with controls, then the device list."""
            card = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("bt-card")
            header = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=6
            )
            title = self.gtk.Label(label="Bluetooth")
            title.set_halign(self.gtk.Align.START)
            title.set_hexpand(True)
            title.add_css_class("bt-card-title")
            settings_button = self.gtk.Button.new_from_icon_name(self.icon_settings)
            settings_button.add_css_class("bt-icon-button")
            settings_button.set_tooltip_text("Bluetooth settings")
            settings_button.connect("clicked", self._on_settings_clicked)
            self.gtk_helper.add_cursor_effect(settings_button)
            self.scan_spinner = self.gtk.Spinner()
            self.scan_stack = self.gtk.Stack()
            self.scan_stack.add_named(
                self.gtk.Image.new_from_icon_name("view-refresh-symbolic"), "icon"
            )
            self.scan_stack.add_named(self.scan_spinner, "spinner")
            self.scan_button = self.gtk.Button()
            self.scan_button.set_child(self.scan_stack)
            self.scan_button.add_css_class("bt-icon-button")
            self.scan_button.set_tooltip_text("Scan for devices")
            self.scan_button.connect("clicked", self._on_scan_clicked)
            self.gtk_helper.add_cursor_effect(self.scan_button)
            self.power_switch = self.gtk.Switch()
            self.power_switch.set_valign(self.gtk.Align.CENTER)
            self.power_switch.set_sensitive(False)
            self.power_switch.connect("state-set", self._on_power_switch)
            header.append(title)
            header.append(settings_button)
            header.append(self.scan_button)
            header.append(self.power_switch)

            self.list_box = self.gtk.Box(
                orientation=self.gtk.Orientation.VERTICAL, spacing=4
            )
            scrolled = self.gtk.ScrolledWindow()
            scrolled.set_policy(self.gtk.PolicyType.NEVER, self.gtk.PolicyType.AUTOMATIC)
            scrolled.set_min_content_height(180)
            scrolled.set_max_content_height(340)
            scrolled.set_propagate_natural_height(True)
            scrolled.add_css_class("bt-scroll")
            scrolled.set_child(self.list_box)

            message = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=8)
            message.set_valign(self.gtk.Align.CENTER)
            message.add_css_class("bt-message")
            self.message_icon = self.gtk.Image.new_from_icon_name(
                "bluetooth-disabled-symbolic"
            )
            self.message_icon.set_pixel_size(40)
            self.message_label = self.gtk.Label(label="")
            self.message_label.set_wrap(True)
            self.message_label.add_css_class("bt-dim")
            message.append(self.message_icon)
            message.append(self.message_label)

            self.content_stack = self.gtk.Stack()
            self.content_stack.set_vhomogeneous(False)
            self.content_stack.set_interpolate_size(True)
            self.content_stack.add_named(scrolled, "list")
            self.content_stack.add_named(message, "message")
            self.content_stack.set_visible_child_name("message")
            self._show_message("bluetooth-active-symbolic", "Loading\u2026")

            card.append(header)
            card.append(self.content_stack)
            return card

        def on_page_show(self) -> None:
            """Draw what is known right away, then refresh and look for devices."""
            self.page_visible = True
            self.seen = set()
            self._render()
            self.run_in_async_task(self._on_show_async())

        def on_page_hide(self) -> None:
            """The page is no longer visible: stop scanning and close any error."""
            self.page_visible = False
            self.row_errors.clear()
            self.run_in_async_task(self._stop_scan_async())

        def on_disable(self) -> None:
            """Take the page out of the clock popover."""
            self.run_in_async_task(self._stop_scan_async())
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

        # ------------------------------------------------------------------ #
        # D-Bus plumbing and audio routing
        # ------------------------------------------------------------------ #

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
                await self.client.watch(self._on_bluez_change)
                await self._auto_connect_devices()

        def _on_bluez_change(self) -> None:
            """BlueZ reported a change: reload while the page is open."""
            if self.page_visible:
                self.run_in_async_task(self._refresh_async())

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

            adapter, known_devices = await self.client.get_state()
            if adapter is None or not adapter.powered:
                return
            macs_to_connect = set()
            for entry in self.connect_devices:
                mac = self._extract_mac_from_string(entry)
                if mac:
                    macs_to_connect.add(mac)
                    continue
                for device in known_devices:
                    if device.name == entry:
                        macs_to_connect.add(device.mac)
                        break

            for device in known_devices:
                if device.mac in macs_to_connect and device.paired and not device.connected:
                    await self._connect_with_audio(device)

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

        async def _set_sink_when_ready(self, mac: str, name: str) -> None:
            """Wait for the device's audio sink to appear, then make it the default."""
            pa_sink = None
            for _ in range(10):
                pa_sink = await self._get_pa_sink_for_device(mac)
                if pa_sink:
                    break
                await self.asyncio.sleep(0.5)
            if pa_sink:
                await self._set_default_sink(pa_sink, name)

        async def _connect_with_audio(self, device: BtDevice):
            """Handles connection and PulseAudio sink setup."""
            ok, message = await self.client.connect(device.path)
            if ok and device.is_audio:
                await self._set_sink_when_ready(device.mac, device.name)
            return ok, message

        # ------------------------------------------------------------------ #
        # State refresh (runs on the background loop)
        # ------------------------------------------------------------------ #

        def _push_render(self) -> None:
            """Ask the GTK thread to redraw from the current state."""
            self.schedule_in_gtk_thread(self._render)

        @staticmethod
        def _key_of(adapter, devices):
            """What the page displays; signal strength is left out on purpose."""
            return adapter, tuple(replace(d, rssi=None) for d in devices)

        async def _refresh_async(self) -> None:
            """Reload the adapter and devices; redraw only when something shown changed."""
            async with self.refresh_lock:
                adapter, devices = await self.client.get_state()
                self.adapter = adapter
                self.devices = devices
                discovering = bool(adapter and adapter.discovering)
                self.seen = update_seen(self.seen, devices, discovering)
                key = self._key_of(adapter, devices) + (frozenset(self.seen),)
                if key != self._display_key:
                    self._display_key = key
                    self._push_render()

        async def _on_show_async(self) -> None:
            await self._refresh_async()
            if self.scan_on_open and self.adapter and self.adapter.powered:
                await self._start_scan_async()

        async def _start_scan_async(self) -> None:
            """Scan for new devices; the scan stops by itself after a while."""
            if not self.adapter or not self.adapter.powered:
                return
            ok, message = await self.client.start_discovery(self.adapter.path)
            if not ok:
                self.logger.error(f"Bluetooth scan failed: {message}")
                return
            if self._scan_task:
                self._scan_task.cancel()
            self._scan_task = self.asyncio.create_task(self._auto_stop_scan_async())
            await self._refresh_async()

        async def _auto_stop_scan_async(self) -> None:
            try:
                await self.asyncio.sleep(self.scan_duration)
            except self.asyncio.CancelledError:
                return
            self._scan_task = None
            if self.adapter:
                await self.client.stop_discovery(self.adapter.path)

        async def _stop_scan_async(self) -> None:
            """Stop a scan this plugin started."""
            if self._scan_task:
                self._scan_task.cancel()
                self._scan_task = None
                if self.adapter:
                    await self.client.stop_discovery(self.adapter.path)

        # ------------------------------------------------------------------ #
        # Rendering (runs on the GTK thread)
        # ------------------------------------------------------------------ #

        def _render(self) -> bool:
            """Redraw the page from the current state."""
            self._render_header()
            self._render_list()
            return False

        def _render_header(self) -> None:
            """Sync the power switch and the scan button with the state."""
            adapter = self.adapter
            powered = bool(adapter and adapter.powered)
            if self.power_switch.get_active() != powered:
                self._syncing_switch = True
                self.power_switch.set_active(powered)
                self._syncing_switch = False
            self.power_switch.set_sensitive(adapter is not None)
            scanning = bool(adapter and adapter.discovering)
            self.scan_stack.set_visible_child_name("spinner" if scanning else "icon")
            self.scan_spinner.set_spinning(scanning)
            self.scan_button.set_sensitive(powered)
            self.scan_button.set_tooltip_text(
                "Stop scanning" if scanning else "Scan for devices"
            )

        def _show_message(self, icon_name: str, text: str) -> None:
            self.message_icon.set_from_icon_name(icon_name)
            self.message_label.set_label(text)
            self.content_stack.set_visible_child_name("message")

        def _render_list(self) -> None:
            """Rebuild the rows: Connected, Paired, then Available."""
            while child := self.list_box.get_first_child():
                self.list_box.remove(child)
            adapter = self.adapter
            if adapter is None:
                self._show_message(
                    "bluetooth-disabled-symbolic", "No Bluetooth adapter found"
                )
                return
            if not adapter.powered:
                self._show_message(
                    "bluetooth-disabled-symbolic",
                    self.power_error or "Bluetooth is off",
                )
                return
            connected, paired, available = group_devices(self.devices, self.seen)
            if not (connected or paired or available):
                self._show_message(
                    "bluetooth-active-symbolic",
                    "Searching\u2026" if adapter.discovering else "No devices found",
                )
                return
            for title, group in (
                ("Connected", connected),
                ("Paired", paired),
                ("Available", available),
            ):
                if group:
                    self.list_box.append(self._build_section_title(title))
                    for device in group:
                        self.list_box.append(self._build_row(device))
            self.content_stack.set_visible_child_name("list")

        def _build_section_title(self, text: str):
            label = self.gtk.Label(label=text)
            label.set_halign(self.gtk.Align.START)
            label.add_css_class("bt-section-title")
            return label

        def _device_icon(self, icon: str) -> str:
            """Symbolic icon for a BlueZ icon name, with a generic fallback."""
            if icon not in self._icon_cache:
                self._icon_cache[icon] = self.gtk_helper.icon_exist(
                    f"{icon}-symbolic" if icon else "bluetooth-symbolic",
                    [icon, "bluetooth-symbolic"] if icon else [],
                )
            return self._icon_cache[icon]

        def _build_row(self, device: BtDevice):
            """One device: a row, plus an optional error line."""
            wrapper = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=4)
            row = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=0)
            row.add_css_class("bt-row")
            if device.connected:
                row.add_css_class("bt-row-active")

            content = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=10
            )
            content.add_css_class("bt-row-content")
            content.append(
                self.gtk.Image.new_from_icon_name(self._device_icon(device.icon))
            )
            name = self.gtk.Label(label=device.name)
            name.set_halign(self.gtk.Align.START)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(26)
            name.add_css_class("bt-name")
            if device.connected:
                name.add_css_class("bt-name-active")
            content.append(name)
            if device.battery is not None:
                content.append(
                    self.gtk.Image.new_from_icon_name(battery_icon(device.battery))
                )
                level = self.gtk.Label(label=f"{device.battery}%")
                level.add_css_class("bt-battery")
                content.append(level)

            if device.connected:
                content.set_hexpand(True)
                row.append(content)
            else:
                button = self.gtk.Button()
                button.set_child(content)
                button.set_hexpand(True)
                button.add_css_class("bt-row-button")
                button.set_sensitive(self.busy_path is None)
                button.set_tooltip_text(
                    "Connect" if device.paired else "Pair and connect"
                )
                button.connect("clicked", self._on_row_clicked, device)
                self.gtk_helper.add_cursor_effect(button)
                row.append(button)
            row.append(self._build_trailing(device))
            wrapper.append(row)

            error = self.row_errors.get(device.path)
            if error:
                label = self.gtk.Label(label=error)
                label.set_halign(self.gtk.Align.START)
                label.set_wrap(True)
                label.add_css_class("bt-row-error")
                wrapper.append(label)
            return wrapper

        def _build_trailing(self, device: BtDevice):
            """Fixed-width slot at the end of a row so every row lines up."""
            slot = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL)
            slot.set_size_request(TRAILING_SLOT_WIDTH, -1)
            slot.set_halign(self.gtk.Align.CENTER)
            slot.set_valign(self.gtk.Align.CENTER)
            slot.add_css_class("bt-row-trailing")
            if self.busy_path == device.path:
                slot.append(self.gtk.Spinner(spinning=True))
            elif device.connected:
                button = self.gtk.Button.new_from_icon_name(self.icon_disconnect)
                button.add_css_class("bt-icon-button")
                button.set_tooltip_text("Disconnect")
                button.connect("clicked", self._on_disconnect_clicked, device)
                self.gtk_helper.add_cursor_effect(button)
                slot.append(button)
            elif device.paired:
                button = self.gtk.Button.new_from_icon_name(self.icon_trash)
                button.add_css_class("bt-icon-button")
                button.set_tooltip_text("Forget device")
                button.set_sensitive(self.busy_path is None)
                button.connect("clicked", self._on_forget_clicked, device)
                self.gtk_helper.add_cursor_effect(button)
                slot.append(button)
            return slot

        # ------------------------------------------------------------------ #
        # Event handlers (GTK thread)
        # ------------------------------------------------------------------ #

        def _begin(self, device: BtDevice) -> bool:
            """Mark a device busy and redraw; False when something else is running."""
            if self.busy_path is not None:
                return False
            self.busy_path = device.path
            self.row_errors.pop(device.path, None)
            self._render()
            return True

        async def _finish(self, device: BtDevice, ok: bool, message: str) -> None:
            """Clear the busy state, show a failure on the row, then reload."""
            self.busy_path = None
            if not ok:
                self.row_errors[device.path] = message
            self._display_key = None
            await self._refresh_async()

        def _on_row_clicked(self, _button, device: BtDevice) -> None:
            """Connect a paired device, or pair and connect a new one."""
            if not self._begin(device):
                return
            icon = device.icon or "bluetooth"
            if device.paired:
                self.notifier.notify_send("Bluetooth", f"Connecting: {device.name}", icon)
                self.run_in_async_task(self._connect_async(device))
            else:
                self.notifier.notify_send("Bluetooth", f"Pairing: {device.name}", icon)
                self.run_in_async_task(self._pair_async(device))

        async def _connect_async(self, device: BtDevice) -> None:
            ok, message = await self._connect_with_audio(device)
            await self._finish(device, ok, message)

        async def _pair_async(self, device: BtDevice) -> None:
            ok, message = await self.client.pair(device.path)
            if ok:
                ok, message = await self._connect_with_audio(device)
            await self._finish(device, ok, message)

        def _on_disconnect_clicked(self, _button, device: BtDevice) -> None:
            if not self._begin(device):
                return
            self.notifier.notify_send(
                "Bluetooth", f"Disconnecting: {device.name}", device.icon or "bluetooth"
            )
            self.run_in_async_task(self._disconnect_async(device))

        async def _disconnect_async(self, device: BtDevice) -> None:
            ok, message = await self.client.disconnect(device.path)
            await self._finish(device, ok, message)

        def _on_forget_clicked(self, _button, device: BtDevice) -> None:
            if self.adapter is None or not self._begin(device):
                return
            self.run_in_async_task(self._forget_async(device, self.adapter.path))

        async def _forget_async(self, device: BtDevice, adapter_path: str) -> None:
            ok, message = await self.client.remove(adapter_path, device.path)
            await self._finish(device, ok, message)

        def _on_scan_clicked(self, _button) -> None:
            if self.adapter and self.adapter.discovering:
                self.run_in_async_task(self._stop_scan_and_refresh_async())
            else:
                self.run_in_async_task(self._start_scan_async())

        async def _stop_scan_and_refresh_async(self) -> None:
            if self._scan_task:
                await self._stop_scan_async()
            elif self.adapter:
                await self.client.stop_discovery(self.adapter.path)
            await self._refresh_async()

        def _on_power_switch(self, _switch, state: bool) -> bool:
            """Switch the adapter on or off and show the new state immediately."""
            if self._syncing_switch or self.adapter is None:
                return False
            self.power_error = ""
            self.adapter = replace(self.adapter, powered=state, discovering=False)
            self._render()
            self.run_in_async_task(self._set_power_async(state))
            return False

        async def _set_power_async(self, powered: bool) -> None:
            if not self.adapter:
                return
            ok, message = await self.client.set_powered(self.adapter.path, powered)
            self.power_error = "" if ok else message
            self._display_key = None
            await self._refresh_async()
            if ok and powered and self.scan_on_open and self.page_visible:
                await self._start_scan_async()

        def _on_settings_clicked(self, _button) -> None:
            self.run_in_async_task(self._open_settings_async())

        async def _open_settings_async(self) -> None:
            """Launches the Control Center on its Bluetooth page."""
            try:
                env = self.os.environ.copy()
                env["XDG_CURRENT_DESKTOP"] = "GNOME"
                await self.asyncio.create_subprocess_exec(
                    "gnome-control-center", "bluetooth", env=env
                )
            except FileNotFoundError:
                self.logger.error("gnome-control-center not found. Please install it.")
            except Exception as e:
                self.logger.error(f"Failed to launch config tool: {e}")

    return Bluetooth
