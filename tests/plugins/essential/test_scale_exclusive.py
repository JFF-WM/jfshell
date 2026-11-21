import sys
import types

import pytest


class _FakeBasePlugin:
    def __init__(self, panel_instance):
        self._panel_instance = panel_instance


@pytest.fixture
def plugin_class(monkeypatch):
    """The plugin class, built without GTK: the modules it imports are stubs."""
    create_panel = types.ModuleType("src.core.create_panel")
    create_panel.get_monitor_info = lambda: {}
    create_panel.get_target_monitor = lambda *_: None
    base = types.ModuleType("src.plugins.core._base")
    base.BasePlugin = _FakeBasePlugin
    monkeypatch.setitem(sys.modules, "src.core.create_panel", create_panel)
    monkeypatch.setitem(sys.modules, "src.plugins.core._base", base)

    from src.plugins.essential.scale_exclusive import get_plugin_class

    return get_plugin_class()


class _Ipc:
    def __init__(self, active=False, error=None):
        self.active = active
        self.error = error

    def is_overview_active(self):
        if self.error:
            raise self.error
        return self.active


class _Logger:
    def error(self, *_):
        pass


def _plugin(plugin_class, ipc):
    plugin = plugin_class(None)
    plugin.ipc = ipc
    plugin.logger = _Logger()
    plugin.activated = 0
    plugin.on_overview_activated = lambda: setattr(
        plugin, "activated", plugin.activated + 1
    )
    return plugin


def test_an_overview_open_before_the_shell_puts_the_panels_up(plugin_class):
    plugin = _plugin(plugin_class, _Ipc(active=True))
    assert plugin._sync_initial_state() is False
    assert plugin.activated == 1


def test_a_closed_overview_leaves_the_panels_alone(plugin_class):
    plugin = _plugin(plugin_class, _Ipc(active=False))
    assert plugin._sync_initial_state() is False
    assert plugin.activated == 0


def test_a_failing_ipc_leaves_the_panels_alone(plugin_class):
    plugin = _plugin(plugin_class, _Ipc(error=RuntimeError("no compositor")))
    assert plugin._sync_initial_state() is False
    assert plugin.activated == 0
