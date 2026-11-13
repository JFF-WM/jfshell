class TaskbarMenu:
    """Handles the creation and logic of the right-click context menu."""

    def __init__(self, plugin_instance):
        """Initializes the menu handler."""
        self.plugin = plugin_instance
        self.ipc = plugin_instance.ipc
        self.menu = None

        # Initialize PulseAudio controller using the same library as AdvancedVolumePlugin
        import pulsectl

        self.pulse = pulsectl.Pulse("jfshell-taskbar-audio-mgr")

    def _create_menu_item(self, label_text, icon_name, callback):
        """Helper to create a menu button with an icon and label."""
        from gi.repository import Gtk

        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.add_css_class("taskbar-menu-item")

        # Spacing to give breathing room for icons
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        box.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(24)
        icon.add_css_class("taskbar-menu-icon")

        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(0)
        lbl.add_css_class("taskbar-menu-label")

        box.append(icon)
        box.append(lbl)
        btn.set_child(box)

        btn.connect("clicked", callback)
        return btn

    def show(self, widget, x, y):
        """Creates and displays the context menu for a specific view."""
        from gi.repository import Gtk, Gdk

        if self.menu:
            self.menu.unparent()
            self.menu = None

        self.menu = Gtk.Popover()
        self.menu.set_parent(widget)
        self.menu.set_has_arrow(False)
        self.menu.set_autohide(True)
        self.menu.add_css_class("taskbar-context-menu")
        self.menu.active_view_id = widget.view_id  # pyright: ignore

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_end(8)

        view_data = self.ipc.get_view(widget.view_id)
        if not view_data:
            return

        is_fullscreen = view_data.get("fullscreen", False)
        is_atop = view_data.get("always-on-top", False)
        is_sticky = view_data.get("sticky", False)

        actions = [
            (
                "Restore" if is_fullscreen else "Fullscreen",
                "view-fullscreen-symbolic",
                self._on_menu_fullscreen_clicked,
            ),
            (
                "Lower" if is_atop else "Always on Top",
                "go-top-symbolic",
                self._on_menu_atop_clicked,
            ),
            (
                "Unstick" if is_sticky else "Sticky",
                "pin-symbolic",
                self._on_menu_sticky_clicked,
            ),
            (
                "Move to Next Output",
                "go-next-symbolic",
                self._on_menu_move_next_clicked,
            ),
            (
                "Pause Process",
                "media-playback-pause-symbolic",
                self._on_menu_pause_clicked,
            ),
            (
                "Resume Process",
                "media-playback-start-symbolic",
                self._on_menu_resume_clicked,
            ),
            (
                "Mute Audio",
                "audio-volume-muted-symbolic",
                self._on_menu_mute_clicked,
            ),
            (
                "Unmute Audio",
                "audio-volume-high-symbolic",
                self._on_menu_unmute_clicked,
            ),
            (
                "Close Window",
                "window-close-symbolic",
                self._on_menu_close_clicked,
            ),
        ]

        for label, icon, cb in actions:
            item = self._create_menu_item(label, icon, cb)
            box.append(item)

        self.menu.set_child(box)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.menu.set_pointing_to(rect)
        self.menu.popup()

    def popdown(self):
        """Hides the menu."""
        if self.menu:
            self.menu.popdown()

    # --- CORE LOGIC HANDLERS ---

    def _toggle_process_state(self, vid, stop):
        """Sends SIGSTOP or SIGCONT to the PID associated with a view ID."""
        import os
        import signal

        view_data = self.ipc.get_view(vid)
        if not view_data or "pid" not in view_data:
            return

        pid = view_data.get("pid")
        try:
            sig = signal.SIGSTOP if stop else signal.SIGCONT
            os.kill(pid, sig)
        except (PermissionError, ProcessLookupError):
            pass

    def _toggle_audio_mute(self, vid, mute):
        """Finds all audio streams for the PID and sets mute state via PulseAudio."""
        view_data = self.ipc.get_view(vid)
        if not view_data or "pid" not in view_data:
            return

        target_pid = str(view_data["pid"])

        try:
            # Match application.process.id in the PulseAudio sink input list
            for stream in self.pulse.sink_input_list():
                if stream.proplist.get("application.process.id") == target_pid:
                    self.pulse.mute(stream, mute)
        except Exception:
            pass

    # --- CALLBACKS ---

    def _on_menu_pause_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        self._toggle_process_state(vid, True)
        self.menu.popdown()

    def _on_menu_resume_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        self._toggle_process_state(vid, False)
        self.menu.popdown()

    def _on_menu_mute_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        self._toggle_audio_mute(vid, True)
        self.menu.popdown()

    def _on_menu_unmute_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        self._toggle_audio_mute(vid, False)
        self.menu.popdown()

    def _on_menu_fullscreen_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        current = self.ipc.get_view(vid)["fullscreen"]
        self.ipc.set_view_fullscreen(vid, not current)
        self.menu.popdown()

    def _on_menu_atop_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        current = self.ipc.get_view(vid)["always-on-top"]
        self.ipc.set_view_always_on_top(vid, not current)
        self.menu.popdown()

    def _on_menu_sticky_clicked(self, _):
        vid = self.menu.active_view_id  # pyright: ignore
        current = self.ipc.get_view(vid)["sticky"]
        self.ipc.set_view_sticky(vid, not current)
        self.menu.popdown()

    def _on_menu_move_next_clicked(self, _):
        self.plugin.compositor_helper.send_view_to_output(
            self.menu.active_view_id, None, True
        )  # pyright: ignore
        self.menu.popdown()

    def _check_hanging_process(self, vid):
        from src.shared.compositor_helpers import CompositorHelpers

        compositor_helper = CompositorHelpers(self.plugin._panel_instance)
        compositor_helper._check_hanging_process(vid)

    def _on_menu_close_clicked(self, _):
        from gi.repository import GLib

        vid = self.menu.active_view_id  # pyright: ignore
        self.ipc.close_view(vid)
        self.menu.popdown()
        GLib.timeout_add_seconds(3, self._check_hanging_process, vid)
