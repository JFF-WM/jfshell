import logging
import os
import asyncio
import threading
import shutil
import subprocess
import json as std_json
import gi
import sys
import time
import tempfile
from pathlib import Path

from gi.repository import Gio
from src.core.compositor.ipc import IPC, jff_msg
from src.ipc.server import EventServer
from src.core.log_setup import setup_logging
from src.shared import kdl_config

gi.require_version("Gio", "2.0")

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

XDG_CONFIG_HOME = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
XDG_CACHE_HOME = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache"))
XDG_DATA_HOME = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))

DEFAULT_CONFIG_PATH = XDG_CONFIG_HOME / "jfshell"
GTK_LAYER_SHELL_INSTALL_PATH = Path(
    os.getenv("GTK_LAYER_SHELL_PATH", XDG_DATA_HOME / "lib" / "gtk4-layer-shell")
)
# Optional source of default config; set JFSHELL_REPO to a git URL to enable.
JFSHELL_REPO = os.getenv("JFSHELL_REPO", "")
TEMP_DIRS = {
    "gtk_layer_shell": XDG_CACHE_HOME / "gtk4-layer-shell",
    "jfshell": XDG_CACHE_HOME / "jfshell",
}
CONFIG_SUBDIR = "jfshell/config"
DEFAULT_SYSTEM_CONFIG = Path(
    os.getenv("JFSHELL_SYSTEM_CONFIG", "/usr/share/jfshell/default-config")
)

if os.path.exists("/nix/store") and not DEFAULT_SYSTEM_CONFIG.exists():
    nix_system_config = Path("/run/current-system/sw/share/jfshell/default-config")
    if nix_system_config.exists():
        DEFAULT_SYSTEM_CONFIG = nix_system_config

logger = setup_logging(level=logging.INFO)
sock = IPC()

# jff has no bindings concept, clear_bindings() is now a shim on IPC,
# but keep the try/except so a stale IPC version does not crash startup.
try:
    sock.clear_bindings()
except Exception:
    pass


class ConfigWatcher:
    """
    Manages filesystem monitoring for configuration files using Gio.FileMonitor.

    A generic watcher that only logs changes.
    """

    def __init__(self, watched_path, callback):
        self.callback = callback
        self._watched = Path(watched_path).resolve()
        self.last = 0.0
        self.gio_file = Gio.File.new_for_path(str(self._watched))
        self.monitor = None
        self.logger = logging.getLogger("JFShellLogger")

    def _on_file_changed(self, monitor, file, other_file, event_type):
        if event_type in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.MOVED,
        ):
            now = time.time()
            if now - self.last > 1.0:
                self.last = now
                try:
                    self.logger.info(
                        "Watched configuration file modified; invoking callback."
                    )
                    self.callback()
                except Exception:
                    self.logger.exception("reload callback failed")

    def start(self):
        if not self._watched.exists():
            self.logger.warning(f"File to monitor does not exist: {self._watched}")
            return
        try:
            self.monitor = self.gio_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self.monitor.connect("changed", self._on_file_changed)
            self.logger.info(f"Gio.FileMonitor started for: {self._watched}")
        except Exception as e:
            self.logger.error(f"Failed to start Gio.FileMonitor: {e}")
            self.monitor = None

    def stop(self):
        if self.monitor:
            self.monitor.cancel()

    def join(self, timeout=None):
        pass


def global_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger = logging.getLogger("JFShellLogger")
    logger.error(
        "Uncaught exception",
        exc_info=(exc_type, exc_value, exc_traceback),
        extra={"thread_name": threading.current_thread().name},
    )


sys.excepthook = global_exception_handler


def restart_application():
    logger.info("Restarting jfshell...")
    python = sys.executable
    os.execv(python, [python] + sys.argv)


def start_config_watcher():
    """
    jfshell's own config.kdl is already watched by ConfigHandler; there is no
    compositor config to watch under jff. Kept as a no-op so main() stays unchanged.
    """
    return None


def cleanup_resources():
    logger.info("Cleaning up resources before restart...")


def _ipc_server_target(ready_event, container, logger):
    try:
        server = EventServer(logger)
        container["instance"] = server
        ready_event.set()
        try:
            asyncio.run(server.main())
        except Exception:
            logger.exception("IPC asyncio loop failed")
    except Exception:
        logger.exception("IPC server crashed")


def start_ipc_server(logger, timeout=5.0):
    server_container = {}
    ready_event = threading.Event()
    t = threading.Thread(
        target=_ipc_server_target,
        args=(ready_event, server_container, logger),
        daemon=True,
    )
    t.start()
    if not ready_event.wait(timeout=timeout):
        raise TimeoutError("IPC server failed to initialize")
    return server_container.get("instance")



def load_panel(ipc_server):
    try:
        for ver, ver_num in [
            ("Gio", "2.0"),
            ("Gtk4LayerShell", "1.0"),
            ("Gtk", "4.0"),
            ("Gdk", "4.0"),
            ("Playerctl", "2.0"),
            ("Adw", "1"),
        ]:
            try:
                gi.require_version(ver, ver_num)
            except Exception:
                logger.warning(f"GI binding {ver} not available")
    except Exception:
        logger.warning("Some GI bindings not available")
    try:
        from src.panel import Panel
    except Exception:
        logger.exception("Failed to import Panel")
        raise

    config_path = find_config_path()
    config = load_config(config_path)
    panel_conf = config.get("org.jfshell.panel", {}) if isinstance(config, dict) else {}
    monitor_name = (
        sys.argv[-1].strip() if len(sys.argv) > 1 else get_monitor_name(panel_conf)
    )
    app_name = "org.jfshell"
    panel = Panel(application_id=app_name, ipc_server=ipc_server, logger=logger)
    panel.set_panel_instance(panel)
    append_to_env("output_name", monitor_name)
    return panel


def create_first_config():
    dest = DEFAULT_CONFIG_PATH
    dest.mkdir(parents=True, exist_ok=True)
    if DEFAULT_SYSTEM_CONFIG.exists():
        try:
            shutil.copytree(DEFAULT_SYSTEM_CONFIG, dest, dirs_exist_ok=True)
            return
        except Exception:
            logger.exception("Failed copying system default config")
    if os.getenv("JFSHELL_ALLOW_GIT_INIT", "0") != "1":
        return
    if not JFSHELL_REPO:
        return
    tmp = Path(tempfile.mkdtemp(prefix="jfshell_config_"))
    try:
        res = subprocess.run(
            ["git", "clone", JFSHELL_REPO, str(tmp)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if res.returncode != 0:
            logger.error("git clone failed")
            return
        src = tmp / CONFIG_SUBDIR
        if not src.exists():
            return
        shutil.copytree(src, dest, dirs_exist_ok=True)
    finally:
        try:
            shutil.rmtree(tmp)
        except Exception:
            pass


def check_config_path():
    p = DEFAULT_CONFIG_PATH
    try:
        if p.exists() and not any(p.iterdir()):
            p.rmdir()
    except Exception:
        pass
    if not p.exists():
        create_first_config()


def append_to_env(app_name, monitor_name, env_var="jfshell"):
    existing = os.getenv(env_var, "{}")
    try:
        d = std_json.loads(existing)
    except Exception:
        d = {}
    d[app_name] = monitor_name
    os.environ[env_var] = std_json.dumps(d)


def load_config(config_path):
    p = Path(config_path)
    if not p.exists():
        logger.critical("Configuration file not found: %s", config_path)
        sys.exit(1)
    try:
        with p.open("r", encoding="utf-8") as f:
            cfg = kdl_config.load(f)
            return cfg
    except kdl_config.KdlDecodeError as e:
        logger.critical("Failed to parse %s: %s", config_path, e)
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error loading config")
        sys.exit(1)


def get_monitor_name(config):
    """
    Resolve the target monitor name from config, else the first output
    reported by jff, else '-1'.
    """
    conf_name = None
    try:
        if isinstance(config, dict):
            conf_name = config.get("monitor", {}).get("name")
    except Exception:
        pass
    if conf_name:
        return conf_name
    try:
        r = jff_msg("status")
        if r.get("ok"):
            names = sorted(
                {v.get("output") for v in r.get("views", []) if v.get("output")}
            )
            if names:
                return names[0]
    except Exception:
        logger.exception("Failed to get outputs from jff")
    return "-1"


def find_config_path():
    home_config = DEFAULT_CONFIG_PATH / kdl_config.CONFIG_FILENAME
    try:
        if kdl_config.migrate_legacy_toml(home_config):
            logger.info("Converted legacy config.toml to %s", home_config.name)
    except Exception:
        logger.exception("Could not convert legacy config.toml")
    if home_config.exists():
        return str(home_config)
    system_config_paths = [
        "/usr/lib/jfshell/jfshell/config/config.kdl",
    ]
    if os.path.exists("/nix/store"):
        nix_config_paths = [
            "/run/current-system/sw/share/jfshell/config.kdl",
            "/nix/var/nix/profiles/system/sw/share/jfshell/config.kdl",
        ]
        system_config_paths.extend(nix_config_paths)
        try:
            nix_configs = list(Path("/nix/store").glob("*/share/jfshell/config.kdl"))
            if nix_configs:
                system_config_paths.extend(str(p) for p in nix_configs)
        except Exception:
            pass
    for config_path in system_config_paths:
        if os.path.exists(config_path):
            return config_path
    script_dir = Path(__file__).resolve().parent
    dev_config = script_dir.parent / "config" / "config.kdl"
    if dev_config.exists():
        return str(dev_config)
    env_config = os.getenv("JFSHELL_CONFIG_PATH")
    if env_config and os.path.exists(env_config):
        return env_config
    return str(home_config)


EXIT_NO_IPC = 3  # run.py treats this code as "do not restart"


def ensure_ipc_available():
    """Exit before building the panel when jff's IPC cannot be reached."""
    try:
        jff_msg("status")
    except Exception as e:
        logger.critical(f"jff IPC is not available, refusing to start: {e}")
        sys.exit(EXIT_NO_IPC)


def main():
    watcher = None
    try:
        # No compositor-specific startup: no config watcher, no plugin whitelisting.
        ensure_ipc_available()
        check_config_path()
        ipc_server = start_ipc_server(logger)
        panel = load_panel(ipc_server)
        if hasattr(panel, "run_gc_cleanup"):
            panel.run_gc_cleanup()
        return panel.run(["org.jfshell"])
    except Exception:
        logger.critical("Fatal error during initialization", exc_info=True)
        raise
    finally:
        if watcher:
            try:
                watcher.stop()
                watcher.join(timeout=1.0)
            except Exception:
                pass
        cleanup_resources()


if __name__ == "__main__":
    main()
