def get_plugin_metadata(_):
    about = (
        "A plugin that monitors and displays system network status (wired and Wi-Fi). "
        "Shown as a page of the clock popover, with its own icon in the sidebar.",
    )
    return {
        "id": "org.jfshell.plugin.network_manager",
        "name": "Network Manager",
        "version": "1.0.0",
        "enabled": True,
        "deps": ["css_generator", "calendar"],
        "description": about,
    }


def get_plugin_class():
    from typing import Dict, List, Optional
    from gi.repository import Pango
    from src.plugins.core._base import BasePlugin
    from ._network_cli_backend import NetworkCLI
    from ._network_parse import (
        ActiveConnection,
        NetworkEntry,
        SavedConnection,
        WifiNetwork,
        details_text,
        group_entries,
        signal_icon,
    )

    ICON_WIFI_CONNECTED = "wifi"
    ICON_WIFI_DISCONNECTED = "network-wireless-disconnected-symbolic"
    ICON_WIFI_EXCELLENT = "network-wireless-signal-excellent-symbolic"
    ICON_WIFI_GOOD = "network-wireless-signal-good-symbolic"
    ICON_WIFI_OK = "network-wireless-signal-ok-symbolic"
    ICON_WIFI_WEAK = "network-wireless-signal-weak-symbolic"
    PAGE_ID = "network"
    TRAILING_SLOT_WIDTH = 28

    class NetworkManager(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.panel = panel_instance

        def on_start(self):
            self.calendar = None
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Network page."
                )
                return
            self.calendar = self.plugins["calendar"]
            self.cli_backend = NetworkCLI(self.panel)
            self.page_root = None
            self.page_visible = False
            self.icon_wired_connected = self.gtk_helper.icon_exist(
                "gnome-dev-network-symbolic",
                [
                    "org.gnome.Settings-network-symbolic",
                    "network-wired-activated-symbolic",
                    "network-wired-symbolic",
                ],
            )
            self.icon_wired_disconnected = self.gtk_helper.icon_exist(
                "network-wired-disconnected-symbolic"
            )
            self.icon_wifi_connected = self.gtk_helper.icon_exist(
                ICON_WIFI_CONNECTED, ["network-wireless-connected-symbolic"]
            )
            self.icon_wifi_disconnected = self.gtk_helper.icon_exist(
                ICON_WIFI_DISCONNECTED, ["network-wireless-disconnected-symbolic"]
            )
            self.icon_lock = self.gtk_helper.icon_exist(
                "changes-prevent-symbolic",
                ["system-lock-screen-symbolic", "object-locked-symbolic"],
            )
            self.icon_trash = self.gtk_helper.icon_exist(
                "user-trash-symbolic", ["edit-delete-symbolic"]
            )
            self.icon_disconnect = self.gtk_helper.icon_exist(
                "network-wireless-offline-symbolic",
                ["network-wireless-disconnected-symbolic", "process-stop-symbolic"],
            )
            self.icon_settings = self.gtk_helper.icon_exist(
                "emblem-system-symbolic",
                ["preferences-system-symbolic", "gnome-control-center-symbolic"],
            )
            self.icon = self.icon_wired_disconnected
            self.network_disconnected = None
            self.notify_was_sent = False
            # Model: everything the page shows is derived from this state.
            self.wifi_enabled = True
            self.wifi_networks: List[WifiNetwork] = []
            self.saved_wifi: List[SavedConnection] = []
            self.active_connection: Optional[ActiveConnection] = None
            self.list_error = ""
            self.scanning_in_progress = False
            self.last_scan_time: float = 0.0
            self.connecting_ssid: Optional[str] = None
            self.prompt_ssid: Optional[str] = None
            self.row_errors: Dict[str, str] = {}
            self.refresh_lock = self.asyncio.Lock()
            self._syncing_switch = False
            self._focus_widget = None
            # View: built once, updated in place.
            self.current_card = None
            self.current_name = None
            self.current_detail = None
            self.disconnect_button = None
            self.wifi_switch = None
            self.refresh_button = None
            self.refresh_stack = None
            self.refresh_spinner = None
            self.content_stack = None
            self.list_box = None
            self.message_icon = None
            self.message_label = None
            self.init_ui()
            self.global_loop.create_task(self.periodic_check_async())
            self.ssids_to_auto_connect = self.get_plugin_setting(
                ["auto_connect_ssids"], ["Wi-Fi-NAME"]
            )
            self.scan_interval = self.get_plugin_setting(["scan_interval"], 300)
            self.add_hint(
                "Settings for the Network Manager plugin",
                "which controls and displays network connection status.",
            )
            self.add_hint(
                "Time in (Seconds) to scan for Wi-Fi networks.", "scan_interval"
            )
            self.global_loop.create_task(self.start_periodic_wifi_scan_async())
            self.global_loop.create_task(
                self._apply_config_autoconnect_settings_async()
            )
            self.plugins["css_generator"].install_css("network-manager.css")

        async def start_periodic_wifi_scan_async(self):
            """Starts a periodic background scan for Wi-Fi networks using self.asyncio."""
            while True:
                try:
                    await self.refresh_async(rescan=True)
                except Exception as e:
                    self.logger.error(f"Periodic Wi-Fi scan failed: {e}")
                await self.asyncio.sleep(self.scan_interval)

        def notify_send_network_disconnected(self):
            if self.network_disconnected and self.notify_was_sent is False:
                default_interface = self.cli_backend.get_default_interface_sync()
                if default_interface and self._is_wireless_interface(default_interface):
                    icon_name = self.icon_wifi_disconnected
                else:
                    icon_name = self.icon_wired_disconnected
                self.notifier.notify_send(
                    "Network Manager", "Network disconnected", icon_name
                )
                self.notify_was_sent = True

        # ------------------------------------------------------------------ #
        # View construction (runs once)
        # ------------------------------------------------------------------ #

        def init_ui(self):
            """
            Initialize the page and add it to the clock popover's sidebar.
            The layout is built once; refreshes update the widgets in place.
            """
            self.page_root = self.gtk.Box(
                orientation=self.gtk.Orientation.VERTICAL, spacing=12
            )
            self.page_root.add_css_class("nm-page")
            self.page_root.append(self._build_current_card())
            self.page_root.append(self._build_wifi_card())
            self.calendar.add_page(
                PAGE_ID,
                [self.icon, "network-wireless-symbolic", "network-wired-symbolic"],
                "Network",
                self.page_root,
                on_show=self.on_page_show,
                on_hide=self.on_page_hide,
            )
            self.global_loop.create_task(self.update_icon_async())

        def _build_current_card(self):
            """The "Current connection" card: name, details and a disconnect button."""
            card = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("nm-card")
            title = self.gtk.Label(label="Current connection")
            title.set_halign(self.gtk.Align.START)
            title.add_css_class("nm-card-title")
            row = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=10)
            info = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=8)
            info.set_hexpand(True)
            info.set_valign(self.gtk.Align.CENTER)
            self.current_name = self.gtk.Label(label="Not connected")
            self.current_name.set_halign(self.gtk.Align.START)
            self.current_name.set_ellipsize(Pango.EllipsizeMode.END)
            self.current_name.add_css_class("nm-current-name")
            self.current_detail = self.gtk.Label(label="")
            self.current_detail.set_halign(self.gtk.Align.START)
            self.current_detail.set_ellipsize(Pango.EllipsizeMode.END)
            self.current_detail.add_css_class("nm-dim")
            info.append(self.current_name)
            info.append(self.current_detail)
            self.disconnect_button = self.gtk.Button.new_from_icon_name(
                self.icon_disconnect
            )
            self.disconnect_button.add_css_class("nm-disconnect")
            self.disconnect_button.set_tooltip_text("Disconnect")
            self.disconnect_button.set_valign(self.gtk.Align.CENTER)
            self.disconnect_button.set_visible(False)
            self.disconnect_button.connect("clicked", self._on_disconnect_clicked)
            self.gtk_helper.add_cursor_effect(self.disconnect_button)
            row.append(info)
            row.append(self.disconnect_button)
            card.append(title)
            card.append(row)
            self.current_card = card
            return card

        def _build_wifi_card(self):
            """The Wi-Fi card: header with controls, then the network list."""
            card = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=10)
            card.add_css_class("nm-card")
            header = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=6
            )
            title = self.gtk.Label(label="Wi-Fi")
            title.set_halign(self.gtk.Align.START)
            title.set_hexpand(True)
            title.add_css_class("nm-card-title")
            settings_button = self.gtk.Button.new_from_icon_name(self.icon_settings)
            settings_button.add_css_class("nm-icon-button")
            settings_button.set_tooltip_text("Network settings")
            settings_button.connect("clicked", self._on_settings_clicked)
            self.gtk_helper.add_cursor_effect(settings_button)
            self.refresh_spinner = self.gtk.Spinner()
            self.refresh_stack = self.gtk.Stack()
            self.refresh_stack.add_named(
                self.gtk.Image.new_from_icon_name("view-refresh-symbolic"), "icon"
            )
            self.refresh_stack.add_named(self.refresh_spinner, "spinner")
            self.refresh_button = self.gtk.Button()
            self.refresh_button.set_child(self.refresh_stack)
            self.refresh_button.add_css_class("nm-icon-button")
            self.refresh_button.set_tooltip_text("Scan for networks")
            self.refresh_button.connect("clicked", self._on_refresh_clicked)
            self.gtk_helper.add_cursor_effect(self.refresh_button)
            self.wifi_switch = self.gtk.Switch()
            self.wifi_switch.set_valign(self.gtk.Align.CENTER)
            self.wifi_switch.set_active(self.wifi_enabled)
            self.wifi_switch.connect("state-set", self._on_wifi_switch)
            header.append(title)
            header.append(settings_button)
            header.append(self.refresh_button)
            header.append(self.wifi_switch)

            self.list_box = self.gtk.Box(
                orientation=self.gtk.Orientation.VERTICAL, spacing=4
            )
            scrolled = self.gtk.ScrolledWindow()
            scrolled.set_policy(
                self.gtk.PolicyType.NEVER, self.gtk.PolicyType.AUTOMATIC
            )
            scrolled.set_min_content_height(180)
            scrolled.set_max_content_height(340)
            scrolled.set_propagate_natural_height(True)
            scrolled.add_css_class("nm-scroll")
            scrolled.set_child(self.list_box)

            message = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=8)
            message.set_valign(self.gtk.Align.CENTER)
            message.add_css_class("nm-message")
            self.message_icon = self.gtk.Image.new_from_icon_name(
                "network-wireless-offline-symbolic"
            )
            self.message_icon.set_pixel_size(40)
            self.message_label = self.gtk.Label(label="")
            self.message_label.add_css_class("nm-dim")
            message.append(self.message_icon)
            message.append(self.message_label)

            self.content_stack = self.gtk.Stack()
            self.content_stack.set_vhomogeneous(False)
            self.content_stack.set_interpolate_size(True)
            self.content_stack.add_named(scrolled, "list")
            self.content_stack.add_named(message, "message")

            card.append(header)
            card.append(self.content_stack)
            return card

        def on_disable(self):
            """Take the page out of the clock popover."""
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

        def _update_sidebar_icon(self) -> bool:
            """Show the current connection icon on the Network sidebar button."""
            page = self.calendar.pages.get(PAGE_ID) if self.calendar else None
            if page:
                page["button"].set_child(self.gtk.Image.new_from_icon_name(self.icon))
            return False

        def _is_wireless_interface(self, interface: str) -> bool:
            """Check if an interface name indicates a wireless device."""
            return interface.startswith(("wlan", "wl"))

        async def update_icon_async(self):
            """Update the icon based on current connection status and type."""
            is_connected = await self.cli_backend.is_internet_connected_async()
            default_interface = await self.cli_backend.get_default_interface_async()
            if default_interface and self._is_wireless_interface(default_interface):
                if is_connected:
                    connected_ssid = (
                        await self.cli_backend.get_connected_wifi_ssid_async()
                    )
                    if connected_ssid:
                        signal = await self.cli_backend._get_wifi_signal_strength_async(
                            connected_ssid
                        )
                        if signal > 80:
                            self.icon = ICON_WIFI_EXCELLENT
                        elif signal > 60:
                            self.icon = ICON_WIFI_GOOD
                        elif signal > 40:
                            self.icon = ICON_WIFI_OK
                        else:
                            self.icon = ICON_WIFI_WEAK
                    else:
                        self.icon = self.icon_wifi_disconnected
                else:
                    self.icon = self.icon_wifi_disconnected
            else:
                self.icon = (
                    self.icon_wired_connected
                    if is_connected
                    else self.icon_wired_disconnected
                )
            self.glib.idle_add(self._update_sidebar_icon)

        async def periodic_check_async(self):
            """Periodically check network status using self.asyncio."""
            while True:
                await self.update_icon_async()
                await self.asyncio.sleep(30)

        def on_page_show(self) -> None:
            """Draw what is known right away, then refresh in the background."""
            self.page_visible = True
            self._render()
            self.run_in_async_task(self.refresh_async(rescan=True))

        def on_page_hide(self) -> None:
            """The page is no longer visible."""
            self.page_visible = False
            self.prompt_ssid = None

        async def is_internet_connected_async(self) -> bool:
            """
            Check if internet is available. UI wrapper to update disconnection state.
            """
            is_connected = await self.cli_backend.is_internet_connected_async()
            if is_connected:
                self.notify_was_sent = False
                self.network_disconnected = False
                return True
            self.network_disconnected = True
            self.notify_send_network_disconnected()
            return False

        # ------------------------------------------------------------------ #
        # State refresh (runs on the background loop)
        # ------------------------------------------------------------------ #

        def _push_render(self) -> None:
            """Ask the GTK thread to redraw from the current state."""
            self.schedule_in_gtk_thread(self._render)

        def _apply_networks(self, ok: bool, networks: List[WifiNetwork]) -> None:
            """Store a scan result; keep the old list when the scan failed."""
            if ok:
                self.wifi_networks = networks
                self.list_error = ""
            else:
                self.list_error = "Could not scan for networks"

        async def refresh_async(self, rescan: bool = False) -> None:
            """
            Reload the radio state, networks, profiles and active connection.
            The cached list is shown first; with ``rescan`` a fresh scan follows.
            """
            async with self.refresh_lock:
                try:
                    self.wifi_enabled = (
                        await self.cli_backend.get_wifi_radio_enabled_async()
                    )
                    if self.wifi_enabled:
                        ok, networks = await self.cli_backend.list_wifi_async(False)
                        self._apply_networks(ok, networks)
                        self.saved_wifi = await self.cli_backend.list_saved_wifi_async()
                        self.active_connection = (
                            await self.cli_backend.get_active_connection_async(
                                self.wifi_networks
                            )
                        )
                        if rescan:
                            self.scanning_in_progress = True
                            self._push_render()
                            ok, networks = await self.cli_backend.list_wifi_async(True)
                            self._apply_networks(ok, networks)
                            self.active_connection = (
                                await self.cli_backend.get_active_connection_async(
                                    self.wifi_networks
                                )
                            )
                    else:
                        self.wifi_networks = []
                        self.saved_wifi = []
                        self.active_connection = (
                            await self.cli_backend.get_active_connection_async([])
                        )
                    self.last_scan_time = self.time.time()
                finally:
                    self.scanning_in_progress = False
                    self._push_render()
            await self.update_icon_async()

        # ------------------------------------------------------------------ #
        # Rendering (runs on the GTK thread)
        # ------------------------------------------------------------------ #

        def _render(self, force: bool = False) -> bool:
            """
            Redraw the page from the current state.
            While a password prompt is open the list is left alone so a
            background refresh cannot wipe what the user is typing.
            """
            self._render_current()
            self._render_header()
            if self.prompt_ssid is None or force:
                self._render_list()
            return False

        def _render_current(self) -> None:
            """Update the "Current connection" card in place."""
            active = self.active_connection
            if active is None:
                self.current_name.set_label("Not connected")
                self.current_name.add_css_class("nm-dim")
                self.current_detail.set_label("")
                self.disconnect_button.set_visible(False)
                self.current_card.set_tooltip_text(None)
                return
            self.current_name.remove_css_class("nm-dim")
            self.current_name.set_label(active.name or active.device)
            parts = [
                active.ip,
                f"{active.signal}%" if active.signal is not None else "",
                active.band,
            ]
            self.current_detail.set_label("  \u2022  ".join(p for p in parts if p))
            self.disconnect_button.set_visible(True)
            self.current_card.set_tooltip_text(details_text(active) or None)

        def _render_header(self) -> None:
            """Sync the Wi-Fi switch and the refresh button with the state."""
            if self.wifi_switch.get_active() != self.wifi_enabled:
                self._syncing_switch = True
                self.wifi_switch.set_active(self.wifi_enabled)
                self._syncing_switch = False
            scanning = self.scanning_in_progress
            self.refresh_stack.set_visible_child_name("spinner" if scanning else "icon")
            self.refresh_spinner.set_spinning(scanning)
            self.refresh_button.set_sensitive(self.wifi_enabled and not scanning)

        def _show_message(self, icon_name: str, text: str) -> None:
            self.message_icon.set_from_icon_name(icon_name)
            self.message_label.set_label(text)
            self.content_stack.set_visible_child_name("message")

        def _render_list(self) -> None:
            """Rebuild the network rows: active, then Saved, then Available."""
            self._focus_widget = None
            while child := self.list_box.get_first_child():
                self.list_box.remove(child)
            if not self.wifi_enabled:
                self._show_message("network-wireless-disabled-symbolic", "Wi-Fi is off")
                return
            active, saved, available = group_entries(
                self.wifi_networks, self.saved_wifi
            )
            if active is None and not saved and not available:
                if self.scanning_in_progress:
                    self._show_message("network-wireless-symbolic", "Scanning\u2026")
                else:
                    self._show_message(
                        "network-wireless-offline-symbolic",
                        self.list_error or "No networks found",
                    )
                return
            if active:
                self.list_box.append(self._build_row(active))
            if saved:
                self.list_box.append(self._build_section_title("Saved"))
                for entry in saved:
                    self.list_box.append(self._build_row(entry))
            if available:
                self.list_box.append(self._build_section_title("Available"))
                for entry in available:
                    self.list_box.append(self._build_row(entry))
            self.content_stack.set_visible_child_name("list")
            if self._focus_widget is not None:
                self._focus_widget.grab_focus()

        def _build_section_title(self, text: str):
            label = self.gtk.Label(label=text)
            label.set_halign(self.gtk.Align.START)
            label.add_css_class("nm-section-title")
            return label

        def _build_row(self, entry: NetworkEntry):
            """One network: a row, plus an optional password prompt and error."""
            wrapper = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL, spacing=4)
            row = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=0)
            row.add_css_class("nm-row")
            if entry.in_use:
                row.add_css_class("nm-row-active")
            if not entry.in_range:
                row.add_css_class("nm-row-dim")

            content = self.gtk.Box(
                orientation=self.gtk.Orientation.HORIZONTAL, spacing=10
            )
            content.add_css_class("nm-row-content")
            icon_name = (
                signal_icon(entry.signal)
                if entry.in_range
                else "network-wireless-offline-symbolic"
            )
            content.append(self.gtk.Image.new_from_icon_name(icon_name))
            name = self.gtk.Label(label=entry.ssid)
            name.set_halign(self.gtk.Align.START)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(26)
            name.add_css_class("nm-ssid")
            if entry.in_use:
                name.add_css_class("nm-ssid-active")
            content.append(name)
            if entry.secured:
                content.append(self.gtk.Image.new_from_icon_name(self.icon_lock))
            percent = self.gtk.Label(
                label=f"{entry.signal}%" if entry.in_range else "\u2014"
            )
            percent.add_css_class("nm-signal")
            content.append(percent)

            if entry.in_use:
                content.set_hexpand(True)
                row.append(content)
            else:
                button = self.gtk.Button()
                button.set_child(content)
                button.set_hexpand(True)
                button.add_css_class("nm-row-button")
                button.set_sensitive(entry.in_range and self.connecting_ssid is None)
                button.connect("clicked", self._on_row_clicked, entry)
                self.gtk_helper.add_cursor_effect(button)
                row.append(button)
            row.append(self._build_trailing(entry))
            wrapper.append(row)

            if self.prompt_ssid == entry.ssid and not entry.in_use:
                wrapper.append(self._build_prompt(entry))
            error = self.row_errors.get(entry.ssid)
            if error:
                label = self.gtk.Label(label=error)
                label.set_halign(self.gtk.Align.START)
                label.set_wrap(True)
                label.add_css_class("nm-row-error")
                wrapper.append(label)
            return wrapper

        def _build_trailing(self, entry: NetworkEntry):
            """Fixed-width slot at the end of a row so every row lines up."""
            slot = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL)
            slot.set_size_request(TRAILING_SLOT_WIDTH, -1)
            slot.set_halign(self.gtk.Align.CENTER)
            slot.set_valign(self.gtk.Align.CENTER)
            slot.add_css_class("nm-row-trailing")
            if self.connecting_ssid == entry.ssid:
                slot.append(self.gtk.Spinner(spinning=True))
            elif entry.in_use:
                slot.append(self.gtk.Image.new_from_icon_name("object-select-symbolic"))
            elif entry.saved_uuid:
                forget = self.gtk.Button.new_from_icon_name(self.icon_trash)
                forget.add_css_class("nm-icon-button")
                forget.set_tooltip_text("Forget network")
                forget.connect("clicked", self._on_forget_clicked, entry)
                self.gtk_helper.add_cursor_effect(forget)
                slot.append(forget)
            return slot

        def _build_prompt(self, entry: NetworkEntry):
            """Inline password entry shown under a secured, unsaved network."""
            box = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL, spacing=8)
            box.add_css_class("nm-prompt")
            password = self.gtk.Entry()
            password.set_visibility(False)
            password.set_hexpand(True)
            password.set_placeholder_text("Password")
            password.set_input_purpose(self.gtk.InputPurpose.PASSWORD)
            password.set_icon_from_icon_name(
                self.gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic"
            )

            def toggle_visibility(widget, _position):
                widget.set_visibility(not widget.get_visibility())

            password.connect("icon-press", toggle_visibility)
            connect_button = self.gtk.Button(label="Connect")
            connect_button.add_css_class("suggested-action")
            connect_button.add_css_class("nm-connect")
            self.gtk_helper.add_cursor_effect(connect_button)

            def submit(_widget):
                self._submit_password(entry, password.get_text())

            password.connect("activate", submit)
            connect_button.connect("clicked", submit)
            box.append(password)
            box.append(connect_button)
            self._focus_widget = password
            return box

        # ------------------------------------------------------------------ #
        # Event handlers (GTK thread)
        # ------------------------------------------------------------------ #

        def _on_row_clicked(self, _button, entry: NetworkEntry) -> None:
            """Connect right away, or open the password prompt for a new network."""
            if self.connecting_ssid is not None or not entry.in_range:
                return
            if entry.saved_uuid or not entry.secured:
                self._start_connect(entry, None)
                return
            self.prompt_ssid = None if self.prompt_ssid == entry.ssid else entry.ssid
            self.row_errors.pop(entry.ssid, None)
            self._render(force=True)

        def _submit_password(self, entry: NetworkEntry, password: str) -> None:
            if password:
                self._start_connect(entry, password)

        def _start_connect(self, entry: NetworkEntry, password: Optional[str]) -> None:
            self.connecting_ssid = entry.ssid
            self.prompt_ssid = None
            self.row_errors.pop(entry.ssid, None)
            self._render(force=True)
            self.run_in_async_task(self._connect_async(entry, password))

        async def _connect_async(self, entry: NetworkEntry, password: Optional[str]):
            """Connect, show any failure on the row, then reload the state."""
            ok, message = await self.cli_backend.connect_wifi_async(
                entry.ssid, password, entry.saved_uuid
            )
            self.connecting_ssid = None
            if not ok:
                self.row_errors[entry.ssid] = message
            await self.refresh_async(rescan=False)

        def _on_forget_clicked(self, _button, entry: NetworkEntry) -> None:
            if entry.saved_uuid:
                self.run_in_async_task(self._forget_async(entry.saved_uuid))

        async def _forget_async(self, uuid: str):
            await self.cli_backend.forget_connection_async(uuid)
            await self.refresh_async(rescan=False)

        def _on_disconnect_clicked(self, _button) -> None:
            if self.active_connection:
                self.run_in_async_task(
                    self._disconnect_async(self.active_connection.device)
                )

        async def _disconnect_async(self, device: str):
            await self.cli_backend.disconnect_device_async(device)
            await self.refresh_async(rescan=False)

        def _on_refresh_clicked(self, _button) -> None:
            self.run_in_async_task(self.refresh_async(rescan=True))

        def _on_wifi_switch(self, _switch, state: bool) -> bool:
            """Turn the radio on or off and show the new state immediately."""
            if self._syncing_switch:
                return False
            self.wifi_enabled = state
            self.scanning_in_progress = state
            self.prompt_ssid = None
            if not state:
                self.wifi_networks = []
            self._render(force=True)
            self.run_in_async_task(self._set_radio_async(state))
            return False

        async def _set_radio_async(self, enabled: bool):
            ok = await self.cli_backend.set_wifi_radio_async(enabled)
            if ok and enabled:
                await self.asyncio.sleep(1.5)
            await self.refresh_async(rescan=enabled)

        def _on_settings_clicked(self, _button) -> None:
            self.run_in_async_task(self.on_config_clicked_async())

        async def on_config_clicked_async(self, widget=None):
            """Launches the Control Center to configure the network settings."""
            try:
                self.logger.info("Opening configuration window for network plugin.")
                env = self.os.environ.copy()
                env["XDG_CURRENT_DESKTOP"] = "GNOME"
                await self.asyncio.create_subprocess_exec(
                    "gnome-control-center", "network", env=env
                )
            except FileNotFoundError:
                self.logger.error("gnome-control-center not found. Please install it.")
            except Exception as e:
                self.logger.error(f"Failed to launch config tool: {e}")

        async def _apply_config_autoconnect_settings_async(self):
            """Applies autoconnect settings from config via the CLI backend."""
            ssids_to_autoconnect: List[str] = self.ssids_to_auto_connect
            await self.cli_backend._apply_config_autoconnect_settings_async(
                ssids_to_autoconnect
            )

    return NetworkManager
