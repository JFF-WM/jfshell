"""Compositor event subscriptions of the dock taskbar."""


class DockTaskbarEvents:
    """Routes compositor events to a debounced sync of the bar."""

    NAME = "dock_taskbar"
    # Events that change what the bar shows.
    SYNC_EVENTS = (
        "view-focused",
        "view-mapped",
        "view-unmapped",
        "view-app-id-changed",
        "view-state-changed",
    )

    def __init__(self, plugin_instance):
        """Initializes the event handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
        """
        from src.shared.overview_drag import OverviewDragHider

        self.plugin = plugin_instance
        self._subscribed = []
        self._drag_hider = OverviewDragHider(
            lambda: getattr(self.plugin, "dock", None),
            lambda: bool(self.plugin.config.hide_on_overview_drag),
        )

    def subscribe(self):
        """Subscribes to every event the bar reacts to."""
        mgr = self.plugin.plugins.get("event_manager")
        if not mgr:
            return
        for ev in (*self.SYNC_EVENTS, *self._drag_hider.EVENTS):
            mgr.subscribe_to_event(ev, self._handle_event, self.NAME)
            self._subscribed.append(ev)

    def unsubscribe(self):
        """Removes the subscriptions (plugin disabled)."""
        mgr = self.plugin.plugins.get("event_manager")
        if mgr:
            for ev in self._subscribed:
                mgr.unsubscribe_from_event(ev, self._handle_event)
        self._subscribed = []

    def _handle_event(self, msg: dict):
        """Routes one compositor event."""
        ev = msg.get("event")

        if ev in self._drag_hider.EVENTS:
            self._drag_hider.handle(msg)
            if ev == "view-drag-changed":
                return

        if ev == "overview-changed":
            if msg.get("state", False):
                # The overview shows its own windows: no thumbnails, no focus mark.
                self.plugin.preview.hide()
                self.plugin.menu.popdown()
                for widget in self.plugin.widgets.values():
                    widget.remove_css_class("focused")
                    # Forget the cached state, or the class would never come back.
                    widget._dock_state = None
            else:
                self.plugin.schedule_sync()
            return

        view = msg.get("view")
        if ev in self.SYNC_EVENTS and view and view.get("id") is not None:
            self.plugin.schedule_sync()
