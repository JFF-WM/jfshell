def get_plugin_metadata(_):
    about = (
        "This plugin adds a calendar popover to the panel, which is "
        "displayed when the user clicks on the clock widget. "
        "The popover has a vertical sidebar where other plugins add their own pages."
    )

    return {
        "id": "org.jfshell.plugin.calendar",
        "name": "Calendar",
        "version": "1.2.0",
        "enabled": True,
        "priority": 1,
        "deps": ["clock", "css_generator"],
        "description": about,
    }


def get_plugin_class():
    """
    The main plugin class entry point. ALL imports are deferred here
    to comply with the JFShell loading architecture.
    Returns:
        type: The CalendarPlugin class.
    """
    from src.plugins.core._base import BasePlugin

    class CalendarPlugin(BasePlugin):
        """
        Plugin to integrate a calendar popover with the clock plugin.

        The popover holds a vertical sidebar and a stack of pages. The calendar
        (with the weather and notes plugins attached to ``self.grid``) is the
        first page. Other plugins add pages with ``add_page``.
        """

        CALENDAR_PAGE = "calendar"

        def __init__(self, panel_instance):
            """
            Initialize the plugin.
            Args:
                panel_instance: The main panel instance provided by the framework.
            """
            super().__init__(panel_instance)

        def setup_calendar(self):
            """Setup the calendar popover."""
            if "clock" not in self.plugin_loader.plugins:
                self.logger.error("Clock plugin is not loaded. Cannot append calendar.")
                return
            clock_plugin = self.plugin_loader.plugins["clock"]
            clock_button = clock_plugin.clock_button
            self.popover_calendar = self._gtk_helper.create_popover(
                clock_button, "calender-popover", has_arrow=False, offset=(0, 5)
            )
            self.grid = self.gtk.Grid()
            self.grid.set_row_spacing(self.layout_row_spacing)
            self.grid.set_column_spacing(self.layout_column_spacing)
            self.grid.set_margin_top(self.layout_margin_top)
            self.grid.set_margin_bottom(self.layout_margin_bottom)
            self.grid.set_margin_start(self.layout_margin_start)
            self.grid.set_margin_end(self.layout_margin_end)
            self.grid.set_halign(self.gtk.Align.CENTER)
            self.grid.set_valign(self.gtk.Align.CENTER)

            self.calendar = self.gtk.Calendar()
            self.calendar.add_css_class("calendar-widget")
            self.calendar.set_halign(self.gtk.Align.CENTER)
            self.calendar.set_valign(self.gtk.Align.CENTER)
            self.calendar.set_size_request(340, -1)

            self.grid.attach(self.calendar, 0, 0, 1, 1)
            self.setup_sidebar()
            self.add_page(
                self.CALENDAR_PAGE,
                ["x-office-calendar-symbolic", "office-calendar-symbolic"],
                "Calendar",
                self.grid,
            )
            self.popover_calendar.set_child(self.root_box)
            self.popover_calendar.connect("notify::visible", self.on_visibility_changed)
            clock_button.connect("clicked", self.toggle_calendar)

        def setup_sidebar(self):
            """Build the popover root: a vertical sidebar next to a stack of pages."""
            self.root_box = self.gtk.Box(orientation=self.gtk.Orientation.HORIZONTAL)
            self.root_box.add_css_class("clock-popover-root")
            self.sidebar = self.gtk.Box(orientation=self.gtk.Orientation.VERTICAL)
            self.sidebar.add_css_class("clock-sidebar")
            self.sidebar.set_spacing(6)
            self.sidebar.set_valign(self.gtk.Align.FILL)
            self.sidebar.set_visible(False)
            self.stack = self.gtk.Stack()
            self.stack.add_css_class("clock-pages")
            self.stack.set_hexpand(True)
            self.stack.set_vexpand(True)
            self.stack.set_hhomogeneous(True)
            self.stack.set_vhomogeneous(True)
            self.stack.set_transition_type(self.gtk.StackTransitionType.CROSSFADE)
            self.stack.set_transition_duration(120)
            self.root_box.append(self.sidebar)
            self.root_box.append(self.stack)

        def resolve_icon(self, icon_names):
            """Return the first icon name the current theme has, else the first name."""
            display = self.gdk.Display.get_default()
            theme = self.gtk.IconTheme.get_for_display(display) if display else None
            for name in icon_names:
                if theme is None or theme.has_icon(name):
                    return name
            return icon_names[0]

        def add_page(
            self, page_id, icon_names, title, widget, on_show=None, on_hide=None
        ) -> bool:
            """
            Add a page to the popover with its button in the sidebar.
            Args:
                page_id: Unique name of the page.
                icon_names: Icon names to try in order for the sidebar button.
                title: Tooltip of the sidebar button.
                widget: The page content.
                on_show: Called (no arguments) each time the page becomes visible.
                on_hide: Called (no arguments) each time the page stops being visible.
            Returns:
                bool: False when the page id is already taken.
            """
            if page_id in self.pages:
                self.logger.warning(f"Popover page '{page_id}' already exists.")
                return False
            button = self.gtk.ToggleButton()
            button.add_css_class("clock-sidebar-button")
            button.set_tooltip_text(title)
            button.set_child(
                self.gtk.Image.new_from_icon_name(self.resolve_icon(icon_names))
            )
            self.gtk_helper.add_cursor_effect(button)
            if self.sidebar_group is None:
                self.sidebar_group = button
            else:
                button.set_group(self.sidebar_group)
            button.connect("toggled", self.on_sidebar_toggled, page_id)
            self.sidebar.append(button)
            self.stack.add_named(widget, page_id)
            self.pages[page_id] = {
                "button": button,
                "widget": widget,
                "on_show": on_show,
                "on_hide": on_hide,
            }
            if self.current_page is None:
                button.set_active(True)
                self.current_page = page_id
                self.stack.set_visible_child_name(page_id)
            self.sidebar.set_visible(len(self.pages) > 1)
            return True

        def remove_page(self, page_id) -> None:
            """Remove a page added with add_page. The calendar page stays."""
            page = self.pages.get(page_id)
            if page is None or page_id == self.CALENDAR_PAGE:
                return
            if self.current_page == page_id:
                self.show_page(self.CALENDAR_PAGE)
            del self.pages[page_id]
            page["button"].set_group(None)
            self.sidebar.remove(page["button"])
            self.stack.remove(page["widget"])
            self.sidebar.set_visible(len(self.pages) > 1)

        def show_page(self, page_id) -> None:
            """Show a page and mark its sidebar button."""
            page = self.pages.get(page_id)
            if page is None:
                return
            page["button"].set_active(True)
            self.activate_page(page_id)

        def open_page(self, page_id) -> None:
            """
            Open the popover on a page, for plugins with their own panel button.
            When the popover is already open on that page it closes instead.
            """
            if self.popover_calendar is None or page_id not in self.pages:
                return
            if self.popover_calendar.get_visible():
                if self.current_page == page_id:
                    self.popover_calendar.popdown()
                else:
                    self.show_page(page_id)
                return
            self.pending_page = page_id
            self.popover_calendar.popup()

        def on_sidebar_toggled(self, button, page_id) -> None:
            """A sidebar button changed: only the button that became active acts."""
            if button.get_active():
                self.activate_page(page_id)

        def activate_page(self, page_id) -> None:
            """Switch the stack to a page and run the hide and show hooks."""
            if page_id == self.current_page or page_id not in self.pages:
                return
            visible = bool(
                self.popover_calendar and self.popover_calendar.get_visible()
            )
            if visible:
                self.run_page_hook(self.current_page, "on_hide")
            self.current_page = page_id
            self.stack.set_visible_child_name(page_id)
            if visible:
                self.run_page_hook(page_id, "on_show")

        def run_page_hook(self, page_id, hook) -> None:
            """Call a page hook, a failing hook never breaks the popover."""
            page = self.pages.get(page_id)
            callback = page.get(hook) if page else None
            if callback is None:
                return
            try:
                callback()
            except Exception as e:
                self.logger.error(f"Popover page '{page_id}' {hook} failed: {e}")

        def on_visibility_changed(self, popover, _param) -> None:
            """Run the hooks of the current page when the popover opens or closes."""
            if not popover.get_visible():
                self.run_page_hook(self.current_page, "on_hide")
                return
            target, self.pending_page = self.pending_page, None
            if target in self.pages and target != self.current_page:
                self.show_page(target)
            elif target in self.pages:
                self.run_page_hook(target, "on_show")
            elif not self.remember_page and self.current_page != self.CALENDAR_PAGE:
                self.show_page(self.CALENDAR_PAGE)
            else:
                self.run_page_hook(self.current_page, "on_show")

        def on_start(self):
            """
            The primary activation method for the plugin.
            """
            self.popover_calendar = None
            self.calendar = None
            self.pages = {}
            self.current_page = None
            self.pending_page = None
            self.sidebar_group = None
            self.add_hint(
                [
                    "Settings controlling the layout, spacing, and margins of the Calendar popover's internal grid."
                ],
                ["layout"],
            )
            self.layout_row_spacing = self.get_plugin_setting(
                ["layout", "row_spacing"], 10
            )
            self.add_hint(
                ["Vertical spacing (in pixels) between rows in the calendar grid."],
                ["layout", "row_spacing"],
            )
            self.layout_column_spacing = self.get_plugin_setting(
                ["layout", "column_spacing"], 10
            )
            self.add_hint(
                [
                    "Horizontal spacing (in pixels) between columns in the calendar grid."
                ],
                ["layout", "column_spacing"],
            )
            self.layout_margin_top = self.get_plugin_setting(
                ["layout", "margin_to_top"], 10
            )
            self.add_hint(
                ["Top margin (in pixels) for the calendar grid content."],
                ["layout", "margin_to_top"],
            )
            self.layout_margin_bottom = self.get_plugin_setting(
                ["layout", "margin_to_bottom"], 10
            )
            self.add_hint(
                ["Bottom margin (in pixels) for the calendar grid content."],
                ["layout", "margin_to_bottom"],
            )
            self.layout_margin_start = self.get_plugin_setting(
                ["layout", "margin_from_start"], 10
            )
            self.add_hint(
                ["Start (left) margin (in pixels) for the calendar grid content."],
                ["layout", "margin_from_start"],
            )
            self.layout_margin_end = self.get_plugin_setting(
                ["layout", "margin_from_end"], 10
            )
            self.add_hint(
                ["End (right) margin (in pixels) for the calendar grid content."],
                ["layout", "margin_from_end"],
            )
            self.remember_page = self.get_plugin_setting_add_hint(
                ["sidebar", "remember_page"],
                False,
                "When true the popover reopens on the page that was open when it closed, otherwise it always opens on the calendar.",
            )

            self.setup_calendar()
            self.plugins["css_generator"].install_css("calendar.css")
            self.plugins["css_generator"].install_css("sidebar.css")

        def toggle_calendar(self, *_) -> None:
            """Toggle the calendar popover."""
            if self.popover_calendar and self.popover_calendar.is_visible():
                self.popover_calendar.popdown()
            else:
                self.popover_calendar.popup()  # pyright: ignore

    return CalendarPlugin
