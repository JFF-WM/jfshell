class TaskbarEvents:
    """Handles compositor and plugin event subscriptions and routing."""

    def __init__(self, plugin_instance):
        """Initializes the event handler.

        Args:
            plugin_instance: The TaskbarPlugin instance.
        """
        from src.shared.overview_drag import OverviewDragHider

        self.plugin = plugin_instance
        self._drag_hider = OverviewDragHider(
            lambda: getattr(self.plugin, "scrolled_window", None),
            lambda: bool(self.plugin.config.hide_on_overview_drag),
        )

    def subscribe(self):
        """Subscribes to all relevant compositor and lifecycle events."""
        mgr = self.plugin.plugins.get("event_manager")
        if not mgr:
            return

        events = [
            "view-focused",
            "view-mapped",
            "view-unmapped",
            "view-app-id-changed",
            "view-title-changed",
            "overview-changed",
            "view-drag-changed",
        ]
        for ev in events:
            mgr.subscribe_to_event(ev, self._handle_view_event, "taskbar")

    def _handle_view_event(self, msg: dict):
        """Routes view events to the appropriate plugin logic."""
        ev = msg.get("event")

        if ev in self._drag_hider.EVENTS:
            self._drag_hider.handle(msg)
            if ev == "view-drag-changed":
                return

        if ev == "overview-changed":
            is_open = msg.get("state", False)
            if is_open:
                for btn in self.plugin.in_use_buttons.values():
                    btn.remove_css_class("focused")

                if (
                    hasattr(self.plugin, "menu_handler")
                    and self.plugin.menu_handler.menu
                ):
                    self.plugin.menu_handler.popdown()
            return

        v = msg.get("view")
        if not v or v.get("id") is None:
            return

        if ev in (
            "view-unmapped",
            "view-mapped",
            "view-title-changed",
            "view-app-id-changed",
        ):
            if not self.plugin._debounce_pending:
                from gi.repository import GLib

                self.plugin._debounce_pending = True
                self.plugin._debounce_timer_id = GLib.timeout_add(
                    self.plugin._debounce_interval, self.plugin.Taskbar
                )
        elif ev == "view-focused":
            # Correct reference to the view engine instance
            self.plugin.view_handler.handle_focus_change(v)
