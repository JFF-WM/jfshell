## jfshell

JFShell is a lightweight, modular and highly customizable status panel for the jff Wayland compositor. It is written in Python on GTK 4, Libadwaita and gtk4-layer-shell, and it behaves like a small shell: top, bottom, left and right panels with their own styling, and plugins that add widgets (system monitor, app launcher, taskbar, dockbar, clock and more) or handle gestures.

JFShell talks to jff over its IPC socket (`$JFF_SOCKET`, or the first live `jff.*.sock` in `$XDG_RUNTIME_DIR`) and supports **jff only**.

### Quick start

```sh
git clone https://github.com/killown/jfshell.git
cd jfshell
python run.py
```

`run.py` needs a running jff session and `libgtk4-layer-shell` installed. It creates a virtualenv, installs the dependencies and starts the panel. Your configuration lives in `~/.config/jfshell/config.toml`.

## Credits

The look and feel of JFShell's interface was heavily inspired by [GNOME Shell](https://www.gnome.org/), whose design language shaped most of the panel, dialogs and overall visual style. Some aspects of the UI were also inspired by [Noctalia](https://github.com/noctalia-dev/noctalia-shell). Full credit for those design ideas goes to the GNOME and Noctalia projects and their designers and contributors.

JFShell is an independent project and is not affiliated with or endorsed by GNOME or Noctalia.

## License

jfshell is licensed under the **AGPLv3 (GNU Affero General Public License version 3)**. [See LICENSE for more information](https://github.com/killown/jfshell/blob/main/LICENSE).
