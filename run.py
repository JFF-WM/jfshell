#!/usr/bin/env python3
"""
A robust application launcher for JFShell.
"""

import socket


def setup_logging() -> None:
    """Configures a basic logger to print info to stdout and errors to stderr."""
    import sys
    import logging

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    stderr_handler.setFormatter(formatter)
    logging.getLogger().addHandler(stderr_handler)


def get_config_class():
    """Defines and returns the AppConfig dataclass for path management."""
    import os
    from pathlib import Path
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class AppConfig:
        """
        Configuration container for application directory and file paths.

        Attributes:
            app_name: The internal name of the application.
            xdg_data_home: Base directory for user-specific data files.
            xdg_config_home: Base directory for user-specific configuration files.
            xdg_cache_home: Base directory for user-specific non-essential data files.
        """

        app_name: str
        xdg_data_home: Path = Path(
            os.getenv("XDG_DATA_HOME", "~/.local/share")
        ).expanduser()
        xdg_config_home: Path = Path(
            os.getenv("XDG_CONFIG_HOME", "~/.config")
        ).expanduser()
        xdg_cache_home: Path = Path(
            os.getenv("XDG_CACHE_HOME", "~/.cache")
        ).expanduser()

        @property
        def data_dir(self) -> Path:
            return self.xdg_data_home / self.app_name

        @property
        def config_dir(self) -> Path:
            return self.xdg_config_home / self.app_name

        @property
        def backup_base_dir(self) -> Path:
            return self.xdg_cache_home / self.app_name / "backups"

        @property
        def build_dir(self) -> Path:
            """Writeable build directory in cache."""
            return self.xdg_cache_home / self.app_name / "build"

        @property
        def venv_dir(self) -> Path:
            return self.data_dir / "venv"

        @property
        def config_file(self) -> Path:
            return self.config_dir / "config.kdl"

        @property
        def resources_dir(self) -> Path:
            return self.data_dir / "resources"

        @property
        def venv_bin_dir(self) -> Path:
            return self.venv_dir / "bin"

        @property
        def venv_python(self) -> Path:
            for name in ("python", "python3"):
                binary = self.venv_bin_dir / name
                if binary.exists():
                    return binary
            return self.venv_bin_dir / "python"

        @property
        def requirements_flag(self) -> Path:
            return self.venv_dir / ".requirements_installed"

    return AppConfig


def run_backup(config, max_copies: int = 10) -> None:
    """Performs a comprehensive backup of data and config directories."""
    import shutil
    import logging
    from datetime import datetime

    logging.info("Starting asynchronous data and config backup...")
    source_dirs = {"data": config.data_dir, "config": config.config_dir}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_target_root = config.backup_base_dir / f"backup_{timestamp}"

    try:
        backup_target_root.mkdir(parents=True, exist_ok=True)
        for name, source_path in source_dirs.items():
            if not source_path.is_dir():
                continue
            target_path = backup_target_root / name
            ignore = shutil.ignore_patterns("venv") if name == "data" else None
            shutil.copytree(source_path, target_path, symlinks=False, ignore=ignore)

        all_backups = sorted(
            config.backup_base_dir.glob("backup_*"), key=lambda p: p.name
        )
        if len(all_backups) > max_copies:
            for old_backup in all_backups[: len(all_backups) - max_copies]:
                shutil.rmtree(old_backup)
        logging.info("Comprehensive backup complete.")
    except Exception as e:
        logging.error("Backup failed: %s", e)


def _find_system_library(lib_name: str) -> str:
    """Locates a system library using dynamic environment paths and system utilities."""
    import os
    import ctypes.util
    from pathlib import Path

    env_override = os.getenv("JFSHELL_GTK_LAYER_SHELL_PATH")
    if env_override and Path(env_override).is_file():
        return env_override

    search_paths = [
        "/usr/lib/libgtk4-layer-shell.so",
        "/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so",
        "/app/lib/libgtk4-layer-shell.so",
    ]
    for path_str in search_paths:
        if Path(path_str).is_file():
            return path_str

    find_lib = ctypes.util.find_library(lib_name.replace("lib", "").replace(".so", ""))
    return find_lib if find_lib else ""


def manage_virtual_environment(config, req_file) -> None:
    """Ensures the venv exists and all dependencies are installed."""
    import os
    import sys
    import shutil
    import subprocess
    import logging

    venv_python = config.venv_dir / "bin" / "python3"
    if not venv_python.exists():
        venv_python = config.venv_dir / "bin" / "python"

    if config.venv_dir.exists() and not venv_python.exists():
        shutil.rmtree(config.venv_dir)

    if not config.venv_dir.is_dir():
        logging.info("Creating virtual environment...")
        config.venv_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "venv",
                "--system-site-packages",
                str(config.venv_dir),
            ],
            check=True,
        )

    os.environ["PATH"] = f"{config.venv_dir}/bin{os.pathsep}{os.environ['PATH']}"

    if config.requirements_flag.exists():
        return

    try:
        pip_check = subprocess.run(
            [str(venv_python), "-m", "pip", "--version"], capture_output=True
        )

        if pip_check.returncode != 0:
            subprocess.run(
                [str(venv_python), "-m", "ensurepip", "--upgrade"], check=True
            )

        if req_file.is_file():
            subprocess.run(
                [
                    str(venv_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "-r",
                    str(req_file),
                ],
                check=True,
            )
        config.requirements_flag.touch()
    except Exception as e:
        logging.critical("Failed to install dependencies: %s", e)
        sys.exit(1)


def ensure_initial_setup(config) -> None:
    """Ensures config files and resources exist before launch."""
    import shutil
    import os
    from pathlib import Path

    legacy_config = config.config_dir / "config.toml"
    if not config.config_file.is_file() and not legacy_config.is_file():
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.config_file.touch()

    if not config.resources_dir.is_dir() or not any(config.resources_dir.iterdir()):
        search_locations = [
            Path(__file__).parent.resolve() / "resources",
            Path("/app/jfshell/resources"),
            Path("/app/share/jfshell/resources"),
            Path("/usr/share/jfshell/resources"),
            Path(os.getenv("JFSHELL_RESOURCES_PATH", "")),
        ]
        res_src = next((p for p in search_locations if p.is_dir()), None)

        if res_src:
            config.data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(res_src, config.resources_dir, dirs_exist_ok=True)


def exist_process(main_py, timeout: float = 3.0) -> None:
    """
    Terminates any other running instance of the main application.

    Instances are found by an exact match of main_py against each process's argv
    in /proc, so unrelated processes are never touched. Each match gets SIGTERM,
    then SIGKILL if it is still alive after the timeout. The launcher that
    started the old instance sees SIGTERM as its child's exit status and stops
    by itself. Waiting here also guarantees the old instance has released the
    D-Bus name and sockets before the new one starts.

    Args:
        main_py (Path): Absolute path of the main.py the launcher runs.
        timeout (float): Seconds to wait after SIGTERM before sending SIGKILL.
    """
    import os
    import time
    import signal
    import contextlib

    target = str(main_py).encode()
    skip = {os.getpid(), os.getppid()}

    def is_running(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat", "rb") as f:
                return f.read().rsplit(b")", 1)[1].split()[0] != b"Z"
        except (OSError, IndexError):
            return False

    pids = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        try:
            with open(f"/proc/{entry.name}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if target in argv:
            pids.append(int(entry.name))

    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout
    while pids and time.monotonic() < deadline:
        time.sleep(0.05)
        pids = [pid for pid in pids if is_running(pid)]

    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


EXIT_NO_IPC = 3  # main.py exit code: jff IPC unavailable, do not restart


def _jff_reachable() -> bool:
    """True if a live jff IPC socket accepts connections."""
    import os

    candidates = []
    env_path = os.environ.get("JFF_SOCKET") or ""
    if env_path:
        candidates.append(env_path)
    else:
        d = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        try:
            candidates = [
                os.path.join(d, n)
                for n in sorted(os.listdir(d))
                if n.startswith("jff.") and n.endswith(".sock")
            ]
        except OSError:
            pass

    for path in candidates:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(path)
            s.close()
            return True
        except OSError:
            continue
    return False


def main() -> None:
    """Main execution flow for the JFShell launcher."""
    import os
    import sys
    import logging
    import threading
    import compileall
    import subprocess
    from pathlib import Path

    setup_logging()
    ConfigClass = get_config_class()
    config = ConfigClass(app_name="jfshell")

    install_root = Path(__file__).parent.resolve()

    threading.Thread(target=run_backup, args=(config,), daemon=True).start()

    gtk_lib = _find_system_library("libgtk4-layer-shell.so")
    if not gtk_lib:
        logging.critical("libgtk4-layer-shell.so not found. Cannot start.")
        sys.exit(1)

    if not _jff_reachable():
        logging.critical("No running jff found (JFF_SOCKET unset / socket missing).")
        sys.exit(1)

    os.environ["LD_PRELOAD"] = str(gtk_lib)
    os.environ["PYTHONPATH"] = str(install_root)
    os.environ["JFSHELL_VENV"] = str(config.venv_python)

    ensure_initial_setup(config)
    manage_virtual_environment(config, install_root / "requirements.txt")

    if os.access(str(install_root), os.W_OK):
        compileall.compile_dir(str(install_root), quiet=1)

    main_py = install_root / "main.py"
    cmd = [str(config.venv_python), "-O", str(main_py)]

    while True:
        if not _jff_reachable():
            logging.critical("jff IPC is not available, not starting.")
            sys.exit(1)
        exist_process(main_py)
        try:
            result = subprocess.run(cmd, check=False)
            if result.returncode == 0 or abs(result.returncode) in {2, 15}:
                logging.info("Application session ended.")
                break
            if result.returncode == EXIT_NO_IPC or not _jff_reachable():
                logging.critical("jff IPC is not available, not restarting.")
                break
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
