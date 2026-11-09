"""Hide a panel while a window is dragged from the overview."""

from typing import Any, Callable, Optional


class OverviewDragHider:
    """Hides the panel a widget lives in while a window is dragged from the
    overview.

    Feed it the ``overview-changed`` and ``view-drag-changed`` events (see
    ``EVENTS``). A drag that starts with the overview open hides the panel
    window, a titlebar drag on the desktop doesn't, and letting go (or the
    overview closing) always shows it again.

    The whole panel window is hidden (``set_visible(False)``), not just the
    widget: that unmaps its layer-shell surface, so the compositor sees no
    layer at all (no background, border, blur or exclusive zone). A panel that
    was already hidden before the drag is left hidden afterwards.
    """

    EVENTS = ("overview-changed", "view-drag-changed")

    def __init__(
        self,
        get_widget: Callable[[], Optional[Any]],
        is_enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        self._get_widget = get_widget
        self._is_enabled = is_enabled
        self._overview_open = False
        self._hidden = False

    def subscribe(self, event_manager: Any, plugin_name: str) -> None:
        for ev in self.EVENTS:
            event_manager.subscribe_to_event(ev, self.handle, plugin_name)

    def handle(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "overview-changed":
            self._overview_open = bool(msg.get("state", False))
            if not self._overview_open:
                # A drag ends with the overview, don't leave the widget hidden.
                self.set_hidden(False)
        elif ev == "view-drag-changed":
            dragging = bool(msg.get("state", False))
            from_overview = bool(msg.get("overview", self._overview_open))
            self.set_hidden(dragging and from_overview and self._is_enabled())

    def _panel_window(self) -> Optional[Any]:
        """The window holding the widget (the panel's layer-shell window)."""
        widget = self._get_widget()
        if widget is None:
            return None
        root = widget.get_root()
        return root if root is not None else widget

    def set_hidden(self, hidden: bool) -> None:
        if hidden == self._hidden:
            return
        window = self._panel_window()
        if window is None:
            return
        if hidden and not window.get_visible():
            # Not ours to hide, and not ours to show again later.
            return
        self._hidden = hidden
        if hidden:
            window.set_visible(False)
        elif hasattr(window, "present"):
            window.present()
        else:
            window.set_visible(True)
