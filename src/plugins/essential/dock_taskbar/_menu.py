"""Context menus of the dock taskbar.

Right click on an icon opens the application menu (new window, always on top,
sticky, mute/unmute, close all, pin/unpin). Icons are reordered by dragging
them (see DockTaskbarUI), not from the menu. Right click on a thumbnail
card opens the menu of that one window (fullscreen, always on top, sticky,
mute/unmute, next output, close).
"""

from ._audio import DockTaskbarAudio


class DockTaskbarMenu:
    """Builds and shows the popover menus."""

    def __init__(self, plugin_instance):
        """Initializes the menu handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.popover = None
        self._closed_id = 0
        self.audio = DockTaskbarAudio()

    # --- building blocks ---

    def _row(self, label_text, icon_name, callback):
        """A menu button with an icon and a label."""
        from gi.repository import Gtk

        btn = Gtk.Button()
        btn.set_has_frame(False)
        btn.add_css_class("dock-menu-item")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        label = Gtk.Label(label=label_text)
        label.set_xalign(0)
        box.append(icon)
        box.append(label)
        btn.set_child(box)

        def clicked(_):
            self.popdown()
            callback()

        btn.connect("clicked", clicked)
        return btn

    def _open(self, parent, rows, x, y):
        """Shows a popover with ``rows`` pointing at (x, y) of ``parent``."""
        from gi.repository import Gdk, Gtk

        self._unparent()
        popover = Gtk.Popover()
        popover.set_parent(parent)
        popover.set_has_arrow(False)
        popover.set_autohide(True)
        popover.add_css_class("dock-context-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for row in rows:
            box.append(row)
        popover.set_child(box)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        # The thumbnails stay while one of their cards has a menu open.
        self._closed_id = popover.connect(
            "closed", lambda *_: self.plugin.preview.release()
        )
        self.popover = popover
        popover.popup()

    def _unparent(self):
        if self.popover is not None:
            # Replacing a menu must not release the thumbnails the new one holds.
            if self._closed_id:
                self.popover.disconnect(self._closed_id)
                self._closed_id = 0
            self.popover.popdown()
            self.popover.unparent()
            self.popover = None

    def destroy(self):
        """Plugin shutdown."""
        self._unparent()
        self.audio.close()

    def is_open(self):
        """Whether a context menu is on screen."""
        return bool(self.popover and self.popover.get_visible())

    def popdown(self):
        """Hides the menu."""
        if self.popover is not None:
            self.popover.popdown()

    def forget(self, widget):
        """A widget is being removed: drop the menu if it hangs on it."""
        if self.popover is not None and self.popover.get_parent() is widget:
            self._unparent()

    # --- application menu ---

    def show_app(self, button, x, y, entry):
        """Menu of an icon: launching, window state, audio, closing, pinning."""
        if entry is None:
            return
        p = self.plugin
        rows = []
        if p.command_for(entry):
            rows.append(self._row("New Window", "window-new-symbolic", lambda: p.launch(entry)))
        if entry.running:
            states = self._view_states(entry.views)
            if states:
                rows.extend(self._flag_rows(states))
                rows.extend(self._audio_rows(states))
            label = "Close All Windows" if len(entry.views) > 1 else "Close Window"
            rows.append(self._row(label, "window-close-symbolic", lambda: p.close_all(entry)))
        if entry.pinned:
            rows.append(self._row("Unpin from Dock", "list-remove-symbolic", lambda: p.unpin(entry)))
        elif p.command_for(entry):
            rows.append(self._row("Pin to Dock", "pin-symbolic", lambda: p.pin(entry)))
        if rows:
            self._open(button, rows, x, y)

    # --- window menu ---

    def show_window(self, widget, x, y, view):
        """Menu of one window (opened from its thumbnail card)."""
        p = self.plugin
        vid = view.get("id")
        state = p.ipc.get_window(vid) if vid is not None else None
        if not state:
            return
        fullscreen = state.get("fullscreen", False)
        atop = state.get("always-on-top", False)
        sticky = state.get("sticky", False)
        ipc = p.ipc
        rows = [
            self._row(
                "Restore" if fullscreen else "Fullscreen",
                "view-fullscreen-symbolic",
                lambda: ipc.set_window_fullscreen(vid, not fullscreen),
            ),
            self._row(
                "Lower" if atop else "Always on Top",
                "go-top-symbolic",
                lambda: ipc.set_window_always_on_top(vid, not atop),
            ),
            self._row(
                "Unstick" if sticky else "Sticky",
                "pin-symbolic",
                lambda: ipc.set_window_sticky(vid, not sticky),
            ),
            self._row(
                "Move to Next Output",
                "go-next-symbolic",
                lambda: ipc.move_window_to_monitor(vid, "next"),
            ),
            *self._audio_rows([(vid, state)]),
            self._row("Close Window", "window-close-symbolic", lambda: p.close_view(view)),
        ]
        self._open(widget, rows, x, y)
        p.preview.hold()

    # --- rows shared by the menus ---

    def _view_states(self, views):
        """(window id, IPC state) of every window of ``views`` that still exists."""
        states = []
        for view in views:
            vid = view.get("id")
            state = self.plugin.ipc.get_window(vid) if vid is not None else None
            if state:
                states.append((vid, state))
        return states

    def _flag_rows(self, states):
        """Always on top and sticky for a group of windows.

        A row turns its flag off only when every window has it on, otherwise it
        turns the flag on for all of them.
        """
        ipc = self.plugin.ipc
        ids = [vid for vid, _ in states]
        atop = all(state.get("always-on-top", False) for _, state in states)
        sticky = all(state.get("sticky", False) for _, state in states)

        def apply(setter, on):
            for vid in ids:
                setter(vid, on)

        return [
            self._row(
                "Lower" if atop else "Always on Top",
                "go-top-symbolic",
                lambda: apply(ipc.set_window_always_on_top, not atop),
            ),
            self._row(
                "Unstick" if sticky else "Sticky",
                "pin-symbolic",
                lambda: apply(ipc.set_window_sticky, not sticky),
            ),
        ]

    def _audio_rows(self, states):
        """Mute / Unmute rows, only for windows whose app has playback streams."""
        pids = [state.get("pid") for _, state in states]
        audio = self.audio
        muted, unmuted = audio.state(pids)
        rows = []
        if unmuted:
            rows.append(
                self._row(
                    "Mute Audio",
                    "audio-volume-muted-symbolic",
                    lambda: audio.set_mute(pids, True),
                )
            )
        if muted:
            rows.append(
                self._row(
                    "Unmute Audio",
                    "audio-volume-high-symbolic",
                    lambda: audio.set_mute(pids, False),
                )
            )
        return rows
