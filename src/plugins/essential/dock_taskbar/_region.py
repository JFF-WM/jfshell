"""Input region of the panel window, so the compositor blurs only the dock.

jff blurs a layer surface (``blur { only-layer "JFShell-Bottom" }`` or
``all-layers``) over the bounds of its input region, the whole window when the
client sets none. The panel window is bigger than the dock (the side boxes, the
margins and paddings of the panel, the window itself), so the blur showed square
beyond the rounded dock. Setting the input region to the rectangle of the dock
makes that rectangle the blurred area; ``blur { layer-radius N }`` in the jff
config, with N the corner radius of the dock, then rounds its corners.

As a side effect the clicks around the dock go to the windows behind the panel.

On the bottom panel the rectangle is that of ``bottom-panel-box-full``, the one box
of the panel, which is also the box the CSS rounds, so the blur and the visible
shape are the same rectangle. Anywhere else it is the dock itself.
"""

import math


# The one box of the bottom panel (see ``panels/bottom_panel.py``).
PANEL_BOX_CLASS = "bottom-panel-box-full"


def region_rect(bounds, size):
    """The integer rectangle ``(x, y, width, height)`` to take input, or ``None``.

    Args:
        bounds: ``(x, y, width, height)`` of the dock in window coordinates.
        size: ``(width, height)`` of the window. The rectangle never leaves it.

    Rounds outwards, so the dock is never cut by a fraction of a pixel. ``None``
    when nothing of the dock is inside the window (not laid out yet).
    """
    x, y, width, height = bounds
    win_width, win_height = size
    left = max(0, math.floor(x))
    top = max(0, math.floor(y))
    right = min(win_width, math.ceil(x + width))
    bottom = min(win_height, math.ceil(y + height))
    if right <= left or bottom <= top:
        return None
    return left, top, right - left, bottom - top


class DockTaskbarRegion:
    """Keeps the input region of the dock's window on the dock."""

    def __init__(self, plugin_instance, get_dock=None):
        """Initializes the handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
            get_dock: Returns the dock this handler follows. Defaults to the
                plugin's own dock; the copies on other monitors pass theirs.
        """
        self.plugin = plugin_instance
        self._get_dock = get_dock or (lambda: plugin_instance.dock)
        self._map_id = 0
        self._idle_id = 0
        self._surface = None
        self._layout_id = 0
        self._rect = None
        self._failed = False

    def start(self):
        """Follows the dock; the first update waits for it to be on screen."""
        dock = self._get_dock()
        self._map_id = dock.connect("map", lambda *_: self.request())
        if dock.get_mapped():
            self.request()

    def request(self):
        """Updates soon; a burst of changes makes one update."""
        if self._failed or self._idle_id:
            return
        self._idle_id = self.plugin.glib.idle_add(self._run)

    def _run(self):
        self._idle_id = 0
        try:
            self._update()
        except Exception as e:
            # Not worth retrying on every layout: the blur just stays as it was.
            self._failed = True
            self.plugin.logger.error(f"Dock taskbar input region failed: {e}")
        return False

    def _update(self):
        dock = self._get_dock()
        window = dock.get_root()
        surface = window.get_surface() if window is not None else None
        if surface is None:
            return
        self._follow(surface)
        found, rect = self._target().compute_bounds(window)
        if not found:
            return
        region = region_rect(
            (rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()),
            (window.get_width(), window.get_height()),
        )
        if region is None or region == self._rect:
            return
        self._rect = region
        self._set(surface, region)

    def _target(self):
        """The widget whose rectangle takes the input: the panel's box, else the dock."""
        node = self._get_dock().get_parent()
        while node is not None:
            if node.has_css_class(PANEL_BOX_CLASS):
                return node
            node = node.get_parent()
        return self._get_dock()

    def _follow(self, surface):
        """Updates again whenever the window changes size (apps come and go)."""
        if surface is self._surface:
            return
        self._unfollow()
        self._surface = surface
        self._layout_id = surface.connect("layout", lambda *_: self.request())

    def _unfollow(self):
        if self._surface is not None and self._layout_id:
            self._surface.disconnect(self._layout_id)
        self._surface = None
        self._layout_id = 0

    @staticmethod
    def _set(surface, rect):
        import cairo

        x, y, width, height = rect
        surface.set_input_region(cairo.Region(cairo.RectangleInt(x, y, width, height)))

    def stop(self):
        """Gives the whole window its input back and lets go of the signals."""
        if self._idle_id:
            self.plugin.glib.source_remove(self._idle_id)
            self._idle_id = 0
        if self._map_id:
            try:
                self._get_dock().disconnect(self._map_id)
            except Exception:
                pass
            self._map_id = 0
        surface = self._surface
        self._unfollow()
        if surface is not None and self._rect is not None:
            try:
                window = self._get_dock().get_root()
                if window is not None:
                    self._set(surface, (0, 0, window.get_width(), window.get_height()))
            except Exception as e:
                self.plugin.logger.debug(f"Dock taskbar input region reset: {e}")
        self._rect = None
