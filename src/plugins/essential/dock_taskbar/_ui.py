"""Widgets of the dock taskbar: the bar, its icon buttons and the window dots."""

import os
from typing import Any

from ._model import state_flags


class DockBar:
    """The bar of a copy of the panel on another monitor.

    The bar of the main panel window is the plugin itself; it has the same
    attributes, so the UI code treats both alike.
    """

    def __init__(self, output: str) -> None:
        self.output = output
        self.dock: Any = None
        self.widgets: dict = {}
        self.launcher_button: Any = None
        self.region: Any = None
        # The revealer of the hidden launchers (see DockTaskbarUI.create_layout).
        self.stash_group: Any = None
        self.stash_toggle: Any = None
        self.stash_revealer: Any = None
        self.stash_box: Any = None


class DockTaskbarUI:
    """Builds the bar and keeps its buttons in step with the entries."""

    # Built-in look of the badges (audio, always on top, sticky). Each one brings
    # its own background and ring, so the white glyph stays readable on light
    # icons even without dock_taskbar.css, which can restyle them.
    #
    # The state badges are 12px discs with no padding on purpose: two of them
    # side by side take 24px, which leaves the top-right corner of a 40px icon
    # (the default icon_size) to the 16px audio badge.
    #
    # The same provider carries the look of an icon being dragged.
    # How long the revealer of the hidden launchers takes to open or close.
    STASH_TRANSITION_MS = 200

    BADGE_CSS = """
.dock-taskbar-audio {
  padding: 2px;
  border-radius: 50%;
  color: #ffffff;
  background-color: #3584e4;
  background-color: var(--theme-status-accent, #3584e4);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.85), 0 1px 3px rgba(0, 0, 0, 0.5);
}

.dock-taskbar-audio.muted {
  color: rgba(255, 255, 255, 0.8);
  background-color: #3a3a3a;
}

.dock-taskbar-state {
  padding: 0;
  border-radius: 50%;
  color: #ffffff;
  background-color: #77767b;
  background-color: var(--theme-state-badge, #77767b);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.85), 0 1px 3px rgba(0, 0, 0, 0.5);
}

.dock-taskbar-button.dragging {
  opacity: 0.7;
}
"""

    def __init__(self, plugin_instance):
        """Initializes the UI handler.

        Args:
            plugin_instance: The DockTaskbarPlugin instance.
        """
        self.plugin = plugin_instance
        self.config = plugin_instance.config
        self._badge_provider = None
        # The icon drag in progress (see _on_drag_begin), or None.
        self._drag = None

    # --- layout ---

    def create_layout(self, bar=None):
        """Creates the box that holds every button (``bar.dock``).

        Args:
            bar: The bar to build (``dock``, ``widgets`` and ``launcher_button``
                are its attributes). Defaults to the plugin itself, which is the
                bar of the main panel window; the others are the copies on the
                other monitors.
        """
        from gi.repository import Gtk

        bar = bar or self.plugin
        bar.dock = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=self.config.spacing
        )
        bar.dock.add_css_class("dock-taskbar")
        bar.dock.set_halign(Gtk.Align.CENTER)
        bar.dock.set_valign(Gtk.Align.CENTER)
        self._create_stash(bar)
        self._install_badge_css()

    def _create_stash(self, bar):
        """Builds the revealer for the hidden launchers: a toggle button and the box.

        Launchers set to hidden (and not running) live in ``bar.stash_box``
        instead of ``bar.dock``. The group is only visible while it holds one.
        """
        from gi.repository import Gtk

        bar.stash_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=self.config.spacing
        )
        bar.stash_box.add_css_class("dock-taskbar-stash")
        bar.stash_box.set_margin_start(self.config.spacing)
        bar.stash_box.set_valign(Gtk.Align.CENTER)

        bar.stash_revealer = Gtk.Revealer()
        bar.stash_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        bar.stash_revealer.set_transition_duration(self.STASH_TRANSITION_MS)
        bar.stash_revealer.set_reveal_child(False)
        bar.stash_revealer.set_child(bar.stash_box)

        bar.stash_toggle = Gtk.Button.new_from_icon_name("pan-end-symbolic")
        bar.stash_toggle.add_css_class("flat")
        bar.stash_toggle.add_css_class("dock-taskbar-stash-toggle")
        bar.stash_toggle.set_valign(Gtk.Align.CENTER)
        bar.stash_toggle.connect("clicked", lambda _b: self._toggle_stash(bar))
        self.plugin.gtk_helper.add_cursor_effect(bar.stash_toggle)

        # One child of the dock, so the spacing of the dock leaves no gap when
        # the revealer is closed.
        bar.stash_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.stash_group.set_valign(Gtk.Align.CENTER)
        bar.stash_group.set_visible(False)
        bar.stash_group.append(bar.stash_toggle)
        bar.stash_group.append(bar.stash_revealer)
        bar.dock.append(bar.stash_group)

    def _toggle_stash(self, bar):
        """Opens or closes the revealer of the hidden launchers."""
        self.plugin.preview.hide()
        revealer = bar.stash_revealer
        revealer.set_reveal_child(not revealer.get_reveal_child())
        self._set_stash_icon(bar)

    def close_stash(self):
        """Closes the revealer of the hidden launchers on every bar."""
        for bar in self.plugin.all_bars():
            if bar.stash_revealer.get_reveal_child():
                bar.stash_revealer.set_reveal_child(False)
                self._set_stash_icon(bar)

    @staticmethod
    def _set_stash_icon(bar):
        opened = bar.stash_revealer.get_reveal_child()
        bar.stash_toggle.set_icon_name(
            "pan-start-symbolic" if opened else "pan-end-symbolic"
        )

    def attach_launcher(self, bar=None):
        """Pins the app launcher button as the first, fixed item of the bar.

        The button gets the same layout as the app buttons (icon plus the dots
        row), so its icon lines up with the others. The main bar uses the
        launcher's own button; the copies on other monitors get a button of
        their own that opens the same popover.
        """
        from gi.repository import Gtk

        p = self.plugin
        bar = bar or p
        bar.launcher_button = None
        if "app_launcher" not in p.plugins:
            return
        if bar is p:
            button = p.plugins["app_launcher"].appmenu
            # Setting the child below clears the icon name of the button.
            p.launcher_icon = button.get_icon_name() or "start-here"
        else:
            button = Gtk.Button()
            button.add_css_class("app-launcher-menu-button")
            button.connect("clicked", lambda b: p.open_launcher(b))
            p.gtk_helper.add_cursor_effect(button)
        icon_name = p.launcher_icon
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(self.config.icon_size)
        dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        dots.add_css_class("dock-taskbar-dots")
        dots.set_halign(Gtk.Align.CENTER)
        dots.set_valign(Gtk.Align.CENTER)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        if p.position == "bottom":
            inner.append(image)
            inner.append(dots)
        else:
            inner.append(dots)
            inner.append(image)
        button.set_child(inner)
        button.add_css_class("dock-taskbar-button")
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.CENTER)
        button.set_hexpand(False)
        button.set_vexpand(False)
        # First child: the revealer was added to the dock when the layout was built.
        bar.dock.prepend(button)
        bar.launcher_button = button

    def _install_badge_css(self):
        """Registers the built-in badge style once for the whole display."""
        from gi.repository import Gdk, Gtk

        display = Gdk.Display.get_default()
        if display is None or self._badge_provider is not None:
            return
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(self.BADGE_CSS)
        else:
            provider.load_from_data(self.BADGE_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._badge_provider = provider

    def remove_badge_css(self):
        """Unregisters the built-in badge style (plugin disabled)."""
        from gi.repository import Gdk, Gtk

        display = Gdk.Display.get_default()
        if display is not None and self._badge_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(display, self._badge_provider)
        self._badge_provider = None

    # --- widget factories ---

    def create_button(self, entry):
        """An icon button: the icon plus a row/column of window dots on the screen edge."""
        from gi.repository import Gtk

        p = self.plugin
        button = Gtk.Button()
        button.add_css_class("dock-taskbar-button")
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.CENTER)
        button.set_hexpand(False)
        button.set_vexpand(False)
        button.entry_key = entry.key  # pyright: ignore

        button.icon = Gtk.Image()  # pyright: ignore
        button.icon.set_pixel_size(self.config.icon_size)  # pyright: ignore
        button.dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)  # pyright: ignore
        button.dots.add_css_class("dock-taskbar-dots")  # pyright: ignore
        button.dots.set_halign(Gtk.Align.CENTER)  # pyright: ignore
        button.dots.set_valign(Gtk.Align.CENTER)  # pyright: ignore
        button.dot_widgets = []  # pyright: ignore

        # The dots sit on the side of the screen edge the panel is on.
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # A small speaker badge sits in the corner of the icon while its app
        # plays sound (see set_audio).
        badge = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        badge.set_pixel_size(12)
        badge.add_css_class("dock-taskbar-audio")
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.START)
        badge.set_can_target(False)
        badge.set_visible(False)
        button.badge = badge  # pyright: ignore
        overlay = Gtk.Overlay()
        overlay.set_child(button.icon)  # pyright: ignore
        overlay.add_overlay(badge)

        # The always-on-top and sticky badges share one box in the opposite
        # (top-left) corner, so they can show together with the speaker badge
        # (see set_state_badges). Like it, they never take clicks.
        state_badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        state_badges.set_halign(Gtk.Align.START)
        state_badges.set_valign(Gtk.Align.START)
        state_badges.set_can_target(False)
        state_badges.set_visible(False)
        button.state_badges = state_badges  # pyright: ignore
        for attr, icon_name, css in (
            ("badge_atop", "go-top-symbolic", "dock-taskbar-badge-atop"),
            ("badge_sticky", "pin-symbolic", "dock-taskbar-badge-sticky"),
        ):
            image = Gtk.Image.new_from_icon_name(icon_name)
            image.set_pixel_size(12)
            image.add_css_class("dock-taskbar-state")
            image.add_css_class(css)
            image.set_can_target(False)
            image.set_visible(False)
            setattr(button, attr, image)
            state_badges.append(image)
        overlay.add_overlay(state_badges)

        if p.position == "bottom":
            inner.append(overlay)
            inner.append(button.dots)  # pyright: ignore
        else:
            inner.append(button.dots)  # pyright: ignore
            inner.append(overlay)
        button.set_child(inner)

        button.connect("clicked", self._on_clicked)
        self._add_gestures(button)
        self._add_drag(button)
        p.gtk_helper.add_cursor_effect(button)
        return button

    def create_separator(self):
        """A thin line between the pinned launchers and the other apps."""
        from gi.repository import Gtk

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.add_css_class("dock-taskbar-separator")
        return sep

    def _add_gestures(self, button):
        """Right click (menu), middle click (new window), scroll (cycle) and hover."""
        from gi.repository import Gtk

        right = Gtk.GestureClick(button=3)
        right.connect("pressed", self._on_right_click)
        button.add_controller(right)

        middle = Gtk.GestureClick(button=2)
        middle.connect("pressed", self._on_middle_click)
        button.add_controller(middle)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_hover_enter)
        motion.connect("leave", self._on_hover_leave)
        button.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        button.add_controller(scroll)

    # --- gesture callbacks ---

    def _on_clicked(self, button):
        self.plugin.preview.hide()
        self.plugin.activate(button.entry_key)

    def _on_right_click(self, gesture, n_press, x, y):
        self.plugin.preview.hide()
        button = gesture.get_widget()
        self.plugin.menu.show_app(
            button, x, y, self.plugin.entry_map.get(button.entry_key)
        )

    def _on_middle_click(self, gesture, n_press, x, y):
        self.plugin.preview.hide()
        entry = self.plugin.entry_map.get(gesture.get_widget().entry_key)
        if entry is not None:
            self.plugin.launch(entry)

    def _on_scroll(self, controller, dx, dy):
        self.plugin.preview.hide()
        key = controller.get_widget().entry_key
        # dy > 0 is scroll down (next window), dy < 0 is scroll up (previous).
        return bool(self.plugin.cycle(key, 1 if dy > 0 else -1))

    def _on_hover_enter(self, controller, x, y):
        if self.dragging:
            return
        self.plugin.preview.on_enter(controller.get_widget())

    def _on_hover_leave(self, controller):
        self.plugin.preview.on_leave(controller.get_widget())

    # --- drag and drop ---
    #
    # The drag never leaves the panel, so it is done with a plain GestureDrag
    # instead of Wayland drag and drop: no data offer, no compositor support
    # needed. The button follows the pointer along the bar while the mouse is
    # held, and the new place is committed on release.

    @property
    def dragging(self):
        """Whether an icon is being dragged right now."""
        return self._drag is not None and self._drag["active"]

    def _add_drag(self, button):
        """Lets an icon be dragged to another place on its bar."""
        from gi.repository import Gtk

        gesture = Gtk.GestureDrag()  # primary button
        gesture.connect("drag-begin", self._on_drag_begin)
        gesture.connect("drag-update", self._on_drag_update)
        gesture.connect("drag-end", self._on_drag_end)
        gesture.connect("cancel", self._on_drag_cancel)
        button.add_controller(gesture)

    @staticmethod
    def _drag_threshold():
        from gi.repository import Gtk

        try:
            return max(1, int(Gtk.Settings.get_default().props.gtk_dnd_drag_threshold))
        except Exception:
            return 8

    def _bar_of(self, widget):
        parent = widget.get_parent()
        return next((b for b in self.plugin.all_bars() if b.dock is parent), None)

    def _on_drag_begin(self, gesture, x, y):
        button = gesture.get_widget()
        bar = self._bar_of(button)
        if bar is None or button.entry_key not in self.plugin.entry_map:
            self._drag = None
            return
        # Nothing happens until the pointer moves past the threshold, so a
        # plain click still reaches the button.
        self._drag = {
            "button": button,
            "bar": bar,
            "start_x": x,
            "start_y": y,
            "active": False,
        }

    def _on_drag_update(self, gesture, off_x, off_y):
        from gi.repository import Gtk

        drag = self._drag
        if drag is None or drag["button"] is not gesture.get_widget():
            return
        button, bar = drag["button"], drag["bar"]
        if not drag["active"]:
            if abs(off_x) < self._drag_threshold() and abs(off_y) < self._drag_threshold():
                return
            # Claiming the sequence cancels the button's own click.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            drag["active"] = True
            self.plugin.preview.hide()
            self.plugin.menu.popdown()
            button.add_css_class("dragging")
        # start + offset is the pointer in the button's current coordinates,
        # even after the button has moved.
        ok, rect = button.compute_bounds(bar.dock)
        if ok:
            self._move_to(bar, button, rect.get_x() + drag["start_x"] + off_x)

    @staticmethod
    def _move_to(bar, button, x):
        """Puts ``button`` in the gap of the bar nearest to ``x``.

        The gap is before the first other item whose middle is right of the
        pointer (or after the last item). The launcher button stays first.
        """
        previous = bar.launcher_button
        child = bar.dock.get_first_child()
        while child is not None:
            if (
                child is not button
                and child is not bar.launcher_button
                and child is not bar.stash_group
            ):
                ok, rect = child.compute_bounds(bar.dock)
                if ok and x < rect.get_x() + rect.get_width() / 2:
                    break
                previous = child
            child = child.get_next_sibling()
        if button.get_prev_sibling() is not previous:
            bar.dock.reorder_child_after(button, previous)

    def _on_drag_end(self, gesture, off_x, off_y):
        drag, self._drag = self._drag, None
        if drag is None or not drag["active"]:
            return
        button, bar = drag["button"], drag["bar"]
        button.remove_css_class("dragging")
        if button.get_parent() is not bar.dock:
            return
        keys = {id(w): k for k, w in bar.widgets.items()}
        nxt = button.get_next_sibling()
        prev = button.get_prev_sibling()
        if nxt is not None and id(nxt) in keys:
            target, after = keys[id(nxt)], False
        elif prev is not None and id(prev) in keys:
            target, after = keys[id(prev)], True
        else:
            return
        # Later: the move rebuilds buttons, maybe this one too.
        self.plugin.glib.idle_add(
            self.plugin.move_entry, button.entry_key, target, after
        )

    def _on_drag_cancel(self, gesture, sequence):
        drag = self._drag
        if drag is None or drag["button"] is not gesture.get_widget():
            return
        self._drag = None
        if drag["active"]:
            drag["button"].remove_css_class("dragging")
            # Put the bar back the way the entries say.
            self.plugin.glib.idle_add(self._resync)

    def _resync(self):
        self.plugin.sync()
        return False

    # --- syncing ---

    def apply(self, entries, bar=None):
        """Creates, updates, removes and orders the widgets so they mirror ``entries``.

        Args:
            entries: What the bar shows.
            bar: The bar to update (defaults to the plugin's own).
        """
        bar = bar or self.plugin
        wanted = {e.key for e in entries}
        for key in [k for k in bar.widgets if k not in wanted]:
            self._remove(key, bar)

        desired = []
        stashed = []
        for entry in entries:
            widget = bar.widgets.get(entry.key)
            if widget is None:
                widget = (
                    self.create_separator()
                    if entry.kind == "separator"
                    else self.create_button(entry)
                )
                bar.widgets[entry.key] = widget
            # A hidden launcher without windows lives in the revealer.
            self._place(widget, bar.stash_box if entry.stashed else bar.dock)
            if entry.kind != "separator":
                self.update_button(widget, entry)
            (stashed if entry.stashed else desired).append(widget)

        current = []
        child = bar.dock.get_first_child()
        while child is not None:
            if child is not bar.launcher_button and child is not bar.stash_group:
                current.append(child)
            child = child.get_next_sibling()
        # A dragged button follows the pointer; the drop decides its place.
        if not self.dragging:
            if current != desired:
                # The launcher button stays first.
                previous = bar.launcher_button
                for widget in desired:
                    bar.dock.reorder_child_after(widget, previous)
                    previous = widget
            # The revealer stays last.
            last = desired[-1] if desired else bar.launcher_button
            if bar.stash_group.get_prev_sibling() is not last:
                bar.dock.reorder_child_after(bar.stash_group, last)

        self._apply_stash(bar, stashed)

    def _place(self, widget, home):
        """Puts a button into ``home`` (the dock or the revealer) when it is elsewhere."""
        parent = widget.get_parent()
        if parent is home:
            return
        if parent is not None:
            # Popovers parented to the widget have to go first, or GTK complains.
            self.plugin.preview.forget(widget)
            self.plugin.menu.forget(widget)
            parent.remove(widget)
        home.append(widget)

    def _apply_stash(self, bar, stashed):
        """Orders the revealer's buttons and shows its toggle only when it has any."""
        current = []
        child = bar.stash_box.get_first_child()
        while child is not None:
            current.append(child)
            child = child.get_next_sibling()
        if current != stashed:
            previous = None
            for widget in stashed:
                bar.stash_box.reorder_child_after(widget, previous)
                previous = widget
        bar.stash_group.set_visible(bool(stashed))
        bar.stash_toggle.set_tooltip_text(f"Hidden apps ({len(stashed)})")
        if not stashed and bar.stash_revealer.get_reveal_child():
            bar.stash_revealer.set_reveal_child(False)
            self._set_stash_icon(bar)

    def clear(self, bar):
        """Removes every button of a bar (it is going away)."""
        for key in list(bar.widgets):
            self._remove(key, bar)

    def _remove(self, key, bar=None):
        p = self.plugin
        bar = bar or p
        widget = bar.widgets.pop(key, None)
        if widget is None:
            return
        # Popovers parented to the widget have to go first, or GTK complains.
        p.preview.forget(widget)
        p.menu.forget(widget)
        if self._drag is not None and self._drag["button"] is widget:
            self._drag = None
        parent = widget.get_parent()
        if parent is not None:
            parent.remove(widget)

    def update_button(self, button, entry):
        """Writes the visible state of an entry to its button, skipping no-op updates."""
        p = self.plugin
        icon = p.icon_for(entry)
        name = p.name_for(entry)
        count = len(entry.views)
        dots = (
            min(count, self.config.max_indicators) if self.config.show_indicators else 0
        )
        # Derived from the windows already listed for this sync: no extra IPC.
        atop, sticky = (
            state_flags(entry.views)
            if self.config.show_state_badges
            else (False, False)
        )
        state = (
            icon,
            self.config.icon_size,
            name,
            count,
            dots,
            entry.focused,
            entry.pinned,
            atop,
            sticky,
        )
        if getattr(button, "_dock_state", None) == state:
            return
        button._dock_state = state  # pyright: ignore

        button.entry_key = entry.key  # pyright: ignore
        self._set_icon(button.icon, icon, self.config.icon_size)  # pyright: ignore
        # The thumbnails replace the tooltip while they are open (see preview).
        button.set_tooltip_text(name)
        for css, on in (
            ("running", count > 0),
            ("focused", entry.focused),
            ("grouped", count > 1),
            ("pinned", entry.pinned),
        ):
            if on:
                button.add_css_class(css)
            else:
                button.remove_css_class(css)
        self._set_dots(button, dots)
        self.set_state_badges(button, atop, sticky)

    @staticmethod
    def _set_icon(image, icon, size):
        from gi.repository import Gio

        if os.path.isabs(icon) and os.path.exists(icon):
            image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(icon)))
        else:
            image.set_from_icon_name(icon)
        image.set_pixel_size(size)

    @staticmethod
    def _set_dots(button, count):
        from gi.repository import Gtk

        while len(button.dot_widgets) < count:
            dot = Gtk.Box()
            dot.add_css_class("dock-taskbar-dot")
            button.dots.append(dot)
            button.dot_widgets.append(dot)
        while len(button.dot_widgets) > count:
            button.dots.remove(button.dot_widgets.pop())

    @staticmethod
    def set_audio(button, playing, muted):
        """Shows the speaker badge: sound is playing, or playing but muted."""
        state = (playing, muted)
        if getattr(button, "_audio_state", None) == state:
            return
        button._audio_state = state  # pyright: ignore
        badge = button.badge
        badge.set_visible(playing or muted)
        badge.set_from_icon_name(
            "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        )
        if muted:
            badge.add_css_class("muted")
        else:
            badge.remove_css_class("muted")

    @staticmethod
    def set_state_badges(button: Any, atop: bool, sticky: bool) -> None:
        """Shows the always-on-top and sticky badges, skipping no-op updates."""
        state = (bool(atop), bool(sticky))
        if getattr(button, "_state_badges", None) == state:
            return
        button._state_badges = state  # pyright: ignore
        button.badge_atop.set_visible(atop)
        button.badge_sticky.set_visible(sticky)
        button.state_badges.set_visible(atop or sticky)
