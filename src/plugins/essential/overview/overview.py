def get_plugin_metadata(panel):
    about = "Adds an Overview button to the left of the top panel that toggles the overview."

    id = "org.jfshell.plugin.overview"
    default_container = "top-panel-left"
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Overview",
        "version": "1.0.0",
        "enabled": True,
        "container": container,
        "index": 1,
        "priority": 990,
        "deps": ["css_generator"],
        "description": about,
    }


def get_plugin_class():
    from src.plugins.core._base import BasePlugin

    class OverviewPlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.overview_button = None
            self.add_hint(
                ["Configuration for the Overview button."],
                None,
            )
            self.icon_name = self.get_plugin_setting_add_hint(
                ["icon"],
                "view-app-grid-symbolic",
                "Icon name of the button that toggles the overview.",
            )

        def on_start(self):
            self.create_overview_widget()

        def create_overview_widget(self):
            """
            Creates the icon button that toggles the overview when clicked. It
            reuses the global menu button classes so it looks like the items
            next to it.
            """
            icon = self.gtk_helper.icon_exist(
                str(self.icon_name),
                [
                    "view-grid-symbolic",
                    "view-fullscreen-symbolic",
                    "applications-symbolic",
                ],
            )
            self.overview_button = self.gtk.Button.new_from_icon_name(icon)
            self.overview_button.set_tooltip_text("Overview")
            self.overview_button.add_css_class("overview-button")
            self.overview_button.add_css_class("global-menu-button")
            self.overview_button.add_css_class("flat")
            self.overview_button.connect("clicked", self.on_clicked)
            self.gtk_helper.add_cursor_effect(self.overview_button)
            self.main_widget = (self.overview_button, "append")

        def on_clicked(self, _button):
            try:
                self.ipc.toggle_overview()
            except Exception as e:
                self.logger.error(f"Overview toggle failed: {e}")

    return OverviewPlugin
