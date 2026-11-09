import os
import time
import threading
import functools
from pathlib import Path
from typing import Any, List, Optional, Dict, Tuple, Union
from gi.repository import Gio  # pyright: ignore
from src.shared import config_template, kdl_config


class _ConfigState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.config_data: Dict[str, Any] = {}
        self.cached_config: Optional[Dict[str, Any]] = None
        self.last_mod_time: float = 0.0
        self.load_successful: bool = False
        self.config_monitor: Optional[Gio.FileMonitor] = None
        self.first_output_name: Optional[str] = None
        self.initialized: bool = False


_STATES: Dict[str, _ConfigState] = {}
_STATES_LOCK = threading.Lock()


def _state_for(config_file: Path) -> _ConfigState:
    key = str(config_file)
    with _STATES_LOCK:
        state = _STATES.get(key)
        if state is None:
            state = _STATES[key] = _ConfigState()
        return state


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._state.lock:
            return method(self, *args, **kwargs)

    return wrapper


class ConfigHandler:
    def __init__(self, panel_instance: Any, plugin_id: Optional[str] = None):
        self.logger = panel_instance.logger
        self.plugin_id = plugin_id
        self.panel_instance = panel_instance
        self.default_config = config_template.default_config
        self._setup_config_paths()
        self.config_file = Path(self.config_path) / kdl_config.CONFIG_FILENAME
        self.gio_config_file = Gio.File.new_for_path(str(self.config_file))
        self.config_path: str = self.config_file.parent.as_posix()
        self._state = _state_for(self.config_file)
        with self._state.lock:
            if not self._state.initialized:
                self.config_data = self.load_config()
                self._cached_config = self.config_data
                self._state.initialized = True
        self._start_watcher()

    @property
    def config_data(self) -> Dict[str, Any]:
        return self._state.config_data

    @config_data.setter
    def config_data(self, value: Dict[str, Any]) -> None:
        self._state.config_data = value

    @property
    def _cached_config(self) -> Optional[Dict[str, Any]]:
        return self._state.cached_config

    @_cached_config.setter
    def _cached_config(self, value: Optional[Dict[str, Any]]) -> None:
        self._state.cached_config = value

    @property
    def _last_mod_time(self) -> float:
        return self._state.last_mod_time

    @_last_mod_time.setter
    def _last_mod_time(self, value: float) -> None:
        self._state.last_mod_time = value

    @property
    def _load_successful(self) -> bool:
        return self._state.load_successful

    @_load_successful.setter
    def _load_successful(self, value: bool) -> None:
        self._state.load_successful = value

    @property
    def config_monitor(self) -> Optional[Gio.FileMonitor]:
        return self._state.config_monitor

    @config_monitor.setter
    def config_monitor(self, value: Optional[Gio.FileMonitor]) -> None:
        self._state.config_monitor = value

    @property
    def first_output_name(self) -> str:
        state = self._state
        if state.first_output_name is None:
            try:
                from src.core.compositor.ipc import jff_snapshot

                # `status` only names monitors that have a window on them, the
                # watch snapshot lists every monitor.
                snap = jff_snapshot(("outputs",))
                outs = {o["name"] for o in snap.get("outputs", []) if o.get("name")}
                state.first_output_name = sorted(outs)[0] if outs else "unknown"
            except Exception:
                state.first_output_name = "unknown"
        return state.first_output_name

    def _on_config_file_changed(self, monitor, file, other_file, event_type) -> None:
        if event_type in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.MOVED,
            Gio.FileMonitorEvent.CHANGED,
        ):
            try:
                current_mod_time = os.path.getmtime(self.config_file)
                if current_mod_time > self._last_mod_time:
                    self.reload_config()
                    self._last_mod_time = current_mod_time
                self._reload_css()
            except FileNotFoundError:
                self.logger.warning("Config file not found during GIO change check.")
            except Exception as e:
                self.logger.error(f"Error checking mod time in GIO callback: {e}")

    def _reload_css(self) -> None:
        if "css_generator" in self.panel_instance.plugins:
            self.panel_instance.plugins["css_generator"].generate_styles_css()

    def _strip_hints(self, data: Dict[str, Any]) -> Dict[str, Any]:
        stripped_data = {}
        for key, value in data.items():
            if key.endswith(("_hint",)):
                continue
            if isinstance(value, dict):
                stripped_data[key] = self._strip_hints(value)
            elif isinstance(value, list) and all(
                isinstance(item, dict) for item in value
            ):
                stripped_data[key] = [self._strip_hints(item) for item in value]
            else:
                stripped_data[key] = value
        return stripped_data

    @property
    def default_config_stripped(self) -> Dict[str, Any]:
        return self._strip_hints(self.default_config)

    def _recursive_merge(
        self, user_config: Dict[str, Any], default_config: Dict[str, Any]
    ) -> bool:
        write_back_needed = False
        for key, default_value in default_config.items():
            if key not in user_config:
                user_config[key] = default_value
                write_back_needed = True
            elif isinstance(default_value, dict) and isinstance(
                user_config.get(key), dict
            ):
                if self._recursive_merge(user_config[key], default_value):
                    write_back_needed = True
        return write_back_needed

    @_locked
    def save_config(self) -> None:
        if not self._load_successful:
            self.logger.warning("Skipping configuration save: load failed.")
            return
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                kdl_config.dump(self.config_data, f)
            self._last_mod_time = os.path.getmtime(self.config_file)
            self.logger.info("Configuration saved successfully.")
        except Exception as e:
            self.logger.error(error=e, message="Failed to save configuration to file.")

    @_locked
    def reload_config(self) -> None:
        try:
            new_config = self.load_config(force_reload=True)
            self.config_data.update(new_config)
            self.logger.debug("Configuration reloaded from file.")
        except Exception as e:
            self.logger.error(f"Error reloading configuration: {e}")

    @_locked
    def load_config(self, force_reload: bool = False) -> Dict[str, Any]:
        if self._cached_config and not force_reload:
            return self._cached_config
        config_from_file: Dict[str, Any] = {}
        file_path = Path(self.config_file)
        try:
            if kdl_config.migrate_legacy_toml(file_path):
                self.logger.info(
                    f"Converted legacy {kdl_config.LEGACY_CONFIG_FILENAME} "
                    f"to {file_path.name}."
                )
        except Exception as e:
            self.logger.error(f"Could not convert legacy config: {e}")
        file_must_be_created = not file_path.exists()
        load_succeeded = False
        if file_must_be_created:
            self.logger.info("Config file is missing. Applying defaults.")
            config_from_file = {}
            load_succeeded = True
        else:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        config_from_file = kdl_config.load(f)
                    self._last_mod_time = os.path.getmtime(self.config_file)
                    load_succeeded = True
                    break
                except Exception as e:
                    self.logger.error(f"Load attempt {attempt + 1} failed: {e}")
                    time.sleep(0.1)
            else:
                self.logger.error(
                    "Failed to load config file. Falling back to defaults."
                )
                config_from_file = {}
        self._load_successful = load_succeeded
        self._recursive_merge(config_from_file, self.default_config_stripped)
        self.config_data = config_from_file
        self._cached_config = config_from_file
        if file_must_be_created:
            self.logger.info("Writing default configuration to new file.")
            self.save_config()
        self.logger.debug("Configuration loaded and synchronized.")
        return self.config_data

    def _setup_config_paths(self) -> None:
        config_paths = self.setup_config_paths()
        self.home: str = config_paths.get("home", os.path.expanduser("~"))
        self.config_path: str = config_paths.get("config_path", "")
        self.style_css_config: str = config_paths.get("style_css_config", "")
        self.cache_folder: str = config_paths.get("cache_folder", "")
        self.config_file = Path(self.config_path) / kdl_config.CONFIG_FILENAME
        os.makedirs(self.config_path, exist_ok=True)
        os.makedirs(self.cache_folder, exist_ok=True)
        self.webapps_applications: str = os.path.join(
            self.home, ".local/share/applications"
        )

    def setup_config_paths(self) -> Dict[str, str]:
        try:
            home = os.path.expanduser("~")
            xdg_config_home = os.getenv(
                "XDG_CONFIG_HOME", os.path.join(home, ".config")
            )
            config_path = os.path.join(xdg_config_home, "jfshell")
            style_css_config = os.path.join(config_path, "shellow-styles.css")
            xdg_cache_home = os.getenv("XDG_CACHE_HOME", os.path.join(home, ".cache"))
            cache_folder = os.path.join(xdg_cache_home, "jfshell")
            Path(config_path).mkdir(parents=True, exist_ok=True)
            Path(cache_folder).mkdir(parents=True, exist_ok=True)
            return {
                "home": home,
                "config_path": config_path,
                "style_css_config": style_css_config,
                "cache_folder": cache_folder,
            }
        except Exception as e:
            self.logger.error(
                f"Unexpected error while setting up configuration paths: {e}",
                exc_info=True,
            )
            return {}

    @_locked
    def initialize_config_section(
        self, section_name: str, default_config: Dict[str, Any] = {}
    ) -> None:
        if section_name not in self.config_data:
            self.config_data[section_name] = self.default_config_stripped.get(
                section_name, {}
            )
            self.save_config()
            self.reload_config()

    def _start_watcher(self) -> None:
        with self._state.lock:
            if self._state.config_monitor is not None:
                return
            try:
                monitor = self.gio_config_file.monitor_file(
                    Gio.FileMonitorFlags.NONE, None
                )
                monitor.connect("changed", self._on_config_file_changed)
                self._state.config_monitor = monitor
            except Exception as e:
                self.logger.error(f"Failed to start Gio.FileMonitor: {e}")

    @_locked
    def set_root_setting(self, key_path: List[str], new_value: Any) -> bool:
        if not key_path:
            self.logger.error("Configuration key path cannot be empty.")
            return False
        if not self._load_successful:
            self.logger.warning("Update skipped: config failed to load.")
            return False
        current_data = self.config_data
        for i, key in enumerate(key_path[:-1]):
            if not isinstance(current_data, dict):
                self.logger.error(f"Configuration data corrupted at {key_path[:i]}")
                return False
            if key not in current_data or not isinstance(current_data[key], dict):
                current_data[key] = {}
            current_data = current_data[key]
        final_key = key_path[-1]
        if not isinstance(current_data, dict):
            return False
        current_data[final_key] = new_value
        self.save_config()
        self.reload_config()
        return True

    def get_root_setting(self, key_path: List[str], default_value: Any = None) -> Any:
        current_data = self.config_data
        for i, key in enumerate(key_path):
            if key.endswith(("_hint", "_section_hint", "_items_hint")):
                continue
            if isinstance(current_data, dict) and key in current_data:
                current_data = current_data[key]
            else:
                return default_value
        return current_data

    @_locked
    def update_config(self, key_path: List[str], new_value: Any) -> bool:
        if not key_path:
            return False
        current_data = self.config_data
        for i, key in enumerate(key_path[:-1]):
            if (
                isinstance(current_data, dict)
                and key in current_data
                and isinstance(current_data[key], dict)
            ):
                current_data = current_data[key]
            else:
                return False
        final_key = key_path[-1]
        if isinstance(current_data, dict):
            if not self._load_successful:
                return False
            current_data[final_key] = new_value
            self.save_config()
            self.reload_config()
            return True
        return False

    @_locked
    def remove_root_setting(self, key: Union[str, List[str]]) -> None:
        if not key:
            return
        key_path = [key] if isinstance(key, str) else key
        current_level = self.config_data
        for part in key_path[:-1]:
            if isinstance(current_level, dict) and part in current_level:
                current_level = current_level[part]
            else:
                return
        final_key = key_path[-1]
        if isinstance(current_level, dict) and final_key in current_level:
            del current_level[final_key]
            hint_key = f"{final_key}_hint"
            if hint_key in current_level:
                del current_level[hint_key]
            self.save_config()

    def remove_plugin_setting(self) -> None:
        if not self.plugin_id:
            return
        self.remove_root_setting(self.plugin_id)

    def set_section_hint(
        self, section_path: Union[str, List[str]], hint: Union[str, Tuple[str, ...]]
    ) -> bool:
        path_list = [section_path] if isinstance(section_path, str) else section_path
        hint_path = path_list + ["_section_hint"]
        return self._inject_to_dict(self.default_config, hint_path, hint)

    def _inject_to_dict(
        self, target_dict: Dict[str, Any], key_path: List[str], new_value: Any
    ) -> bool:
        if not key_path:
            return False
        current_data = target_dict
        for key in key_path[:-1]:
            if not isinstance(current_data, dict):
                return False
            if not isinstance(current_data.get(key), dict):
                current_data[key] = {}
            current_data = current_data[key]
        final_key = key_path[-1]
        if not isinstance(current_data, dict):
            return False
        current_data[final_key] = new_value
        return True

    def set_setting_hint(self, key_path: str, section, hint) -> bool:
        if key_path not in self.default_config or not isinstance(
            self.default_config.get(key_path), dict
        ):
            self.default_config[key_path] = {}
        current_dict = self.default_config[key_path]
        if section is None or section == "":
            path = []
        elif isinstance(section, str):
            path = [section]
        elif isinstance(section, (list, tuple)):
            path = list(section)
        else:
            return False
        if not path:
            current_dict["_section_hint"] = hint
            return True
        path_to_parent = path[:-1]
        last_key = path[-1]
        hint_key = f"{last_key}_hint"
        for key in path_to_parent:
            if key not in current_dict or not isinstance(current_dict.get(key), dict):
                current_dict[key] = {}
            current_dict = current_dict[key]
        current_dict[hint_key] = hint
        return True

    def get_plugin_setting(
        self, key: Optional[Union[str, List[str]]] = None, default_value: Any = None
    ) -> Any:
        _MISSING = object()
        if not self.plugin_id:
            return default_value
        key_path = [self.plugin_id]
        if key is not None:
            if isinstance(key, str):
                key_path.append(key)
            elif isinstance(key, list):
                key_path.extend(key)
        result = self.get_root_setting(key_path, _MISSING)
        if result is _MISSING:
            if default_value is not None:
                self.set_root_setting(key_path, default_value)
            return default_value
        return result

    def get_plugin_container(self, default_container: str, id: str) -> Tuple[str, str]:
        container = self.get_root_setting([id, "container"], None)
        if container is None:
            container = default_container
            self.set_root_setting([id, "container"], container)
        return container, id

    def set_plugin_setting(self, key: Union[str, List[str]], value: Any) -> None:
        if not self.plugin_id:
            return
        key_path: List[str] = [self.plugin_id]
        if isinstance(key, str):
            key_path.append(key)
        elif isinstance(key, list):
            key_path.extend(key)
        self.set_root_setting(key_path, value)

    def get_settings(self) -> Dict[str, Any]:
        if self._cached_config is None:
            return self.load_config(force_reload=True)
        return self._cached_config
