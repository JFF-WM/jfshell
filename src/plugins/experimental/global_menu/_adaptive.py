"""
Responsive container of the global menu bar (GTK side, no D-Bus).

AdaptiveMenuBar holds two children, the row of top level buttons (``full``) and one
Menu button (``compact``), and shows whichever the width it is given allows.

* Natural width is the width of the full row, so a panel with room shows every entry.
* Minimum width is the width of the Menu button (unless the mode is ``full``), so a
  wide menu can no longer force the panel layout wider than the screen.
* The decision is taken in ``size_allocate`` from the width actually granted, which
  makes it follow the panel size, the clock and the tray without any polling.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # pyright: ignore  # noqa: E402

from ._menu_model import normalize_mode, use_compact  # noqa: E402


class AdaptiveMenuBar(Gtk.Widget):
    """Shows ``full`` when it fits the allocated width, ``compact`` otherwise."""

    def __init__(
        self, full: Gtk.Widget, compact: Gtk.Widget, mode: str = "auto"
    ) -> None:
        super().__init__()
        self._full = full
        self._compact = compact
        self._mode = normalize_mode(mode)
        full.set_parent(self)
        compact.set_parent(self)

    def do_measure(self, orientation, for_size):  # pyright: ignore
        full_min, full_nat, _, _ = self._full.measure(orientation, -1)
        compact_min, compact_nat, _, _ = self._compact.measure(orientation, -1)
        if orientation == Gtk.Orientation.VERTICAL:
            return max(full_min, compact_min), max(full_nat, compact_nat), -1, -1
        minimum = full_min if self._mode == "full" else compact_min
        natural = compact_nat if self._mode == "compact" else full_nat
        return minimum, max(minimum, natural), -1, -1

    def do_size_allocate(self, width, height, baseline):  # pyright: ignore
        _, full_nat, _, _ = self._full.measure(Gtk.Orientation.HORIZONTAL, -1)
        compact = use_compact(self._mode, width, full_nat)
        # set_child_visible is the container-side switch meant for size_allocate.
        self._full.set_child_visible(not compact)
        self._compact.set_child_visible(compact)
        child = self._compact if compact else self._full
        child.allocate(width, height, baseline, None)

    def do_dispose(self):  # pyright: ignore
        for child in (self._full, self._compact):
            if child.get_parent() is not None:
                child.unparent()
        Gtk.Widget.do_dispose(self)
