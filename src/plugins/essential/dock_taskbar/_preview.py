"""Hover thumbnails of the dock taskbar.

Hovering an icon for a moment opens a popover with one card per window of the
group: a live picture, the window title and a close button. Clicking a card
focuses that window. The pictures come from jff's ``window-thumbnail`` IPC
command and are redrawn while the popover is open, at up to ``preview.fps``
frames per second (a frame is skipped while the previous one is still being
fetched, so a slow compositor lowers the rate instead of piling up requests).
All IPC and image decoding runs on worker threads, so hovering never blocks
the panel. When jff cannot draw a window, its card shows the app icon instead.
"""

import os
import time


class DockTaskbarPreview:
    """Hover popover with a card per window of the hovered icon."""

    # Wait before a preview opens in place of another one (moving along the bar).
    SWITCH_DELAY_MS = 60
    # Wait before a preview closes: time to cross the gap onto the popover.
    LEAVE_DELAY_MS = 350
    # Where the popover sits relative to the button, per panel position.
    POSITIONS = {"bottom": "TOP", "top": "BOTTOM"}

    def __init__(self, plugin_instance):
        """Initializes the preview handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.popover = None
        self.button = None
        self.key = None
        self._ids = ()
        self._show_id = 0
        self._hide_id = 0
        self._refresh_id = 0
        # Bumped whenever the preview is cancelled, so late worker results are dropped.
        self._generation = 0
        self._refreshing = False
        self._frames = 0
        self._frames_since = 0.0
        # Kept open while a context menu of one of its cards is up.
        self._held = False
        # view id -> Gtk.Picture currently shown, for the refresh.
        self._pictures = {}
        # The window dimmed by hovering its card, restored when leaving.
        self._effect_view = None

    # --- state ---

    def _enabled(self):
        return bool(self.plugin.config.preview_enabled)

    def is_visible(self):
        """Whether a preview is on screen."""
        return bool(self.popover and self.popover.get_visible())

    def _cancel_timer(self, name):
        from gi.repository import GLib

        source = getattr(self, name)
        if source:
            GLib.source_remove(source)
            setattr(self, name, 0)

    def _entry_of(self, button):
        return self.plugin.entry_map.get(getattr(button, "entry_key", None))

    # --- hover entry points ---

    def on_enter(self, button):
        """Pointer entered an icon: open its thumbnails after a short wait."""
        from gi.repository import GLib

        entry = self._entry_of(button)
        if not self._enabled() or entry is None or not entry.running:
            return
        if self.plugin.menu.is_open():
            return
        self._cancel_timer("_hide_id")
        if self.is_visible() and self.button is button:
            return
        self._cancel_timer("_show_id")
        delay = (
            self.SWITCH_DELAY_MS
            if self.is_visible()
            else max(0, int(self.plugin.config.preview_delay))
        )
        self._show_id = GLib.timeout_add(delay, self._start_load, button)

    def on_leave(self, button):
        """Pointer left an icon: close its thumbnails shortly, or drop a pending load."""
        self._cancel_timer("_show_id")
        if self.is_visible() and self.button is button:
            self._cancel_timer("_hide_id")
            self._schedule_hide()
        elif self.button is None or self.button is button:
            # Nothing on screen yet: cancel a load that is still running.
            self._generation += 1

    def _schedule_hide(self):
        from gi.repository import GLib

        if self._held:
            return
        self._cancel_timer("_hide_id")
        self._hide_id = GLib.timeout_add(self.LEAVE_DELAY_MS, self._hide_later)

    def _hide_later(self):
        self._hide_id = 0
        self.hide()
        return False

    def hold(self):
        """Keeps the thumbnails open (a context menu of a card is showing)."""
        self._held = True
        self._cancel_timer("_hide_id")

    def release(self):
        """Lets the thumbnails close again once the pointer is away."""
        self._held = False
        if self.is_visible():
            self._schedule_hide()

    # --- loading (worker thread) ---

    def _fetch(self, view_id, scale):
        """A texture of one window, or None. Runs on a worker thread.

        The picture is decoded into memory and its file is deleted right away,
        so no PNG outlives the frame it carries (also when the preview was
        closed while the request was running).
        """
        from gi.repository import Gdk

        cfg = self.plugin.config
        thumb = self.plugin.ipc.get_window_thumbnail(
            view_id,
            max(1, int(cfg.preview_width * scale)),
            max(1, int(cfg.preview_height * scale)),
        )
        if not thumb:
            return None
        try:
            return Gdk.Texture.new_from_filename(thumb["path"])
        except Exception:
            return None
        finally:
            try:
                os.unlink(thumb["path"])
            except OSError:
                pass

    def _start_load(self, button):
        """Timer callback on the GTK thread: fetch the pictures in the background."""
        self._show_id = 0
        entry = self._entry_of(button)
        if entry is None or not entry.running:
            return False
        self._generation += 1
        views = list(entry.views[: self.plugin.config.preview_max_windows])
        scale = max(1, button.get_scale_factor())
        self.plugin.run_in_thread(
            self._load, button, entry.key, views, self._generation, scale
        )
        return False

    def _load(self, button, key, views, generation, scale):
        from gi.repository import GLib

        try:
            cards = [(view, self._fetch(view.get("id"), scale)) for view in views]
        except Exception as e:
            self.plugin.logger.debug(f"Preview load failed: {e}")
            return
        if cards:
            GLib.idle_add(self._present, button, key, generation, cards, scale)

    def refresh_if_changed(self, key, views):
        """Rebuilds the open thumbnails when the windows of their group changed."""
        if not self.is_visible() or self.key != key or self.button is None:
            return
        shown = tuple(v.get("id") for v in views[: self.plugin.config.preview_max_windows])
        if shown != self._ids:
            if shown:
                self._start_load(self.button)
            else:
                self.hide()

    # --- showing (GTK thread) ---

    def _present(self, button, key, generation, cards, scale):
        """Builds and opens the popover once the pictures are ready."""
        from gi.repository import Gtk

        entry = self._entry_of(button)
        if generation != self._generation or entry is None or entry.key != key:
            return False
        if self.plugin.menu.is_open():
            return False

        # The card under the pointer is about to disappear without a leave event.
        try:
            self._clear_effect()
        except Exception as e:
            self.plugin.logger.debug(f"Preview effect reset failed: {e}")
        self._release_popover()
        self._pictures = {}
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for view, texture in cards:
            row.append(self._build_card(entry, view, texture, scale))

        popover = Gtk.Popover()
        popover.add_css_class("dock-preview")
        popover.set_has_arrow(False)
        popover.set_autohide(False)
        popover.set_can_target(True)
        popover.set_focusable(False)
        # Keep it open while the pointer is on it, close when it leaves.
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._cancel_timer("_hide_id"))
        motion.connect("leave", lambda *_: self._schedule_hide())
        popover.add_controller(motion)
        popover.set_position(self._position())
        popover.set_child(row)
        popover.set_parent(button)
        self.popover = popover
        self.button = button
        self.key = key
        self._ids = tuple(v.get("id") for v, _ in cards)
        # The thumbnails replace the tooltip while they are open.
        button.set_has_tooltip(False)
        popover.popup()
        self._start_refresh()
        return False

    def _build_card(self, entry, view, texture, scale):
        from gi.repository import Gtk, Pango

        cfg = self.plugin.config
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("dock-preview-card")
        if view.get("is_focused"):
            card.add_css_class("focused")
        card.set_cursor_from_name("pointer")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        app_icon = Gtk.Image()
        self.plugin.ui._set_icon(app_icon, self.plugin.icon_for(entry), 16)
        title = Gtk.Label(label=view.get("title", "") or "")
        title.set_xalign(0)
        title.set_hexpand(True)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(24)
        title.add_css_class("dock-preview-title")
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.set_has_frame(False)
        close.add_css_class("dock-preview-close")
        close.connect("clicked", self._on_close_clicked, view)
        header.append(app_icon)
        header.append(title)
        header.append(close)
        card.append(header)

        if texture is not None:
            body = Gtk.Picture.new_for_paintable(texture)
            body.set_can_shrink(False)
            body.set_halign(Gtk.Align.CENTER)
            body.add_css_class("dock-preview-picture")
            self._fit(body, texture, scale)
            self._pictures[view.get("id")] = body
        else:
            # jff cannot draw this window (or has no thumbnail command): show its icon.
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            body.add_css_class("dock-preview-placeholder")
            body.set_size_request(min(cfg.preview_width, 200), min(cfg.preview_height, 120))
            big = Gtk.Image()
            self.plugin.ui._set_icon(big, self.plugin.icon_for(entry), 64)
            big.set_hexpand(True)
            big.set_vexpand(True)
            body.append(big)
        card.append(body)

        click = Gtk.GestureClick(button=1)
        click.connect("released", self._on_card_clicked, view)
        card.add_controller(click)
        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_card_right_click, view)
        card.add_controller(right)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_, v=view: self._peek(v, True))
        motion.connect("leave", lambda *_, v=view: self._peek(v, False))
        card.add_controller(motion)
        return card

    def _on_card_clicked(self, gesture, n_press, x, y, view):
        """Focuses the window of a clicked card and closes the preview."""
        self.hide()
        self.plugin.focus_view(view)

    def _on_card_right_click(self, gesture, n_press, x, y, view):
        self.plugin.menu.show_window(gesture.get_widget(), x, y, view)

    def _on_close_clicked(self, button, view):
        """Asks the window to close; the preview follows the window list."""
        self._peek(view, False)
        self.plugin.close_view(view)

    def _peek(self, view, on):
        """Dims the window of the hovered card, so it stands out on the screen."""
        helper = self.plugin.compositor_helper
        try:
            if on:
                self._clear_effect()
                helper.view_focus_effect_selected(view, 0.9, True)
                self._effect_view = view
            elif self._effect_view is not None and self._effect_view.get("id") == view.get("id"):
                self._clear_effect()
        except Exception as e:
            self.plugin.logger.debug(f"Preview peek failed: {e}")

    def _clear_effect(self):
        view, self._effect_view = self._effect_view, None
        if view is not None:
            self.plugin.compositor_helper.view_focus_effect_selected(view, selected=False)

    def _position(self):
        from gi.repository import Gtk

        name = self.POSITIONS.get(self.plugin.position, "TOP")
        return getattr(Gtk.PositionType, name)

    @staticmethod
    def _fit(picture, texture, scale):
        """Sizes a picture to its texture in logical pixels (the texture is at device scale)."""
        picture.set_size_request(
            max(1, texture.get_width() // scale), max(1, texture.get_height() // scale)
        )

    # --- live refresh ---

    def _start_refresh(self):
        from gi.repository import GLib

        self._cancel_timer("_refresh_id")
        fps = int(self.plugin.config.preview_fps)
        self._frames = 0
        self._frames_since = time.monotonic()
        if fps > 0 and self._pictures:
            self._refresh_id = GLib.timeout_add(max(1, round(1000 / fps)), self._refresh)

    def _refresh(self):
        """Timer callback: redraw the open preview, or close it if its button is gone."""
        button = self.button
        if (
            not self.is_visible()
            or button is None
            or not button.get_visible()
            or button not in self.plugin.widgets.values()
        ):
            self._refresh_id = 0
            self.hide()
            return False
        if not self._refreshing:
            self._refreshing = True
            scale = max(1, button.get_scale_factor())
            self.plugin.run_in_thread(
                self._refresh_pictures, self._generation, list(self._pictures), scale
            )
        return True

    def _refresh_pictures(self, generation, view_ids, scale):
        from gi.repository import GLib

        try:
            fresh = {}
            for view_id in view_ids:
                texture = self._fetch(view_id, scale)
                if texture is not None:
                    fresh[view_id] = texture
            GLib.idle_add(self._apply_refresh, generation, fresh, scale)
        except Exception as e:
            self.plugin.logger.debug(f"Preview refresh failed: {e}")
            self._refreshing = False

    def _apply_refresh(self, generation, fresh, scale):
        self._refreshing = False
        if generation != self._generation:
            return False
        self._count_frame()
        for view_id, texture in fresh.items():
            picture = self._pictures.get(view_id)
            if picture is not None:
                picture.set_paintable(texture)
                self._fit(picture, texture, scale)
        return False

    def _count_frame(self):
        """Logs the rate the preview really reaches (debug level), for tuning ``fps``."""
        self._frames += 1
        now = time.monotonic()
        if now - self._frames_since >= 2.0:
            self.plugin.logger.debug(
                f"Dock preview: {self._frames / (now - self._frames_since):.1f} fps"
            )
            self._frames = 0
            self._frames_since = now

    # --- closing ---

    def _release_popover(self):
        if self.popover is not None:
            self.popover.popdown()
            self.popover.unparent()
            self.popover = None
        if self.button is not None:
            self.button.set_has_tooltip(True)
            self.button = None
        self.key = None
        self._ids = ()

    def forget(self, widget):
        """A button is being removed: close the preview if it hangs on it."""
        if self.button is widget:
            self.hide()

    def hide(self):
        """Closes the preview and cancels anything still pending."""
        self._cancel_timer("_show_id")
        self._cancel_timer("_hide_id")
        self._cancel_timer("_refresh_id")
        self._generation += 1
        self._refreshing = False
        self._held = False
        try:
            self._clear_effect()
        except Exception as e:
            self.plugin.logger.debug(f"Preview effect reset failed: {e}")
        self._pictures = {}
        self._release_popover()

    def destroy(self):
        """Plugin shutdown."""
        self.hide()
