def get_plugin_metadata(panel):
    id = "org.jfshell.plugin.notify_icon"
    container, id = panel.config_handler.get_plugin_container("top-panel-systray", id)
    return {
        "id": id,
        "name": "Notify Icon",
        "version": "1.0.0",
        "deps": ["calendar", "notify_client"],
        "index": 10,
        "container": container,
        "enabled": True,
        "description": (
            "Systray icon that opens the clock popover on the notifications "
            "page. It shows notifications-new-symbolic while there are unseen "
            "notifications, notifications-symbolic otherwise and "
            "notifications-disabled-symbolic in Do Not Disturb."
        ),
    }


def get_plugin_class():
    import os
    import sqlite3
    from gi.repository import Gio, GLib, Gtk
    from src.plugins.core._base import BasePlugin
    from ._icon_state import icon_candidates, resolve_seen, tooltip_text

    PAGE_ID = "notifications"
    DEBOUNCE_MS = 150

    class NotifyIconPlugin(BasePlugin):
        """
        Systray button for the notification history.

        An id is considered seen once the notifications page has been shown in
        the clock popover. Notifications already stored at startup count as
        seen.
        """

        def on_start(self):
            """Create the systray button and start watching the database."""
            self.client = self.plugins["notify_client"]
            self.calendar = self.plugins["calendar"]
            self.db_path = self.path_handler.get_data_path("db/notify/notifications.db")
            self.seen_id = 0
            self.unread = 0
            self.gio_monitor = None
            self.refresh_source = 0

            self.btn = Gtk.Button()
            self.btn.add_css_class("notify-panel-button")
            self.btn.connect("clicked", self.on_clicked)
            self.add_cursor_effect(self.btn)
            self.main_widget = (self.btn, "append")

            self.seen_id = self.query_state()[0]
            self.apply_state()
            self.watch_database()
            self.watch_popover()

        def query_state(self) -> tuple[int, int]:
            """
            Read the newest notification id and the number of unseen ones.

            Returns:
                tuple[int, int]: (latest id, count of ids above the seen marker).
            """
            if not os.path.exists(self.db_path):
                return 0, 0
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(id), 0), "
                    "COUNT(CASE WHEN id > ? THEN 1 END) FROM notifications",
                    (self.seen_id,),
                ).fetchone()
            finally:
                conn.close()
            return int(row[0]), int(row[1])

        def page_visible(self) -> bool:
            """Whether the clock popover currently shows the notifications page."""
            popover = getattr(self.calendar, "popover_calendar", None)
            return bool(
                popover
                and popover.get_visible()
                and self.calendar.current_page == PAGE_ID
            )

        def refresh(self, *_) -> None:
            """Re-read the database and update the icon."""
            try:
                latest, unread = self.query_state()
            except sqlite3.Error as e:
                self.logger.error(f"Error reading notifications for the icon: {e}")
                return
            self.seen_id = resolve_seen(self.seen_id, latest)
            if self.page_visible():
                self.seen_id = latest
                unread = 0
            self.unread = unread
            self.apply_state()

        def apply_state(self) -> None:
            """Set icon, tooltip and style class from the current state."""
            dnd = not self.client.show_messages
            names = list(icon_candidates(self.unread, dnd))
            self.btn.set_icon_name(self.calendar.resolve_icon(names))
            self.btn.set_tooltip_text(tooltip_text(self.unread, dnd))
            if self.unread > 0:
                self.btn.add_css_class("notify-unread")
            else:
                self.btn.remove_css_class("notify-unread")

        def schedule_refresh(self) -> None:
            """Coalesce bursts of file events into a single refresh."""
            if self.refresh_source:
                return
            self.refresh_source = GLib.timeout_add(DEBOUNCE_MS, self.run_scheduled)

        def run_scheduled(self) -> bool:
            """Timer callback of schedule_refresh."""
            self.refresh_source = 0
            self.refresh()
            return GLib.SOURCE_REMOVE

        def on_db_changed(self, _monitor, _file, _other, event_type) -> None:
            """The database file changed on disk."""
            if event_type in (
                Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                Gio.FileMonitorEvent.CHANGED,
                Gio.FileMonitorEvent.MOVED,
                Gio.FileMonitorEvent.CREATED,
                Gio.FileMonitorEvent.DELETED,
            ):
                self.schedule_refresh()

        def watch_database(self) -> None:
            """Monitor the notifications database for new rows."""
            try:
                gio_file = Gio.File.new_for_path(self.db_path)
                self.gio_monitor = gio_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
                self.gio_monitor.connect("changed", self.on_db_changed)
            except GLib.Error as e:
                self.logger.error(f"Error monitoring the notifications database: {e}")

        def watch_popover(self) -> None:
            """Follow the popover, the notifications page and the DND button."""
            popover = getattr(self.calendar, "popover_calendar", None)
            if popover:
                popover.connect("notify::visible", self.refresh)
            page = self.calendar.pages.get(PAGE_ID)
            if page:
                page["button"].connect("toggled", self.refresh)
            dnd_button = getattr(self.client, "dnd_toggle_btn", None)
            if dnd_button:
                dnd_button.connect("clicked", self.refresh)

        def on_clicked(self, *_) -> None:
            """Open the clock popover on the notifications page, or close it
            when that page is already showing."""
            self.calendar.open_page(PAGE_ID)

        def on_disable(self) -> None:
            """Stop the file monitor and any pending refresh."""
            if self.gio_monitor:
                self.gio_monitor.cancel()
                self.gio_monitor = None
            if self.refresh_source:
                GLib.source_remove(self.refresh_source)
                self.refresh_source = 0

    return NotifyIconPlugin
