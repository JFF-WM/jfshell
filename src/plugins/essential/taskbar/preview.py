import time


class TaskbarPreview:
    """Hover preview for taskbar buttons.

    Hovering a button for a moment opens a popover with a picture of the
    window behind it (every window of the group when apps are grouped).
    The pictures come from jff's ``window-thumbnail`` IPC command and are
    redrawn while the popover is open, at up to ``preview.fps`` frames per second
    (a frame is skipped when the previous one is still being fetched, so a slow
    compositor lowers the rate instead of piling up requests). All IPC and image decoding runs on
    worker threads, so hovering never blocks the panel.
    """

    # Wait before a preview opens in place of another one (moving along the bar).
    SWITCH_DELAY_MS = 60
    # Wait before a preview closes, so a move to the next button does not flicker.
    LEAVE_DELAY_MS = 150
    # Same, when the cards can be clicked: time to cross the gap onto the popover.
    LEAVE_DELAY_CLICKABLE_MS = 350
    # Where the popover sits relative to the button, per panel position.
    POSITIONS = {"bottom": "TOP", "top": "BOTTOM", "left": "RIGHT", "right": "LEFT"}

    def __init__(self, plugin_instance):
        """Initializes the preview handler.

        Args:
            plugin_instance: The TaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.popover = None
        self.button = None
        self._show_id = 0
        self._hide_id = 0
        self._refresh_id = 0
        # Bumped whenever the preview is cancelled, so late worker results are dropped.
        self._generation = 0
        self._refreshing = False
        self._frames = 0
        self._frames_since = 0.0
        # Whether the open popover takes clicks (grouped windows).
        self._clickable = False
        # view id -> Gtk.Picture currently shown, for the refresh.
        self._pictures = {}

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

    def _identifier_of(self, button):
        """The in_use_buttons key of a button (app id when grouped, else window id)."""
        return next(
            (k for k, v in self.plugin.in_use_buttons.items() if v == button), None
        )

    # --- hover entry points ---

    def on_enter(self, button):
        """Pointer entered a button: open its preview after a short wait."""
        from gi.repository import GLib

        if not self._enabled() or getattr(button, "view_id", None) is None:
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
        """Pointer left a button: close its preview shortly, or drop a pending one."""
        from gi.repository import GLib

        self._cancel_timer("_show_id")
        if self.is_visible() and self.button is button:
            self._cancel_timer("_hide_id")
            self._schedule_hide()
        elif self.button is None or self.button is button:
            # Nothing on screen yet: cancel a load that is still running.
            self._generation += 1

    def _schedule_hide(self):
        from gi.repository import GLib

        self._cancel_timer("_hide_id")
        delay = self.LEAVE_DELAY_CLICKABLE_MS if self._clickable else self.LEAVE_DELAY_MS
        self._hide_id = GLib.timeout_add(delay, self._hide_later)

    def _hide_later(self):
        self._hide_id = 0
        self.hide()
        return False

    # --- loading (worker thread) ---

    def _views_for(self, identifier):
        """The windows a button stands for, limited to the configured count."""
        plugin = self.plugin
        views = [
            v for v in plugin.ipc.list_windows() if plugin.view_handler.is_valid_view(v)
        ]
        if plugin.group_apps:
            views = [
                v for v in views if (v.get("app_id") or v.get("app-id")) == identifier
            ]
        else:
            views = [v for v in views if v.get("id") == identifier]
        return views[: max(1, int(plugin.config.preview_max_windows))]

    def _fetch(self, view_id, scale):
        """A texture of one window, or None. Runs on a worker thread.

        The picture is decoded into memory and its file is deleted right away,
        so no PNG outlives the frame it carries (also when the preview was
        closed while the request was running).
        """
        import os

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
        self._generation += 1
        generation = self._generation
        identifier = self._identifier_of(button)
        if identifier is None:
            return False
        scale = max(1, button.get_scale_factor())
        self.plugin.run_in_thread(self._load, button, identifier, generation, scale)
        return False

    def _load(self, button, identifier, generation, scale):
        from gi.repository import GLib

        try:
            cards = []
            for view in self._views_for(identifier):
                texture = self._fetch(view.get("id"), scale)
                if texture is not None:
                    cards.append((view, texture))
        except Exception as e:
            self.plugin.logger.debug(f"Preview load failed: {e}")
            return
        if cards:
            GLib.idle_add(self._present, button, generation, cards, scale)

    # --- showing (GTK thread) ---

    def _present(self, button, generation, cards, scale):
        """Builds and opens the popover once the pictures are ready."""
        from gi.repository import Gtk, Pango

        if generation != self._generation or getattr(button, "view_id", None) is None:
            return False

        self._release_popover()
        self._pictures = {}
        # With several windows in a group the button opens a list instead of
        # focusing, so the cards themselves are the shortcut.
        self._clickable = bool(self.plugin.group_apps and len(cards) > 1)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for view, texture in cards:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.add_css_class("taskbar-preview-card")
            if self._clickable:
                card.add_css_class("clickable")
                card.set_cursor_from_name("pointer")
                click = Gtk.GestureClick(button=1)
                click.connect("released", self._on_card_clicked, view)
                card.add_controller(click)
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_can_shrink(False)
            picture.set_halign(Gtk.Align.CENTER)
            picture.add_css_class("taskbar-preview-picture")
            self._fit(picture, texture, scale)
            title = Gtk.Label(label=view.get("title", "") or "")
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(28)
            title.add_css_class("taskbar-preview-title")
            card.append(picture)
            card.append(title)
            row.append(card)
            self._pictures[view.get("id")] = picture

        popover = Gtk.Popover()
        popover.add_css_class("taskbar-preview")
        popover.set_has_arrow(False)
        popover.set_autohide(False)
        popover.set_can_target(self._clickable)
        popover.set_focusable(False)
        if self._clickable:
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
        # The title preview replaces the tooltip while it is open.
        button.set_has_tooltip(False)
        popover.popup()
        self._start_refresh()
        return False

    def _on_card_clicked(self, gesture, n_press, x, y, view):
        """Focuses the window of a clicked card and closes the preview."""
        self.hide()
        self.plugin.view_handler.set_view_focus(view)

    def _position(self):
        from gi.repository import Gtk

        name = self.POSITIONS.get(self.plugin.config.panel_position, "TOP")
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
        if fps > 0:
            self._refresh_id = GLib.timeout_add(max(1, round(1000 / fps)), self._refresh)

    def _refresh(self):
        """Timer callback: redraw the open preview, or close it if its button is gone."""
        button = self.button
        if (
            not self.is_visible()
            or button is None
            or not button.get_visible()
            or button not in self.plugin.in_use_buttons.values()
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
                f"Taskbar preview: {self._frames / (now - self._frames_since):.1f} fps"
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

    def hide(self):
        """Closes the preview and cancels anything still pending."""
        self._cancel_timer("_show_id")
        self._cancel_timer("_hide_id")
        self._cancel_timer("_refresh_id")
        self._generation += 1
        self._refreshing = False
        self._clickable = False
        self._pictures = {}
        self._release_popover()

    def destroy(self):
        """Plugin shutdown."""
        self.hide()
