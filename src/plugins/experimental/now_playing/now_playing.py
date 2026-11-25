def get_plugin_metadata(_):
    """
    Returns the metadata for the Now Playing plugin.
    """
    return {
        "id": "org.jfshell.plugin.now_playing",
        "name": "Media Player",
        "version": "3.5.2",
        "index": 20,
        "description": "Media control page in the clock popover with marquee, pulse effects, window focus, and close action.",
        "container": "background",
        "deps": ["css_generator", "calendar"],
        "enabled": True,
    }


def get_plugin_class():
    """
    Defines and returns the MediaPlugin class with deferred imports.
    """
    import gi
    import gc
    import math
    import time

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk, GLib, GdkPixbuf, Gio, Pango, Gdk
    from src.plugins.core._base import BasePlugin
    from src.shared.dbus_helpers import DbusHelpers
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType
    from ._player_format import (
        estimate_position,
        format_time,
        marquee_offset,
        player_name,
        progress_fraction,
    )

    PAGE_ID = "now_playing"

    class MarqueeLabel(Gtk.ScrolledWindow):
        """
        Fixed width label that scrolls back and forth when its text is too long.

        The width is set once and never depends on the text, so the page does not
        resize while the text moves. The animation only runs while the label is
        on screen.
        """

        SPEED = 28.0
        PAUSE_US = 1_500_000

        def __init__(self, width, css_class):
            super().__init__()
            self._width = width
            self._tick_id = 0
            self._start_us = None
            self.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
            self.set_kinetic_scrolling(False)
            self.set_propagate_natural_height(True)
            self.set_size_request(width, -1)
            self.set_halign(Gtk.Align.CENTER)
            self.label = Gtk.Label()
            self.label.add_css_class(css_class)
            self.label.set_hexpand(True)
            self.label.set_xalign(0.5)
            self.set_child(self.label)
            self.connect("map", lambda *_: self._restart())
            self.connect("unmap", lambda *_: self._stop())

        def set_text(self, text):
            """
            Shows new text and restarts the animation when it does not fit.
            """
            if text == self.label.get_text():
                return
            self.label.set_text(text)
            self._restart()

        def _restart(self):
            self._stop()
            self.get_hadjustment().set_value(0)
            if not self.get_mapped():
                return
            natural = self.label.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            if natural > self._width:
                self._tick_id = self.add_tick_callback(self._on_tick)

        def _stop(self):
            if self._tick_id:
                self.remove_tick_callback(self._tick_id)
                self._tick_id = 0
            self._start_us = None

        def _on_tick(self, _widget, frame_clock):
            now = frame_clock.get_frame_time()
            if self._start_us is None:
                self._start_us = now
            adj = self.get_hadjustment()
            span = adj.get_upper() - adj.get_page_size()
            adj.set_value(
                marquee_offset(now - self._start_us, span, self.SPEED, self.PAUSE_US)
            )
            return True

    class PlayerRow(Gtk.Box):
        """
        UI component representing a single MPRIS player session within the popover.
        """

        def __init__(self, service_name, plugin):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.service_name = service_name
            self.plugin = plugin
            self.last_art_url = None
            self.current_pid = None
            self.current_title = None
            self._progress_timer = None
            self._position = 0
            self._position_at = 0.0
            self._length = 0
            self._playing = False
            self._setup_ui()

        def stop(self):
            """
            Stops the progress timer of this row.
            """
            if self._progress_timer:
                GLib.source_remove(self._progress_timer)
                self._progress_timer = None

        def add_cursor_effect(self, widget):
            """
            Applies interactive hover effects and cursor changes to a widget.
            """
            motion = Gtk.EventControllerMotion()
            widget._cursor_anim_id = None
            widget._watchdog_id = None

            def on_tick(widget, frame_clock):
                duration = 300000.0
                if not hasattr(widget, "_anim_start_time"):
                    return False
                elapsed = frame_clock.get_frame_time() - widget._anim_start_time
                if elapsed > duration:
                    widget.set_opacity(1.0)
                    widget._cursor_anim_id = None
                    return False
                intensity = math.sin((elapsed / duration) * math.pi)
                widget.set_opacity(1.0 - (0.3 * intensity))
                return True

            def handle_event(controller, *args):
                is_enter = len(args) > 1
                if widget._cursor_anim_id is not None:
                    widget.remove_tick_callback(widget._cursor_anim_id)
                    widget._cursor_anim_id = None
                if widget._watchdog_id:
                    GLib.source_remove(widget._watchdog_id)
                    widget._watchdog_id = None

                if is_enter:
                    widget.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
                    clock = widget.get_frame_clock()
                    if clock:
                        widget._anim_start_time = clock.get_frame_time()
                        widget._cursor_anim_id = widget.add_tick_callback(on_tick)
                    widget._watchdog_id = GLib.timeout_add(
                        500,
                        lambda: [
                            widget.set_opacity(1.0),
                            setattr(widget, "_watchdog_id", None),
                            False,
                        ][2],
                    )
                else:
                    widget.set_cursor(None)
                    widget.set_opacity(1.0)

            motion.connect("enter", handle_event)
            motion.connect("leave", handle_event)
            widget.add_controller(motion)

        def _small(self, label):
            """
            Makes a label smaller and dimmer than the title and artist.
            """
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_scale_new(0.85))
            label.set_attributes(attrs)
            label.set_opacity(0.7)

        def _setup_ui(self):
            """
            Constructs the layout for the player row including art, controls, and labels.
            """
            self.add_css_class("player-row")
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            art_size = self.plugin.art_size
            text_width = art_size + 60

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            header.set_valign(Gtk.Align.START)
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            header.append(spacer)

            self.btn_close = Gtk.Button.new_from_icon_name("window-close-symbolic")
            self.btn_close.add_css_class("flat")
            self.btn_close.add_css_class("player-close-btn")
            self.add_cursor_effect(self.btn_close)
            self.btn_close.connect("clicked", self._on_close_clicked)
            header.append(self.btn_close)
            self.append(header)

            self.art_button = Gtk.Button()
            self.art_button.add_css_class("flat")
            self.art_button.add_css_class("player-art-btn")
            self.add_cursor_effect(self.art_button)
            self.art_button.connect("clicked", self._on_art_clicked)

            self.art_image = Gtk.Image()
            self.art_image.set_pixel_size(art_size)
            self.art_image.set_size_request(art_size, art_size)
            self.art_image.add_css_class("player-art")
            self.art_button.set_child(self.art_image)

            art_center_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            art_center_box.set_halign(Gtk.Align.CENTER)
            art_center_box.append(self.art_button)
            self.append(art_center_box)

            info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            info_vbox.set_halign(Gtk.Align.CENTER)

            self.title_label = MarqueeLabel(text_width, "player-title")
            self.title_label.set_text("Unknown Title")
            self.artist_label = MarqueeLabel(text_width, "player-artist")
            self.artist_label.set_text("Unknown Artist")
            self.album_label = MarqueeLabel(text_width, "player-album")
            self._small(self.album_label.label)

            self.progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self.progress_box.set_size_request(text_width, -1)
            self.progress_box.set_halign(Gtk.Align.CENTER)
            self.progress_box.set_visible(False)
            self.time_now = Gtk.Label(label="0:00", xalign=0)
            self.time_total = Gtk.Label(label="0:00", xalign=1)
            for lbl in (self.time_now, self.time_total):
                lbl.add_css_class("player-time")
                lbl.set_width_chars(5)
                self._small(lbl)
            self.progress = Gtk.ProgressBar()
            self.progress.add_css_class("player-progress")
            self.progress.set_hexpand(True)
            self.progress.set_valign(Gtk.Align.CENTER)
            self.progress_box.append(self.time_now)
            self.progress_box.append(self.progress)
            self.progress_box.append(self.time_total)

            controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
            controls_box.set_halign(Gtk.Align.CENTER)
            controls_box.add_css_class("player-controls")

            self.btn_prev = self._create_btn("media-skip-backward-symbolic", "previous")
            self.btn_play = self._create_btn(
                "media-playback-start-symbolic", "play_pause"
            )
            self.btn_next = self._create_btn("media-skip-forward-symbolic", "next")

            for btn in [self.btn_prev, self.btn_play, self.btn_next]:
                controls_box.append(btn)

            info_vbox.append(self.title_label)
            info_vbox.append(self.artist_label)
            info_vbox.append(self.album_label)
            info_vbox.append(self.progress_box)
            info_vbox.append(controls_box)
            self.append(info_vbox)

        def _on_art_clicked(self, _):
            """
            Focuses the window of the media player associated with this row.
            """
            if not self.current_title and not self.current_pid:
                return

            view = self.plugin.compositor_helper.get_view_by_title(self.current_title)
            if not view:
                view = self.plugin.compositor_helper.get_view_by_pid(self.current_pid)

            if view.get("pid") != self.current_pid:
                return

            if view and "id" in view:
                self.plugin.ipc.set_focus(view["id"])
                self.plugin.compositor_helper.move_cursor_middle(view["id"])

        def _on_close_clicked(self, _):
            """
            Closes the application associated with the current media player.
            """
            if not self.current_pid:
                return
            view = self.plugin.compositor_helper.get_view_by_pid(self.current_pid)
            if view and "id" in view:
                self.plugin.ipc.close_window(view["id"])

        def _create_btn(self, icon, action):
            """
            Helper to create an MPRIS control button with localized DBus actions.
            """
            btn = Gtk.Button.new_from_icon_name(icon)
            btn.add_css_class("flat")
            btn.add_css_class("player-control-btn")
            if action == "play_pause":
                btn.add_css_class("player-play-btn")
            self.add_cursor_effect(btn)
            btn.connect(
                "clicked",
                lambda _: self.plugin.run_in_async_task(
                    self.plugin.local_dbus.player_action(self.service_name, action)
                ),
            )
            return btn

        def update_ui(self, data):
            """
            Updates the labels, icons, and album art based on fresh metadata.
            """
            title = data.get("title", "Unknown")
            artist = data.get("artist", "Unknown")
            self.current_pid = data.get("pid")
            self.current_title = title

            self.title_label.set_text(title)
            self.artist_label.set_text(artist)
            album = data.get("album") or ""
            subtitle = " · ".join(
                part for part in (album, player_name(self.service_name)) if part
            )
            self.album_label.set_text(subtitle)
            self.album_label.set_visible(bool(subtitle))

            self._playing = data.get("status") == "Playing"
            icon = (
                "media-playback-playing-symbolic"
                if self._playing
                else "media-playback-start-symbolic"
            )
            self.btn_play.set_icon_name(icon)
            self._update_progress(data.get("position"), data.get("length"))

            art_url = data.get("art_url")
            if art_url and art_url != self.last_art_url:
                self.last_art_url = art_url
                self._load_art_async(art_url)
            elif not art_url:
                app_id = data.get("player", "").split(".")[-1]
                icon_name = (
                    self.plugin.icon_exist(app_id) or "multimedia-audio-player-symbolic"
                )
                self.art_image.set_from_icon_name(icon_name)

        def _update_progress(self, position, length):
            """
            Syncs the progress bar with the player. Without a position or a
            length (live streams) the bar is hidden.
            """
            has_progress = (
                isinstance(position, int)
                and isinstance(length, int)
                and length > 0
            )
            self.progress_box.set_visible(has_progress)
            if not has_progress:
                self.stop()
                return
            self._position = position
            self._length = length
            self._position_at = time.monotonic()
            self.time_total.set_text(format_time(length))
            self._refresh_progress()
            if not self._progress_timer:
                self._progress_timer = GLib.timeout_add(500, self._on_progress_tick)

        def _refresh_progress(self):
            """
            Shows the estimated position between two polls of the player.
            """
            position = estimate_position(
                self._position,
                time.monotonic() - self._position_at,
                self._playing,
                self._length,
            )
            self.time_now.set_text(format_time(position))
            self.progress.set_fraction(progress_fraction(position, self._length))

        def _on_progress_tick(self):
            """
            Keeps the progress bar moving while the page is visible.
            """
            if not self.progress_box.get_visible():
                self._progress_timer = None
                return False
            if self.get_mapped():
                self._refresh_progress()
            return True

        def _load_art_async(self, url):
            """
            Loads album art from a URI asynchronously to avoid blocking the UI thread.
            """

            def _done(s, r):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_stream_at_scale(
                        s.read_finish(r),
                        self.plugin.art_size,
                        self.plugin.art_size,
                        True,
                        None,
                    )
                    self.plugin.schedule_in_gtk_thread(
                        lambda: self.art_image.set_from_pixbuf(pix)
                    )
                except Exception:
                    pass

            try:
                Gio.File.new_for_uri(url).read_async(GLib.PRIORITY_DEFAULT, None, _done)
            except Exception:
                pass

    class MediaPlugin(BasePlugin):
        """
        Main plugin class managing DBus connectivity and MPRIS session tracking.
        """

        def on_start(self):
            """
            Initializes state and starts the asynchronous DBus monitoring loop.
            """
            self.active_rows, self.local_dbus, self.main_button, self._added = (
                {},
                None,
                None,
                False,
            )
            self.calendar = None
            self.max_height = int(
                self.get_plugin_setting_add_hint(
                    ["max_height"],
                    480,
                    "Height in pixels the media page grows to before it scrolls.",
                )
            )
            self.art_size = int(
                self.get_plugin_setting_add_hint(
                    ["art_size"],
                    200,
                    "Size in pixels of the album art on the media page.",
                )
            )
            self.popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.popover_box.add_css_class("player-popover-content")
            self.init_page()
            self.run_in_async_task(self._init_dbus())
            self.plugins["css_generator"].install_css("main.css")

        def init_page(self) -> None:
            """
            Builds the page and adds it to the clock popover's sidebar. Its
            sidebar button stays hidden until a player is active.
            """
            if "calendar" not in self.plugins:
                self.logger.error(
                    "Calendar plugin is not loaded. Cannot add Media Player page."
                )
                return
            self.calendar = self.plugins["calendar"]
            scrolled = Gtk.ScrolledWindow()
            scrolled.add_css_class("player-popover")
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_propagate_natural_height(True)
            scrolled.set_propagate_natural_width(True)
            scrolled.set_max_content_height(self.max_height)
            scrolled.set_child(self.popover_box)
            actual_icon = self.icon_exist(
                "media-playback-playing-symbolic",
                ["media-playback-playing-symbolic"],
            )
            added = self.calendar.add_page(
                PAGE_ID,
                [actual_icon, "multimedia-player-symbolic"],
                "Media Player",
                scrolled,
            )
            if added:
                self.main_button = self.calendar.pages[PAGE_ID]["button"]
                self.main_button.add_css_class("player-trigger")
                self.main_button.set_visible(False)

        async def _init_dbus(self):
            """
            Asynchronous loop that polls for active MPRIS players and filters invalid metadata.
            """
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            self.local_dbus = DbusHelpers(bus)
            while True:
                players = await self.local_dbus.get_active_mpris_players()
                valid = set()
                for p in players:
                    m = await self.local_dbus.get_media_metadata(p)

                    if not m:
                        continue

                    title = m.get("title", "").strip()
                    status = m.get("status", "").strip()

                    if not title or title.lower() == "unknown":
                        continue
                    if not status or status.lower() == "unknown":
                        continue

                    m["pid"] = await self.local_dbus.get_player_pid(p)
                    valid.add(p)
                    self.schedule_in_gtk_thread(self._sync_row, p, m)

                for p in list(self.active_rows.keys()):
                    if p not in valid:
                        self.schedule_in_gtk_thread(self._remove_row, p)

                self.schedule_in_gtk_thread(self._sync_panel)
                await self.asyncio.sleep(1.5)

        def _sync_row(self, p, m):
            """
            Ensures a UI row exists for a valid player and updates its content.
            """
            if p not in self.active_rows or self.active_rows[p].get_parent() is None:
                if p in self.active_rows:
                    self._remove_row(p)

                row = PlayerRow(p, self)
                self.active_rows[p] = row
                self.popover_box.append(row)

            self.active_rows[p].update_ui(m)

        def _remove_row(self, p):
            """
            Removes a player row from the UI and stops its animation timers.
            """
            row = self.active_rows.pop(p, None)
            if row:
                row.stop()
                if row.get_parent() == self.popover_box:
                    self.popover_box.remove(row)
                gc.collect()

        def _sync_panel(self) -> None:
            """
            Shows the page's sidebar button while a player is active and hides
            it when there is none, returning to the calendar if it was open.
            """
            if self.main_button is None:
                return
            has_players = bool(self.active_rows)
            if has_players == self._added:
                return
            self._added = has_players
            self.main_button.set_visible(has_players)
            if not has_players:
                if self.calendar and self.calendar.current_page == PAGE_ID:
                    self.calendar.show_page(self.calendar.CALENDAR_PAGE)
                gc.collect()

        def on_disable(self):
            """
            Cleans up active timers and UI elements when the plugin is stopped.
            """
            for r in self.active_rows.values():
                r.stop()
            self.active_rows.clear()
            if self.calendar:
                self.calendar.remove_page(PAGE_ID)
            self.main_button = None
            self._added = False
            gc.collect()

    return MediaPlugin
