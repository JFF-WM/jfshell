# About JFShell, User Guide & Overview

**JFShell** is a lightweight, modular, and highly-customizable GTK4/Libadwaita status panel built for the **jff** Wayland compositor. It aims to feel like a modern shell panel while staying fast and extensible through plugins and a single KDL-based configuration.

> This document is an expanded, user-focused **ABOUT** that extracts implementation details from the project sources and turns them into practical guidance for end users and power users.

---

## Quick start (for most users)

### 1\. Clone & run (developer / source install)

    git clone <your-jfshell-repository-url>
    cd jfshell
    python run.py      # creates venv, copies defaults, installs deps, and starts the app

`run.py` will detect or copy default configs, look for the GTK4 Layer Shell library (preloads `libgtk4-layer-shell.so` if found), create a per-user venv (`~/.local/share/jfshell/venv`) and install `requirements.txt` there before launching `main.py`.

### Restarting / stopping

    # stop running panel processes (dev)
    pkill -f jfshell/main.py

    # or simple restart from cloned repo
    python run.py

If you installed via packages (AUR, distro packages) follow the package instructions, but the `run.py` flow above is the safe manual path for first-run configuration.

## Architecture (high level)

- **run.py:** Prepares runtime environment: finds installed package path or development path, ensures GTK layer-shell is available (exits if not), copies default config when needed, creates a user virtualenv and installs dependencies, then runs `main.py`.
- **main.py:** Orchestrates app initialization: sets up logging, finds the active config, starts an IPC server thread (EventServer) and then loads the Panel application.
- **Panel (`src/panel.py`):** The central GTK application (inherits from Adw.Application). It:
  - Loads and watches `config.kdl` (single source of truth).
  - Sets monitor dimensions and creates panel windows using `CreatePanel`.
  - Initializes `PluginLoader` and loads plugins asynchronously.
- **PluginLoader:** Discovers, validates and initializes plugins. Requires plugins to expose `get_plugin_metadata()` and `get_plugin_class()`. User plugins live under `~/.local/share/jfshell/plugins`.
- **Control Center:** A small GTK/Adwaita GUI tool to edit `config.kdl` without manually editing the file.

## Where JFShell stores / finds configuration & defaults

- **User config:** `~/.config/jfshell/config.kdl` (the primary location).
- **System/default config:** Typical system paths (e.g. `/usr/lib/jfshell/...`). If no user config exists, JFShell will try to copy default config from system or development locations.

## Logs & troubleshooting basics

- **Log file:** JFShell writes a rotating log to: `~/.local/state/jfshell/jfshell.log`. Use this file as the first step when diagnosing crashes or plugin load errors.
- **When things don't start:** Check `run.py` output; it explicitly errors out if `libgtk4-layer-shell.so` cannot be found.

## Panels & displays (how JFShell draws windows)

Panel windows are created using `CreatePanel` which uses `Gtk4LayerShell` to anchor panels to screen edges.

- **Monitor selection:** JFShell reads `panel.primary_output` (or `monitor.name` in config). It has a helper to choose a monitor with fallbacks to compositor-reported outputs.
- **Event system:** The project includes a central `event_manager` plugin that other plugins can use to subscribe to compositor events. It dispatches events to subscribers safely on the GTK main thread using `GLib.idle_add`.

## IPC & compositor integration

JFShell starts an asynchronous IPC server (`EventServer`) in a separate thread to receive commands/events from external scripts. JFShell talks to jff through its IPC socket (`$JFF_SOCKET`, or the first live `jff.*.sock` in `$XDG_RUNTIME_DIR`).

## For power users / developers

### Plugin Lifecycle (Updated for Asynchronous API)

The modern lifecycle is asynchronous and requires adherence to the JFShell Coding Protocol:

    def get_plugin_metadata(panel_instance):  # Defines placement and dependencies.

    def get_plugin_class():  # The main entry point. STRICTLY PROHIBITED to have imports at the top level.
        # All imports (e.g., gi.repository.Gtk, BasePlugin) MUST be inside this function.

    def on_enable():  # The primary activation method.
        # This is where you initialize widgets and register settings hints.

    def on_disable():  # The primary deactivation method.
        # Handles custom cleanup; BasePlugin automatically cancels background tasks.

### BasePlugin API Reference

Inheriting from `BasePlugin` gives your plugin access to a rich set of non-blocking utilities:

Method / Property

Description

`self.run_in_thread(func)`

Offloads blocking I/O to a background thread pool.

`self.schedule_in_gtk_thread(func)`

Safely updates the UI from a background thread.

`self.get_plugin_setting(key, default)`

Retrieves persistent settings from `config.kdl`.

`self.get_plugin_setting_add_hint()`

Registers settings descriptions for the Control Center.

`self.notify_send(title, msg)`

Dispatches a system notification.

`self.run_cmd(cmd)`

Executes shell commands non-blockingly.

### jff IPC Events (Monitoring State)

Plugins subscribe via `self.ipc_server` to events such as:

- `view-focused`: Window focus changed.
- `view-mapped / unmapped`: Window created/closed.
- `workspace-activated`: User switched workspaces.
- `output-layout-changed`: Monitor configuration updated.
