def get_plugin_metadata(panel_instance):
    from src.core.panel_mode import is_single_mode

    return {
        "id": "org.jfshell.plugin.single_panel",
        "name": "Single Panel",
        "version": "1.0.0",
        "enabled": is_single_mode(panel_instance),
        "priority": 1,
        "container": "single-panel",
        "deps": ["event_manager", "css_generator"],
        "description": "One exclusive bottom panel that hosts every widget when single panel mode is active.",
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class SinglePanelPlugin(BasePlugin):
        """
        Layout (left to right):
            start | center (expands, hosts the taskbar) | end | systray | buttons | clock
        Every box name used by the classic panels is aliased to one of these
        boxes, so plugins keep their usual container strings.
        Icons and buttons outside the center follow the taskbar's size.
        """

        def on_start(self):
            self._build()
            self._alias_classic_names()
            self.plugins["css_generator"].install_css("single-panel.css")
            self._start_size_normalizer()
            self._force_popovers_up()

        def _force_popovers_up(self):
            """The panel sits on the bottom edge, so every popover must open upward."""
            Popover = self.gtk.Popover
            if getattr(Popover, "_jf_orig_popup", None):
                return
            orig = Popover.popup
            up = self.gtk.PositionType.TOP

            def popup_up(popover):
                popover.set_position(up)
                return orig(popover)

            Popover._jf_orig_popup = orig
            Popover.popup = popup_up

        def _restore_popover_popup(self):
            Popover = self.gtk.Popover
            orig = getattr(Popover, "_jf_orig_popup", None)
            if orig:
                Popover.popup = orig
                Popover._jf_orig_popup = None

        def on_disable(self):
            self._restore_popover_popup()
            if getattr(self, "_norm_timer", None):
                self.glib.source_remove(self._norm_timer)
                self._norm_timer = None

        def _start_size_normalizer(self):
            """Keeps every icon and button of the panel at the taskbar's size."""
            from src.core.panel_mode import single_panel_metrics

            self._metrics = single_panel_metrics(self._config_handler)
            self._norm_timer = self.glib.timeout_add(1000, self._normalize_sizes)

        def _normalize_sizes(self):
            o = self.obj
            for box in (
                o.single_panel_box_start,
                o.single_panel_box_end,
                o.single_panel_box_systray,
                o.single_panel_box_buttons,
                o.single_panel_box_clock,
            ):
                self._normalize_widget(box, False)
            return True

        def _has_label(self, widget):
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, self.gtk.Popover):
                    child = child.get_next_sibling()
                    continue
                if isinstance(child, self.gtk.Label) or self._has_label(child):
                    return True
                child = child.get_next_sibling()
            return False

        def _size_image(self, image, size):
            """Sizes named icons by pixel size and pixmap/paintable icons by size request."""
            storage = image.get_storage_type()
            if storage in (self.gtk.ImageType.ICON_NAME, self.gtk.ImageType.GICON):
                if image.get_pixel_size() != size:
                    image.set_pixel_size(size)
            elif storage == self.gtk.ImageType.PAINTABLE:
                if image.get_size_request() != (size, size):
                    image.set_size_request(size, size)
                    image.set_halign(self.gtk.Align.CENTER)
                    image.set_valign(self.gtk.Align.CENTER)

        def _normalize_widget(self, widget, in_button):
            """Applies the shared icon size and button height to a widget tree."""
            gtk = self.gtk
            if isinstance(widget, gtk.Popover):
                return
            m = self._metrics
            if isinstance(widget, gtk.Image):
                self._size_image(widget, m["icon_size"])
                return
            if isinstance(widget, (gtk.Button, gtk.MenuButton)):
                in_button = True
                h = m["button_height"]
                w = -1 if self._has_label(widget) else h
                if widget.get_size_request() != (w, h):
                    widget.set_size_request(w, h)
                widget.set_valign(gtk.Align.CENTER)
            if isinstance(widget, gtk.MenuButton):
                if widget.get_direction() != gtk.ArrowType.UP:
                    widget.set_direction(gtk.ArrowType.UP)
            child = widget.get_first_child()
            while child is not None:
                self._normalize_widget(child, in_button)
                child = child.get_next_sibling()

        def _box(self, css_class, hexpand=False, spacing=0):
            box = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, spacing)
            box.add_css_class(css_class)
            box.set_hexpand(hexpand)
            box.set_valign(self.gtk.Align.FILL)
            return box

        def _build(self):
            o = self.obj
            o.single_panel_box_start = self._box("single-panel-start", spacing=4)
            o.single_panel_box_center = self._box("single-panel-center", True, 2)
            o.single_panel_box_center.set_halign(self.gtk.Align.FILL)
            o.single_panel_box_end = self._box("single-panel-end", spacing=4)
            o.single_panel_box_systray = self._box("single-panel-systray", spacing=4)
            o.single_panel_box_buttons = self._box("single-panel-buttons", spacing=4)
            # Very last slot of the panel (right edge): reserved for the clock.
            o.single_panel_box_clock = self._box("single-panel-clock", spacing=0)

            full = self.gtk.Box.new(self.gtk.Orientation.HORIZONTAL, 0)
            full.add_css_class("single-panel-full")
            full.set_hexpand(True)
            for child in (
                o.single_panel_box_start,
                o.single_panel_box_center,
                o.single_panel_box_end,
                o.single_panel_box_systray,
                o.single_panel_box_buttons,
                o.single_panel_box_clock,
            ):
                full.append(child)
            o.single_panel_box_full = full
            self.main_widget = (full, "set_child")

        def _alias_classic_names(self):
            o = self.obj
            aliases = {
                "left": o.single_panel_box_start,
                "widgets_left": o.single_panel_box_start,
                "top": o.single_panel_box_start,
                "center": o.single_panel_box_center,
                "right": o.single_panel_box_end,
                "bottom": o.single_panel_box_end,
                "systray": o.single_panel_box_systray,
                "for_buttons": o.single_panel_box_buttons,
                "full": o.single_panel_box_full,
            }
            for prefix in ("top", "bottom"):
                for suffix, widget in aliases.items():
                    setattr(o, f"{prefix}_panel_box_{suffix}", widget)

    return SinglePanelPlugin
