def get_plugin_metadata(panel):
    """Retrieves the metadata for the Universal Update plugin.

    Args:
        panel: The panel instance (unused, the plugin lives in the clock popover).

    Returns:
        dict: A dictionary containing plugin identification, requirements, and metadata.
    """
    import shutil

    bins = ["pacman", "dnf", "apt", "zypper", "xbps-install", "apk", "flatpak"]
    ENABLE = any(shutil.which(b) for b in bins)
    id = "org.jfshell.plugin.universal_update"
    return {
        "id": id,
        "name": "Universal Update",
        "version": "2.5.1",
        "enabled": ENABLE,
        "deps": ["css_generator", "calendar"],
        "description": "Universal Linux update manager with per-backend custom commands, shown as a page of the clock popover.",
    }


def get_plugin_class():
    """Returns the UniversalUpdate plugin class definition with logic for background updates.

    Returns:
        type: The UniversalUpdate class inheriting from BasePlugin.
    """
    import shutil
    import time
    from src.plugins.core._base import BasePlugin
    from ._manager import get_manager

    PAGE_ID = "universal_update"

    class UniversalUpdate(BasePlugin):
        """Plugin for orchestrating system updates across multiple Linux package managers."""

        def __init__(self, panel_instance):
            """Initializes the UniversalUpdate plugin, registering settings and UI components.

            Args:
                panel_instance: The main panel instance this plugin is attached to.
            """
            super().__init__(panel_instance)

        def delay_on_start(self):
            """Initializes the UI popover and starts the periodic update checking loop."""
            custom_cmds = {
                "pacman": self.get_plugin_setting_add_hint(
                    ["commands", "pacman"], "", "Custom command for Pacman."
                ),
                "dnf": self.get_plugin_setting_add_hint(
                    ["commands", "dnf"], "", "Custom command for DNF."
                ),
                "apt": self.get_plugin_setting_add_hint(
                    ["commands", "apt"], "", "Custom command for APT."
                ),
                "zypper": self.get_plugin_setting_add_hint(
                    ["commands", "zypper"], "", "Custom command for Zypper."
                ),
                "xbps": self.get_plugin_setting_add_hint(
                    ["commands", "xbps"], "", "Custom command for XBPS."
                ),
                "apk": self.get_plugin_setting_add_hint(
                    ["commands", "apk"], "", "Custom command for APK."
                ),
                "flatpak": self.get_plugin_setting_add_hint(
                    ["commands", "flatpak"], "", "Custom command for Flatpak."
                ),
            }
            UpdateManager = get_manager()
            self.manager = UpdateManager(
                config={k: v for k, v in custom_cmds.items() if v}
            )
            self.check_interval_seconds = self.get_plugin_setting_add_hint(
                ["timing", "check_interval_seconds"], 3600, "Background check interval."
            )
            self.check_timeout_seconds = self.get_plugin_setting_add_hint(
                ["timing", "check_timeout_seconds"], 20, "Command timeout per backend."
            )
            self.terminal_monitor_interval_seconds = self.get_plugin_setting_add_hint(
                ["timing", "terminal_monitor_interval_seconds"],
                2,
                "PID check interval.",
            )

            self.update_command = self.get_plugin_setting_add_hint(
                ["actions", "system_update_command"],
                self.manager.get_combined_command(),
                "Combined update command for all detected managers.",
            )

            self.terminal_preference = self.get_plugin_setting_add_hint(
                ["actions", "terminal_preference"],
                ["kitty", "alacritty", "terminator", "xterm", "gnome-terminal"],
                "Preferred terminals.",
            )
            self.list_max_height = self.get_plugin_setting_add_hint(
                ["ui", "list_max_height"],
                320,
                "Height in pixels after which the package list scrolls.",
            )
            self.main_icon = self.get_plugin_setting_add_hint(
                ["main_icon"],
                "software-update-available-symbolic",
                "Icon of the update button in the clock popover's sidebar.",
            )
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Universal Update page."
                )
                return False
            self.calendar = self.plugins["calendar"]
            self.update_count = 0
            self.is_checking = False
            self.terminal_pid = None
            self.count_label = None
            self.checked_label = None
            self.scroller = None
            self.list_box = None
            self.update_btn = None
            self._setup_popover()
            self.run_in_async_task(self._manual_refresh())
            self.glib.timeout_add_seconds(
                self.check_interval_seconds, self._check_updates_periodically
            )
            self.plugins["css_generator"].install_css("universal-update.css")
            return False

        def on_start(self):
            self.calendar = None
            self.trigger_button = None
            self.glib.timeout_add_seconds(10, self.delay_on_start)

        def _setup_popover(self):
            """Constructs the page (status, package list, actions) and adds it to the clock popover."""
            Gtk = self.gtk
            self.popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.popover_box.add_css_class("universal-update-popover")
            for side in ["top", "bottom", "start", "end"]:
                getattr(self.popover_box, f"set_margin_{side}")(10)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.count_label = Gtk.Label(label="Initializing...")
            self.count_label.set_xalign(0)
            self.count_label.set_hexpand(True)
            self.count_label.add_css_class("heading")
            self.checked_label = Gtk.Label()
            self.checked_label.add_css_class("dim-label")
            self.checked_label.add_css_class("caption")
            header.append(self.count_label)
            header.append(self.checked_label)
            self.popover_box.append(header)

            # The list grows with its content up to list_max_height, then
            # scrolls, so a big update never stretches the popover.
            self.list_box = Gtk.ListBox()
            self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
            self.list_box.add_css_class("universal-update-list")
            self.scroller = Gtk.ScrolledWindow()
            self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.scroller.set_propagate_natural_height(True)
            self.scroller.set_max_content_height(max(80, int(self.list_max_height)))
            self.scroller.set_vexpand(False)
            self.scroller.add_css_class("universal-update-scroller")
            self.scroller.set_child(self.list_box)
            self.scroller.set_visible(False)
            self.popover_box.append(self.scroller)

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            buttons.set_homogeneous(True)
            refresh_btn = Gtk.Button(label="Refresh")
            refresh_btn.connect(
                "clicked", lambda _: self.run_in_async_task(self._manual_refresh())
            )
            self.update_btn = Gtk.Button(label="Update Now")
            self.update_btn.add_css_class("universal-update-now")
            self.update_btn.add_css_class("suggested-action")
            self.update_btn.connect("clicked", self._launch_terminal)
            buttons.append(refresh_btn)
            buttons.append(self.update_btn)
            self.popover_box.append(buttons)

            added = self.calendar.add_page(
                PAGE_ID,
                [self.main_icon, "software-update-available-symbolic"],
                "Updates",
                self.popover_box,
                on_show=self._on_page_show,
            )
            if added:
                self.trigger_button = self.calendar.pages[PAGE_ID]["button"]
                self.trigger_button.add_css_class("universal-update-button")

        def _clear_list(self):
            if hasattr(self.list_box, "remove_all"):
                self.list_box.remove_all()
                return
            child = self.list_box.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.list_box.remove(child)
                child = nxt

        def _header_row(self, backend, count):
            Gtk = self.gtk
            label = Gtk.Label(label=f"{backend} · {count}")
            label.set_xalign(0)
            label.add_css_class("dim-label")
            label.add_css_class("caption-heading")
            label.set_margin_top(6)
            label.set_margin_bottom(2)
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            row.add_css_class("universal-update-backend")
            row.set_child(label)
            return row

        def _package_row(self, update):
            Gtk = self.gtk
            Ellipsize = self.pango.EllipsizeMode
            name = Gtk.Label(label=update.name)
            name.set_xalign(0)
            name.set_ellipsize(Ellipsize.END)
            versions = (
                f"{update.old} → {update.new}" if update.old else update.new
            ) or "new version"
            detail_text = f"{versions} · {update.source}" if update.source else versions
            detail = Gtk.Label(label=detail_text)
            detail.set_xalign(0)
            detail.set_ellipsize(Ellipsize.MIDDLE)
            detail.add_css_class("dim-label")
            detail.add_css_class("caption")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.set_margin_top(3)
            box.set_margin_bottom(3)
            box.append(name)
            box.append(detail)
            row = Gtk.ListBoxRow()
            row.set_activatable(False)
            row.set_selectable(False)
            row.add_css_class("universal-update-package")
            row.set_tooltip_text(f"{update.name}\n{detail_text}")
            row.set_child(box)
            return row

        def _fill_list(self, details):
            """Shows the pending packages, grouped per backend when there are several."""
            self._clear_list()
            groups = [(name, ups) for name, ups in details if ups]
            for backend, updates in groups:
                if len(groups) > 1:
                    self.list_box.append(self._header_row(backend, len(updates)))
                for update in sorted(updates, key=lambda u: u.name.lower()):
                    self.list_box.append(self._package_row(update))
            self.scroller.set_visible(bool(groups))

        def _on_page_show(self):
            """Triggers a refresh of the update count when the page becomes visible."""
            self.run_in_async_task(self._manual_refresh())

        def on_disable(self):
            """Takes the page out of the clock popover."""
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)
            self.trigger_button = None

        async def _manual_refresh(self):
            """Performs an asynchronous update check, preventing concurrent polling operations."""
            if self.is_checking:
                return
            self.is_checking = True
            self.schedule_in_gtk_thread(
                self.count_label.set_label,
                "Polling backends...",
            )
            await self._check_updates()
            self.is_checking = False

        def _check_updates_periodically(self):
            """Periodic callback for GLib timeout to trigger background update checks.

            Returns:
                bool: True to keep the timeout active.
            """
            if not self.is_checking:
                self.run_in_async_task(self._manual_refresh())
            return True

        async def _check_updates(self):
            """Queries all available update backends and schedules a UI update with the results."""
            try:
                details = await self.manager.check_all_details(
                    self.asyncio, self.subprocess, self.check_timeout_seconds
                )
                count = sum(len(updates) for _, updates in details)
                self.update_count = count
                self.schedule_in_gtk_thread(self._update_ui, count, details)
            except Exception as e:
                self.logger.exception(f"Manager check failed: {e}")
                self.schedule_in_gtk_thread(self._update_ui, -1)

        def _update_ui(self, count, details=None):
            """Updates the button visibility, the labels and the package list.

            Args:
                count (int): Number of updates found, or -1 for error.
                details (list): ``[(backend name, [Update, ...]), ...]``.
            """
            if self.trigger_button is None:
                return
            self.checked_label.set_label(time.strftime("%H:%M"))
            self.checked_label.set_tooltip_text("Last check")
            self._fill_list(details or [])
            self.update_btn.set_sensitive(count != 0)
            if count == -1:
                self.count_label.set_label("Backend Error")
                self.trigger_button.show()
            elif count == 0:
                self.count_label.set_label("System up to date")
                self.trigger_button.hide()
                if self.calendar.current_page == PAGE_ID:
                    self.calendar.show_page(self.calendar.CALENDAR_PAGE)
            else:
                noun = "update" if count == 1 else "updates"
                self.count_label.set_label(f"{count} {noun} available")
                self.trigger_button.show()

        def _launch_terminal(self, _):
            """Spawns the preferred terminal to execute the combined update command string."""
            terminal = next(
                (t for t in self.terminal_preference if shutil.which(t)), None
            )
            if not terminal:
                return
            wrapped_command = f"sh -c \"{self.update_command}; echo; echo 'Task finished. Press Enter to exit...'; read\""
            cmd = [terminal, "-e", "sh", "-c", wrapped_command]
            try:
                proc = self.subprocess.Popen(cmd)
                self.terminal_pid = proc.pid
                self.glib.timeout_add_seconds(
                    self.terminal_monitor_interval_seconds, self._monitor_terminal
                )
            except Exception as e:
                self.logger.exception(f"Launch error: {e}")
            self.calendar.popover_calendar.popdown()

        def _monitor_terminal(self):
            """Monitors the terminal process PID and refreshes status once the process exits.

            Returns:
                bool: True if monitoring should continue, False if process finished.
            """
            if not self.terminal_pid:
                return False
            try:
                res = self.subprocess.run(
                    ["ps", "-p", str(self.terminal_pid)], capture_output=True
                )
                if res.returncode != 0:
                    self.terminal_pid = None
                    self.run_in_async_task(self._manual_refresh())
                    return False
                return True
            except Exception:
                return False

    return UniversalUpdate
