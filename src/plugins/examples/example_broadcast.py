def get_plugin_metadata(panel):
    """
    Define the plugin's properties, dependencies, and panel placement.

    Args:
        panel: The main Panel instance, used here to access config_handler.
               (Type: The specific Panel application class.)

    Valid Positions:
        - Top Panel:
            "top-panel-left"
            "top-panel-center"
            "top-panel-right"
            "top-panel-systray"
            "top-panel-after-systray"
        - Bottom Panel:
            "bottom-panel-left"
            "bottom-panel-center"
            "bottom-panel-right"
        - Left Panel:
            "left-panel-top"
            "left-panel-center"
            "left-panel-bottom"
        - Right Panel:
            "right-panel-top"
            "right-panel-center"
            "right-panel-bottom"
        - Background:
            "background"
    Returns:
        dict: Plugin configuration metadata.
    """

    id = "org.jfshell.plugin.example_broadcast"
    default_container = "top-panel-center"

    # check for user config containers, this is not necessary for background plugins
    container, id = panel.config_handler.get_plugin_container(default_container, id)

    return {
        "id": id,
        "name": "Example Broadcast Plugin",
        "version": "1.0.0",
        "enabled": True,
        "container": container,
        "index": 5,
        # CRITICAL: Always define dependencies if the current plugin requires certain plugin to be loaded first
        # WARNING: Missing dependencies can cause plugins to fail loading.
        "deps": ["event_manager"],
    }


def get_plugin_class():
    """
    Returns the main plugin class. All necessary imports are deferred here.
    """
    from src.plugins.core._base import BasePlugin

    class ExampleBroadcastPlugin(BasePlugin):
        def __init__(self, panel_instance):
            super().__init__(panel_instance)
            self.button = None

        def on_start(self):
            """
            Asynchronous entry point, replacing the deprecated initialize_plugin().
            """
            self.logger.info("Setting up ExampleBroadcastPlugin...")
            self._setup_plugin_ui()
            self.logger.info("ExampleBroadcastPlugin has started.")

        def _setup_plugin_ui(self):
            """
            Set up the plugin's UI and functionality.
            """
            self.button = self.create_broadcast_button()
            self.main_widget = (self.button, "append")

        def create_broadcast_button(self):
            """
            Create a button that triggers an IPC broadcast when clicked.
            """
            button = self.gtk.Button()
            button.set_icon_name(self._gtk_helper.icon_exist("broadcast"))
            button.connect("clicked", self.on_button_clicked)
            button.set_tooltip_text("Click to broadcast a message!")
            return button

        def on_button_clicked(self, widget):
            """
            Handle button click event by correctly scheduling the async broadcast.
            """
            self.logger.info("ExampleBroadcastPlugin button clicked!")
            message = {
                "event": "custom_message",
                "data": "Hello from ExampleBroadcastPlugin!",
            }
            self.run_in_async_task(self.broadcast_message(message))

        async def broadcast_message(self, message):
            """
            Broadcast a custom message to all connected clients via the IPC server.
            """
            try:
                self.logger.info(f"Broadcasting message: {message}")
                await self.ipc_server.broadcast_message(message)
            except Exception as e:
                self.logger.error(f"Failed to broadcast message: {e}")

        def on_stop(self):
            """
            Called when the plugin is stopped or unloaded.
            """
            self.logger.info("ExampleBroadcastPlugin has stopped.")

        def on_reload(self):
            """
            Called when the plugin is reloaded dynamically.
            """
            self.logger.info("ExampleBroadcastPlugin has been reloaded.")

        def on_cleanup(self):
            """
            Called before the plugin is completely removed.
            """
            self.logger.info("ExampleBroadcastPlugin is cleaning up resources.")

        def about(self):
            """An example plugin that demonstrates how to create a UI widget and broadcast a message via the IPC server."""
            return self.about.__doc__

    return ExampleBroadcastPlugin
