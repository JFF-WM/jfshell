def get_plugin_metadata(_):
    """Retrieves the metadata for the Home plugin.

    Args:
        _: The panel instance (unused, the plugin lives in the clock popover).

    Returns:
        dict: A dictionary containing plugin identification, requirements, and metadata.
    """
    return {
        "id": "org.jfshell.plugin.home",
        "name": "Home",
        "version": "1.0.0",
        "enabled": True,
        "container": "background",
        "deps": ["css_generator", "calendar"],
        "description": (
            "Home page of the clock popover: user card, now playing, clock and "
            "weather location, quick toggles (Wi-Fi, Bluetooth, caffeine, night "
            "light, Do Not Disturb, power profile) and buttons for the panel "
            "settings, the power menu and closing the popover."
        ),
    }


def get_plugin_class():
    """Returns the Home plugin class definition.

    Returns:
        type: The HomePlugin class inheriting from BasePlugin.
    """
    import ctypes
    import shlex
    import shutil
    import signal
    from importlib import metadata

    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    from src.plugins.core._base import BasePlugin
    from src.shared.dbus_helpers import DbusHelpers
    from ._home_info import (
        PROFILE_ICONS,
        location_text,
        media_texts,
        next_profile,
        parse_active_ssid,
        parse_bluetooth_powered,
        parse_profile_list,
        parse_radio_enabled,
        parse_uptime,
        pick_player,
        profile_label,
        uptime_line,
    )

    PAGE_ID = "home"
    WEATHER_ID = "org.jfshell.plugin.weather"
    NOW_PLAYING_PAGE = "now_playing"
    WEATHER_PAGE = "weather"
    UPTIME_FILE = "/proc/uptime"
    UPTIME_EVERY_TICKS = 30
    TILE_ORDER = ("wifi", "bluetooth", "caffeine", "night_light", "dnd", "profile")
    PR_SET_PDEATHSIG = 1

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        libc = None

    def die_with_parent() -> None:
        """Child pre-exec hook: the process gets SIGTERM when the panel dies,
        so a caffeine or night light helper never outlives it."""
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)  # pyright: ignore

    class HomePlugin(BasePlugin):
        """Home page of the clock popover.

        Threading: commands run on the shared thread pool and D-Bus calls on the
        asyncio loop; every widget is changed on the GTK thread through
        schedule_in_gtk_thread. The state is only polled while the page is
        visible.
        """

        def __init__(self, panel_instance):
            """Initializes the Home plugin.

            Args:
                panel_instance: The main panel instance this plugin is attached to.
            """
            super().__init__(panel_instance)

        # ---------------------------------------------------------- startup
        def on_start(self):
            """Builds the page and adds it to the clock popover."""
            self.calendar = None
            self._procs = {"caffeine": None, "night_light": None}
            self._dbus = None
            self._timers = {"clock": 0, "state": 0}
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Home page."
                )
                return
            self.calendar = self.plugins["calendar"]
            self._read_settings()
            self.state = {}
            self.tiles = {}
            self._tile_visible = {}
            self._ticks = 0
            self._state_busy = False
            self._media_busy = False
            self._media_active = False
            self._build_page()
            added = self.calendar.add_page(
                PAGE_ID,
                [self.main_icon, "user-home-symbolic", "go-home-symbolic"],
                "Home",
                self.page_root,
                on_show=self._on_page_show,
                on_hide=self._on_page_hide,
            )
            if added:
                button = self.calendar.pages[PAGE_ID]["button"]
                button.add_css_class("home-trigger")
                # The Home button leads the sidebar, the other pages follow it.
                self.calendar.sidebar.reorder_child_after(button, None)
            self.plugins["css_generator"].install_css("home.css")

        def _read_settings(self):
            """Reads the plugin settings, registering their hints and defaults."""
            setting = self.get_plugin_setting_add_hint
            self.main_icon = setting(
                ["main_icon"],
                "user-home-symbolic",
                "Icon of the Home button in the clock popover's sidebar.",
            )
            self.card_background = setting(
                ["card_background"],
                "",
                "Path of an image drawn behind the user card. Empty keeps the theme gradient.",
            )
            self.clock_24h = setting(
                ["clock_24h"], True, "Show the time in 24-hour format."
            )
            self.refresh_seconds = max(
                2,
                int(
                    setting(
                        ["timing", "refresh_seconds"],
                        5,
                        "Seconds between state checks while the Home page is open.",
                    )
                ),
            )
            self.commands = {
                "lock": setting(
                    ["commands", "lock_screen"],
                    "swaylock",
                    "Command of the Lock entry in the power menu.",
                ),
                "logout": setting(
                    ["commands", "logout"],
                    "wayland-logout",
                    "Command of the Log Out entry in the power menu.",
                ),
                "suspend": setting(
                    ["commands", "suspend"],
                    "systemctl suspend",
                    "Command of the Suspend entry in the power menu.",
                ),
                "reboot": setting(
                    ["commands", "reboot"],
                    "reboot",
                    "Command of the Reboot entry in the power menu.",
                ),
                "shutdown": setting(
                    ["commands", "shutdown"],
                    "shutdown -h now",
                    "Command of the Shut Down entry in the power menu.",
                ),
                "caffeine": setting(
                    ["commands", "caffeine"],
                    "systemd-inhibit --what=idle:sleep --who=JFShell --why=Caffeine --mode=block sleep infinity",
                    "Long running command that keeps the system awake while Caffeine is on. It is stopped when Caffeine is turned off.",
                ),
                "night_light": setting(
                    ["commands", "night_light"],
                    "gammastep -O 4500",
                    "Long running command that tints the screen while Night Light is on. It is stopped when Night Light is turned off.",
                ),
            }

        # ------------------------------------------------------------- build
        def _build_page(self):
            """Constructs the page: header, user card, media, clock and tiles."""
            Gtk = self.gtk
            self.page_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.page_root.add_css_class("home-page")
            self.page_root.set_size_request(480, -1)
            for side in ("top", "bottom", "start", "end"):
                getattr(self.page_root, f"set_margin_{side}")(12)
            self.page_root.append(self._build_header())
            self.page_root.append(self._build_user_card())

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            left.set_hexpand(True)
            left.append(self._build_media_card())
            left.append(self._build_clock_card())
            body.append(left)
            body.append(self._build_tiles())
            self.page_root.append(body)

        def _icon_button(self, icons, tooltip, callback):
            """Flat icon button of the header."""
            button = self.gtk.Button.new_from_icon_name(
                self.calendar.resolve_icon(icons)
            )
            button.add_css_class("flat")
            button.add_css_class("home-header-button")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", callback)
            self.add_cursor_effect(button)
            return button

        def _build_header(self):
            """Title with the settings, power and close buttons."""
            Gtk = self.gtk
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header.add_css_class("home-header")
            title = Gtk.Label(label="Home", xalign=0)
            title.add_css_class("home-title")
            title.set_hexpand(True)
            header.append(title)
            header.append(
                self._icon_button(
                    [
                        "emblem-system-symbolic",
                        "preferences-system-symbolic",
                        "settings-configure-symbolic",
                    ],
                    "Panel settings",
                    self._on_settings_clicked,
                )
            )
            header.append(self._build_power_button())
            header.append(
                self._icon_button(
                    ["window-close-symbolic"], "Close", lambda *_: self._close_popover()
                )
            )
            return header

        def _build_power_button(self):
            """Menu button with the lock, log out, suspend, reboot and shut down entries."""
            Gtk = self.gtk
            entries = (
                ("Lock", "lock", ["system-lock-screen-symbolic", "lock-symbolic"]),
                ("Log Out", "logout", ["system-log-out-symbolic"]),
                ("Suspend", "suspend", ["system-suspend-symbolic"]),
                ("Reboot", "reboot", ["system-reboot-symbolic"]),
                ("Shut Down", "shutdown", ["system-shutdown-symbolic"]),
            )
            self.power_popover = Gtk.Popover()
            self.power_popover.add_css_class("home-power-popover")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for label, action, icons in entries:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.append(
                    Gtk.Image.new_from_icon_name(self.calendar.resolve_icon(icons))
                )
                row.append(Gtk.Label(label=label, xalign=0))
                button = Gtk.Button()
                button.set_child(row)
                button.add_css_class("flat")
                button.add_css_class("home-power-entry")
                button.connect("clicked", self._on_power_clicked, action)
                self.add_cursor_effect(button)
                box.append(button)
            self.power_popover.set_child(box)
            menu = Gtk.MenuButton()
            menu.set_icon_name(
                self.calendar.resolve_icon(
                    ["system-shutdown-symbolic", "preferences-system-power-symbolic"]
                )
            )
            menu.set_popover(self.power_popover)
            menu.set_tooltip_text("Power")
            menu.set_has_frame(False)
            menu.add_css_class("home-header-button")
            self.add_cursor_effect(menu)
            return menu

        def _build_user_card(self):
            """Card with avatar, user, host, uptime and versions over an optional image."""
            Gtk = self.gtk
            card = Gtk.Overlay()
            card.add_css_class("home-user-card")
            card.set_size_request(-1, 150)
            card.set_overflow(Gtk.Overflow.HIDDEN)
            picture = Gtk.Picture()
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(True)
            if self.card_background and self.os.path.isfile(
                self.os.path.expanduser(self.card_background)
            ):
                picture.set_filename(self.os.path.expanduser(self.card_background))
            card.set_child(picture)

            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            content.set_valign(Gtk.Align.CENTER)
            content.set_margin_start(20)
            content.set_margin_end(20)
            user = self.glib.get_user_name()
            avatar = self.adw.Avatar.new(72, user, True)
            avatar.add_css_class("home-avatar")
            face = self.pathlib.Path.home() / ".face"
            if face.is_file():
                try:
                    avatar.set_custom_image(
                        self.gdk.Texture.new_from_filename(str(face))
                    )
                except self.glib.Error as e:
                    self.logger.warning(f"Home: could not load {face}: {e}")
            content.append(avatar)

            lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            lines.set_valign(Gtk.Align.CENTER)
            name = Gtk.Label(label=user, xalign=0)
            name.add_css_class("home-user-name")
            address = Gtk.Label(label=f"{user}@{self.glib.get_host_name()}", xalign=0)
            self.uptime_label = Gtk.Label(xalign=0)
            shell = Gtk.Label(label=self._shell_version(), xalign=0)
            compositor = Gtk.Label(label=self._compositor_name(), xalign=0)
            lines.append(name)
            for label in (address, self.uptime_label, shell, compositor):
                label.add_css_class("home-user-line")
                lines.append(label)
            content.append(lines)
            card.add_overlay(content)
            self._refresh_uptime()
            return card

        def _shell_version(self) -> str:
            """JFShell with its version when it is installed as a package."""
            try:
                return f"JFShell {metadata.version('jfshell')}"
            except metadata.PackageNotFoundError:
                return "JFShell"

        def _compositor_name(self) -> str:
            """Name of the running compositor."""
            try:
                return str(self.ipc.get_version() or "jff")
            except Exception:
                return "jff"

        def _build_media_card(self):
            """Card with the player in use, it opens the media page."""
            Gtk = self.gtk
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.set_valign(Gtk.Align.CENTER)
            row.set_margin_start(16)
            row.set_margin_end(16)
            icon = Gtk.Image.new_from_icon_name(
                self.calendar.resolve_icon(
                    ["media-optical-symbolic", "multimedia-player-symbolic"]
                )
            )
            icon.set_pixel_size(56)
            icon.add_css_class("home-media-icon")
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            texts.set_valign(Gtk.Align.CENTER)
            texts.set_hexpand(True)
            self.media_title = Gtk.Label(xalign=0)
            self.media_title.set_ellipsize(self.pango.EllipsizeMode.END)
            self.media_title.set_max_width_chars(22)
            self.media_title.add_css_class("home-media-title")
            self.media_subtitle = Gtk.Label(xalign=0)
            self.media_subtitle.set_ellipsize(self.pango.EllipsizeMode.END)
            self.media_subtitle.set_max_width_chars(22)
            self.media_subtitle.add_css_class("home-media-subtitle")
            texts.append(self.media_title)
            texts.append(self.media_subtitle)
            row.append(icon)
            row.append(texts)
            button = Gtk.Button()
            button.set_child(row)
            button.set_size_request(-1, 130)
            button.add_css_class("flat")
            button.add_css_class("home-card")
            button.connect("clicked", self._on_media_clicked)
            self.add_cursor_effect(button)
            self._apply_media(None)
            return button

        def _build_clock_card(self):
            """Card with the time, the date and the weather location."""
            Gtk = self.gtk
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.set_valign(Gtk.Align.CENTER)
            row.set_margin_start(16)
            row.set_margin_end(16)
            self.time_label = Gtk.Label(xalign=0)
            self.time_label.add_css_class("home-clock-time")
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            texts.set_valign(Gtk.Align.CENTER)
            self.date_label = Gtk.Label(xalign=0)
            self.date_label.add_css_class("home-clock-date")
            location = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            location.append(
                Gtk.Image.new_from_icon_name(
                    self.calendar.resolve_icon(
                        ["weather-few-clouds-symbolic", "weather-clear-symbolic"]
                    )
                )
            )
            self.location_label = Gtk.Label(xalign=0)
            self.location_label.set_ellipsize(self.pango.EllipsizeMode.END)
            self.location_label.set_max_width_chars(22)
            self.location_label.add_css_class("home-clock-location")
            location.append(self.location_label)
            texts.append(self.date_label)
            texts.append(location)
            row.append(self.time_label)
            row.append(texts)
            button = Gtk.Button()
            button.set_child(row)
            button.set_size_request(-1, 90)
            button.add_css_class("flat")
            button.add_css_class("home-card")
            button.connect("clicked", self._on_clock_clicked)
            self.add_cursor_effect(button)
            self._refresh_clock()
            return button

        def _build_tiles(self):
            """Two column grid of quick toggles."""
            Gtk = self.gtk
            self.tile_grid = Gtk.Grid()
            self.tile_grid.set_row_spacing(8)
            self.tile_grid.set_column_spacing(8)
            self.tile_grid.set_column_homogeneous(True)
            self.tile_grid.set_valign(Gtk.Align.START)
            actions = {
                "wifi": self._toggle_wifi,
                "bluetooth": self._toggle_bluetooth,
                "caffeine": lambda *_: self._toggle_process("caffeine"),
                "night_light": lambda *_: self._toggle_process("night_light"),
                "dnd": self._toggle_dnd,
                "profile": self._toggle_profile,
            }
            for key in TILE_ORDER:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                box.set_halign(Gtk.Align.CENTER)
                box.set_valign(Gtk.Align.CENTER)
                image = Gtk.Image()
                image.set_pixel_size(20)
                label = Gtk.Label()
                label.set_ellipsize(self.pango.EllipsizeMode.END)
                label.set_max_width_chars(10)
                label.add_css_class("home-tile-label")
                box.append(image)
                box.append(label)
                button = Gtk.Button()
                button.set_child(box)
                button.set_size_request(92, 76)
                button.add_css_class("home-tile")
                button.connect("clicked", actions[key])
                self.add_cursor_effect(button)
                self.tiles[key] = {"button": button, "image": image, "label": label}
                self._tile_visible[key] = False
            self._render_tiles()
            return self.tile_grid

        # ------------------------------------------------------------ tiles
        def _command_available(self, key: str) -> bool:
            """Whether the program behind a command setting is installed."""
            try:
                program = shlex.split(self.commands[key])[0]
            except (IndexError, ValueError):
                return False
            return shutil.which(program) is not None

        def _process_running(self, key: str) -> bool:
            proc = self._procs.get(key)
            return proc is not None and proc.poll() is None

        def _dnd_client(self):
            """The notification client plugin, None when it is not loaded."""
            return self.plugins.get("notify_client")

        def _tile_info(self, key: str):
            """Visibility, active flag, label and icon candidates of a tile."""
            state = self.state
            if key == "wifi":
                enabled = state.get("wifi")
                return (
                    enabled is not None,
                    bool(enabled),
                    state.get("ssid") or "Wi-Fi",
                    ["network-wireless-symbolic", "network-wired-symbolic"],
                )
            if key == "bluetooth":
                powered = state.get("bluetooth")
                return (
                    powered is not None,
                    bool(powered),
                    "Bluetooth",
                    ["bluetooth-active-symbolic", "bluetooth-symbolic"],
                )
            if key == "caffeine":
                return (
                    self._command_available("caffeine"),
                    self._process_running("caffeine"),
                    "Caffeine",
                    [
                        "caffeine-cup-full-symbolic",
                        "my-caffeine-on-symbolic",
                        "view-reveal-symbolic",
                    ],
                )
            if key == "night_light":
                return (
                    self._command_available("night_light"),
                    self._process_running("night_light"),
                    "Night Light",
                    ["night-light-symbolic", "weather-clear-night-symbolic"],
                )
            if key == "dnd":
                client = self._dnd_client()
                return (
                    client is not None,
                    client is not None and not client.show_messages,
                    "Do Not Disturb",
                    [
                        "notifications-disabled-symbolic",
                        "preferences-system-notifications-symbolic",
                        "dialog-information-symbolic",
                    ],
                )
            profile = state.get("profile") or ""
            return (
                bool(state.get("profiles")),
                False,
                profile_label(profile),
                [
                    PROFILE_ICONS.get(profile, "power-profile-balanced-symbolic"),
                    "power-profile-balanced-symbolic",
                    "speedometer-symbolic",
                ],
            )

        def _render_tiles(self) -> None:
            """Updates every tile and lays out the visible ones in two columns."""
            changed = False
            for key in TILE_ORDER:
                visible, active, label, icons = self._tile_info(key)
                tile = self.tiles[key]
                tile["label"].set_label(label)
                tile["image"].set_from_icon_name(self.calendar.resolve_icon(icons))
                if active:
                    tile["button"].add_css_class("home-tile-active")
                else:
                    tile["button"].remove_css_class("home-tile-active")
                if visible != self._tile_visible[key]:
                    self._tile_visible[key] = visible
                    changed = True
            if not changed and self.tile_grid.get_first_child() is not None:
                return
            for key in TILE_ORDER:
                button = self.tiles[key]["button"]
                if button.get_parent() is self.tile_grid:
                    self.tile_grid.remove(button)
            position = 0
            for key in TILE_ORDER:
                if self._tile_visible[key]:
                    self.tile_grid.attach(
                        self.tiles[key]["button"], position % 2, position // 2, 1, 1
                    )
                    position += 1

        # ------------------------------------------------------ page hooks
        def _on_page_show(self) -> None:
            """Page visible: refresh everything and keep the page live."""
            self._ticks = 0
            self._refresh_clock()
            self._refresh_uptime()
            self._request_state()
            if not self._timers["clock"]:
                self._timers["clock"] = self.glib.timeout_add_seconds(1, self._on_tick)
            if not self._timers["state"]:
                self._timers["state"] = self.glib.timeout_add_seconds(
                    self.refresh_seconds, self._on_state_tick
                )

        def _on_page_hide(self) -> None:
            """Page hidden: stop polling."""
            self._stop_timers()

        def _stop_timers(self) -> None:
            for name, source in self._timers.items():
                if source:
                    self.glib.source_remove(source)
                    self._timers[name] = 0

        def _on_tick(self) -> bool:
            self._ticks += 1
            self._refresh_clock()
            if self._ticks % UPTIME_EVERY_TICKS == 0:
                self._refresh_uptime()
            return True

        def _on_state_tick(self) -> bool:
            self._request_state()
            return True

        # ---------------------------------------------------------- refresh
        def _refresh_clock(self) -> None:
            """Time, date and weather location."""
            now = self.glib.DateTime.new_now_local()
            time_format = "%H:%M" if self.clock_24h else "%I:%M %p"
            self.time_label.set_label(now.format(time_format) or "")
            self.date_label.set_label(now.format("%A, %x") or "")
            self.location_label.set_label(
                location_text(
                    self.get_root_setting([WEATHER_ID, "location", "name"], "")
                )
            )

        def _refresh_uptime(self) -> None:
            """Uptime row of the user card."""
            try:
                with open(UPTIME_FILE, encoding="utf-8") as handle:
                    seconds = parse_uptime(handle.read())
            except OSError:
                seconds = None
            self.uptime_label.set_label(uptime_line(seconds))

        def _request_state(self) -> None:
            """Starts a background check of the system state and the players."""
            if not self._state_busy:
                self._state_busy = True
                self.run_in_thread(self._collect_state)
            if not self._media_busy:
                self._media_busy = True
                self.run_in_async_task(self._collect_media())
            self._render_tiles()

        def _capture(self, args: list[str], timeout: float = 3.0) -> str:
            """Standard output of a command, empty when it fails or times out."""
            try:
                result = self.subprocess.run(
                    args, capture_output=True, text=True, timeout=timeout
                )
            except (OSError, self.subprocess.TimeoutExpired):
                return ""
            return result.stdout

        def _collect_state(self) -> None:
            """Thread: Wi-Fi, Bluetooth and power profile state."""
            state = {}
            try:
                if shutil.which("nmcli"):
                    state["wifi"] = parse_radio_enabled(
                        self._capture(["nmcli", "radio", "wifi"])
                    )
                    state["ssid"] = (
                        parse_active_ssid(
                            self._capture(
                                [
                                    "nmcli",
                                    "-t",
                                    "-f",
                                    "ACTIVE,SSID",
                                    "dev",
                                    "wifi",
                                    "list",
                                    "--rescan",
                                    "no",
                                ]
                            )
                        )
                        if state["wifi"]
                        else ""
                    )
                if shutil.which("bluetoothctl"):
                    state["bluetooth"] = parse_bluetooth_powered(
                        self._capture(["bluetoothctl", "show"])
                    )
                if shutil.which("powerprofilesctl"):
                    state["profile"] = self._capture(
                        ["powerprofilesctl", "get"]
                    ).strip()
                    state["profiles"] = parse_profile_list(
                        self._capture(["powerprofilesctl", "list"])
                    )
            finally:
                self.schedule_in_gtk_thread(self._apply_state, state)

        def _apply_state(self, state: dict) -> None:
            """GTK thread: stores the collected state and redraws the tiles."""
            self._state_busy = False
            self.state.update(state)
            self._render_tiles()

        async def _collect_media(self) -> None:
            """Asyncio thread: the player to show on the media card."""
            chosen = None
            try:
                if self._dbus is None:
                    bus = await MessageBus(bus_type=BusType.SESSION).connect()
                    self._dbus = DbusHelpers(bus)
                players = []
                for name in await self._dbus.get_active_mpris_players():
                    meta = await self._dbus.get_media_metadata(name)
                    if meta:
                        players.append(meta)
                chosen = pick_player(players)
            except Exception as e:
                self.logger.error(f"Home: could not read the media players: {e}")
                self._dbus = None
            self.schedule_in_gtk_thread(self._apply_media, chosen)

        def _apply_media(self, player) -> None:
            """GTK thread: shows the player on the media card."""
            self._media_busy = False
            self._media_active = player is not None
            title, subtitle = media_texts(player)
            self.media_title.set_label(title)
            self.media_subtitle.set_label(subtitle)

        # ---------------------------------------------------------- actions
        def _close_popover(self) -> None:
            """Closes the clock popover that hosts the page."""
            popover = getattr(self.calendar, "popover_calendar", None)
            if popover:
                popover.popdown()

        def _on_settings_clicked(self, *_) -> None:
            """Opens the panel settings (the Control Center window)."""
            control_center = self.plugins.get("control_center")
            if control_center is None:
                self.logger.error("Control Center plugin is not loaded.")
                return
            self._close_popover()
            control_center.do_activate()

        def _on_power_clicked(self, _button, action: str) -> None:
            """Runs the command of a power menu entry."""
            self.power_popover.popdown()
            self._close_popover()
            try:
                self.subprocess.Popen(shlex.split(self.commands[action]))
            except (OSError, ValueError) as e:
                self.logger.error(f"Home: {action} failed: {e}")

        def _on_media_clicked(self, *_) -> None:
            """Opens the media page when a player is active."""
            if self._media_active:
                self.calendar.show_page(NOW_PLAYING_PAGE)

        def _on_clock_clicked(self, *_) -> None:
            """Opens the weather page."""
            self.calendar.show_page(WEATHER_PAGE)

        def _run_then_refresh(self, args: list[str]) -> None:
            """Thread: runs a command, then checks the state again."""
            self._capture(args, timeout=10.0)
            self.schedule_in_gtk_thread(self._request_state)

        def _toggle_wifi(self, *_) -> None:
            enable = not self.state.get("wifi")
            self.state["wifi"] = enable
            self._render_tiles()
            self.run_in_thread(
                self._run_then_refresh,
                ["nmcli", "radio", "wifi", "on" if enable else "off"],
            )

        def _toggle_bluetooth(self, *_) -> None:
            enable = not self.state.get("bluetooth")
            self.state["bluetooth"] = enable
            self._render_tiles()
            self.run_in_thread(
                self._run_then_refresh,
                ["bluetoothctl", "power", "on" if enable else "off"],
            )

        def _toggle_profile(self, *_) -> None:
            target = next_profile(
                self.state.get("profile", ""), self.state.get("profiles", [])
            )
            if target is None:
                return
            self.state["profile"] = target
            self._render_tiles()
            self.run_in_thread(
                self._run_then_refresh, ["powerprofilesctl", "set", target]
            )

        def _toggle_dnd(self, *_) -> None:
            """Flips Do Not Disturb the way the notification page does."""
            client = self._dnd_client()
            if client is None:
                return
            enable = bool(client.show_messages)
            client.dnd_enabled = enable
            button = getattr(client, "dnd_toggle_btn", None)
            if button is not None:
                button.set_label("Show Notifications" if enable else "Do Not Disturb")
            client.on_dnd_toggled(None, enable)
            self._render_tiles()

        def _toggle_process(self, key: str) -> None:
            """Starts or stops the helper process behind Caffeine or Night Light."""
            if self._process_running(key):
                self._procs[key].terminate()
                self._procs[key] = None
            else:
                try:
                    self._procs[key] = self.subprocess.Popen(
                        shlex.split(self.commands[key]),
                        stdout=self.subprocess.DEVNULL,
                        stderr=self.subprocess.DEVNULL,
                        preexec_fn=die_with_parent if libc else None,
                    )
                except (OSError, ValueError) as e:
                    self.logger.error(f"Home: could not start {key}: {e}")
            self._render_tiles()

        # -------------------------------------------------------- lifecycle
        def on_disable(self) -> None:
            """Stops polling and helpers and takes the page out of the popover."""
            self._stop_timers()
            for key in self._procs:
                if self._process_running(key):
                    self._procs[key].terminate()
                self._procs[key] = None
            if self._dbus is not None:
                self._dbus.bus.disconnect()
                self._dbus = None
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)

    return HomePlugin
