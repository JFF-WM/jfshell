def get_plugin_metadata(_):
    return {
        "id": "org.jfshell.plugin.top_panel",
        "name": "Top Panel",
        "version": "1.0.0",
        "enabled": True,
        "priority": 10,
        "container": "top-panel",
        "deps": ["event_manager"],
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class TopPanelPlugin(BasePlugin):
        def __init__(self, panel_instance):
            """
            Initialize the TopPanelPlugin and prepare its UI structure.
            """
            super().__init__(panel_instance)
            self._deferred_widgets_attached = False
            self.is_top_panel_ready = False

        # The styles for the classes below (panel-fit, panel-fit-keep) live in
        # top-panel.css.
        # Vertical space left around a button inside the panel (px, total).
        FIT_MARGIN = 6
        FIT_DEFAULT_HEIGHT = 32

        def on_start(self):
            self._fit_timer = None
            self._setup_panel_boxes()
            self.add_css_class()
            self.plugins["css_generator"].install_css("top-panel.css")
            self._start_fit()

        def on_disable(self):
            if self._fit_timer:
                self.glib.source_remove(self._fit_timer)
                self._fit_timer = None

        # ------------------------------------------------------------ design
        def _start_fit(self) -> None:
            self._fit_timer = self.glib.timeout_add(1000, self._fit_tick)
            self.glib.idle_add(self._fit_tick)

        def _fit_sizes(self) -> tuple[int, int]:
            """(button, icon) sizes derived from the configured top panel height."""
            try:
                height = int(
                    float(
                        self._config_handler.get_root_setting(
                            ["org.jfshell.panel", "top", "height"],
                            self.FIT_DEFAULT_HEIGHT,
                        )
                    )
                )
            except (TypeError, ValueError):
                height = self.FIT_DEFAULT_HEIGHT
            button = max(height - self.FIT_MARGIN, 16)
            icon = max(min(button - 8, 16), 12)
            return button, icon

        def _fit_tick(self):
            """Re-applies the sizes: plugins add and replace widgets at any time."""
            sizes = self._fit_sizes()
            # Right side: flat square buttons. Left and center (launcher, clock):
            # keep their own look, only cap icons and drop the forced min height.
            for box in (
                self.obj.top_panel_box_systray,
                self.obj.top_panel_box_for_buttons,
                self.obj.top_panel_box_right,
            ):
                self._fit_widget(box, sizes)
            for box in (self.obj.top_panel_box_left, self.obj.top_panel_box_center):
                self._fit_widget(box, sizes, keep_look=True)
            return True

        def _has_label(self, widget) -> bool:
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, self.gtk.Popover):
                    child = child.get_next_sibling()
                    continue
                if isinstance(child, self.gtk.Label) or self._has_label(child):
                    return True
                child = child.get_next_sibling()
            return False

        def _fit_widget(
            self, widget, sizes: tuple[int, int], keep_look: bool = False
        ) -> None:
            gtk = self.gtk
            if isinstance(widget, gtk.Popover):
                return
            button, icon = sizes
            if keep_look and isinstance(widget, gtk.Box):
                widget.add_css_class("panel-fit-keep")
            if isinstance(widget, gtk.Image):
                if keep_look:
                    # Left and center icons keep the size their plugin gave them.
                    return
                if widget.get_storage_type() == gtk.ImageType.PAINTABLE:
                    if widget.get_size_request() != (icon, icon):
                        widget.set_size_request(icon, icon)
                        widget.set_halign(gtk.Align.CENTER)
                        widget.set_valign(gtk.Align.CENTER)
                elif widget.get_pixel_size() != icon:
                    widget.set_pixel_size(icon)
                return
            if isinstance(widget, (gtk.Button, gtk.MenuButton)):
                widget.set_valign(gtk.Align.CENTER)
                if keep_look:
                    widget.add_css_class("panel-fit-keep")
                else:
                    width = -1 if self._has_label(widget) else button
                    if widget.get_size_request() != (width, button):
                        widget.set_size_request(width, button)
                    widget.add_css_class("panel-fit")
            child = widget.get_first_child()
            while child is not None:
                self._fit_widget(child, sizes, keep_look)
                child = child.get_next_sibling()

        def _attach_widget_to_grid_next_to(
            self, grid, widget_to_attach, relative_widget, position_type, width, height
        ):
            if widget_to_attach.get_parent() is None:
                grid.attach_next_to(
                    widget_to_attach,
                    relative_widget,
                    position_type,
                    width,
                    height,
                )

        def _setup_panel_boxes(self):
            # Left Box Setup
            self.obj.top_panel_box_widgets_left = self.gtk.Box.new(
                self.gtk.Orientation.HORIZONTAL, 6
            )
            self.obj.top_panel_box_left = self.gtk.Box.new(
                self.gtk.Orientation.HORIZONTAL, 0
            )
            self.obj.top_panel_box_widgets_left.set_homogeneous(True)
            self.obj.top_panel_box_left.append(self.obj.top_panel_box_widgets_left)

            # Right Box & Systray Setup
            self.obj.top_panel_box_systray = self.gtk.Box()
            self.obj.top_panel_box_for_buttons = self.gtk.Box()
            self.obj.top_panel_box_right = self.gtk.Box()
            for box in (
                self.obj.top_panel_box_systray,
                self.obj.top_panel_box_for_buttons,
                self.obj.top_panel_box_right,
            ):
                box.set_valign(self.gtk.Align.CENTER)
                box.set_vexpand(False)

            self.spacer = self.gtk.Separator(
                orientation=self.gtk.Orientation.HORIZONTAL
            )
            self.spacer.set_hexpand(True)
            self.spacer.add_css_class("right-box-spacer")
            self.obj.top_panel_box_right.append(self.spacer)

            self.obj.top_panel_grid_right = self.gtk.Grid()
            self.obj.top_panel_grid_right.attach(
                self.obj.top_panel_box_right, 1, 0, 1, 2
            )

            self._attach_widget_to_grid_next_to(
                self.obj.top_panel_grid_right,
                self.obj.top_panel_box_systray,
                self.obj.top_panel_box_right,
                self.gtk.PositionType.RIGHT,
                1,
                2,
            )
            self._attach_widget_to_grid_next_to(
                self.obj.top_panel_grid_right,
                self.obj.top_panel_box_for_buttons,
                self.obj.top_panel_box_systray,
                self.gtk.PositionType.RIGHT,
                1,
                2,
            )

            # Center Box Setup
            self.obj.top_panel_box_center = self.gtk.Box()
            self.obj.top_panel_box_center.set_halign(self.gtk.Align.CENTER)
            self.obj.top_panel_box_center.set_valign(self.gtk.Align.CENTER)
            self.obj.top_panel_box_center.set_hexpand(False)

            # Main Layout Assembly. A CenterBox keeps the center widget centered while
            # left and right only take the width they need. The homogeneous grid it
            # replaces gave all three columns the width of the widest one, so a wide
            # left side (the global menu) made the whole panel scroll.
            self.obj.top_panel_box_full = self.gtk.CenterBox()
            self.obj.top_panel_box_full.set_start_widget(self.obj.top_panel_box_left)
            self.obj.top_panel_box_full.set_center_widget(self.obj.top_panel_box_center)
            self.obj.top_panel_box_full.set_end_widget(self.obj.top_panel_grid_right)

            # Scrolled Window Wrapper
            self.obj.top_panel_scrolled_window = self.gtk.ScrolledWindow()
            # EXTERNAL (not NEVER) vertically: NEVER makes the window as tall as its
            # tallest child, so no widget could let the panel go below its height.
            self.obj.top_panel_scrolled_window.set_policy(
                self.gtk.PolicyType.AUTOMATIC, self.gtk.PolicyType.EXTERNAL
            )
            self.obj.top_panel_scrolled_window.set_child(self.obj.top_panel_box_full)

            # Prevent the scrolled window from trapping clicks
            self.obj.top_panel_scrolled_window.set_focus_on_click(False)

            self.main_widget = (self.obj.top_panel_scrolled_window, "set_child")
            self.is_top_panel_ready = True

        def add_css_class(self):
            widgets = [
                self.obj.top_panel_box_left,
                self.obj.top_panel_box_widgets_left,
                self.obj.top_panel_box_right,
                self.obj.top_panel_box_systray,
                self.obj.top_panel_box_center,
                self.obj.top_panel_box_full,
                self.obj.top_panel_scrolled_window,
            ]

            if all(self.is_widget_ready(w) for w in widgets):
                self.obj.top_panel_box_left.add_css_class("top-panel-box-left")
                self.obj.top_panel_box_widgets_left.add_css_class(
                    "top-panel-box-widgets-left"
                )
                self.obj.top_panel_box_right.add_css_class("top-panel-box-right")
                self.obj.top_panel_box_systray.add_css_class("top-panel-box-systray")
                self.obj.top_panel_box_center.add_css_class("top-panel-box-center")
                self.obj.top_panel_box_full.add_css_class("top-panel-box-full")
                self.obj.top_panel_scrolled_window.add_css_class(
                    "top-panel-scrolled-window"
                )
                return False
            else:
                self.glib.timeout_add(100, self.add_css_class)
                return True

    return TopPanelPlugin
