import logging
import os
import socket
import json
import typing
from typing import (
    Any,
    Callable,
    Iterator,
    Sequence,
    Type,
    TypeAlias,
    Union,
    Optional,
    List,
    Dict,
    get_origin,
    get_args,
)
from functools import wraps

logger = logging.getLogger(__name__)

TypeInput: TypeAlias = Type | list[Type] | tuple[Type, ...]


def _jff_socket_path() -> Optional[str]:
    p = os.getenv("JFF_SOCKET")
    if p:
        return p
    d = os.getenv("XDG_RUNTIME_DIR") or "/tmp"
    try:
        for name in sorted(os.listdir(d)):
            if name.startswith("jff.") and name.endswith(".sock"):
                cand = os.path.join(d, name)
                try:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(cand)
                    s.close()
                    return cand
                except OSError:
                    continue
    except OSError:
        pass
    return None


def jff_msg(cmd: str, timeout: float = 2.0) -> dict:
    path = _jff_socket_path()
    if not path:
        raise ConnectionRefusedError("no running jff found")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(cmd.encode() + b"\n")
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "{}")
    finally:
        s.close()


def jff_snapshot(groups: Sequence[str] = (), timeout: float = 2.0) -> dict:
    """Return the ``snapshot`` event jff sends first on ``watch [GROUP...]``.

    One-shot commands (``status``, ``views``...) don't report outputs, layers or
    the overview, the watch snapshot does. The connection is closed right after
    the first line, so nothing keeps streaming.
    """
    path = _jff_socket_path()
    if not path:
        raise ConnectionRefusedError("no running jff found")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        # No shutdown(SHUT_WR) here: EOF from the client ends a watch.
        s.sendall((" ".join(["watch", *groups]) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0].decode() or "{}")
    finally:
        s.close()


def jff_watch(
    groups: Sequence[str] = (), timeout: float = 1.0
) -> Iterator[Optional[dict]]:
    """Stream jff's ``watch [GROUP...]`` events, one parsed dict per line.

    The first event is the ``snapshot``. ``None`` is yielded whenever nothing
    arrived for ``timeout`` seconds so the caller can check whether to stop.
    Raises ``ConnectionError``/``OSError`` when jff goes away.
    """
    path = _jff_socket_path()
    if not path:
        raise ConnectionRefusedError("no running jff found")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall((" ".join(["watch", *groups]) + "\n").encode())
        buf = b""
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                yield None
                continue
            if not chunk:
                raise ConnectionError("jff closed the watch connection")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    yield json.loads(line.decode("utf-8"))
                except ValueError:
                    logger.warning("jff watch: unparsable line %r", line[:200])
    finally:
        s.close()


def validate_type(
    value: Any,
    expected_type: TypeInput,
    name: str = "value",
    allow_none: bool = False,
    optional: bool = False,
) -> bool:
    current_type = expected_type
    if optional:
        allow_none = True
    origin = get_origin(current_type)
    args = get_args(current_type)
    if origin is Union and type(None) in args:
        allow_none = True
        inner = tuple(t for t in args if t is not type(None))
        current_type = inner[0] if len(inner) == 1 else Union[inner]
        origin = get_origin(current_type)
        args = get_args(current_type)
    if allow_none and value is None:
        return True
    if value is None:
        raise TypeError(f"Invalid {name}: Expected {expected_type}, got None.")
    if current_type is Any:
        return True
    if origin is Union:
        for t in args:
            try:
                if validate_type(value, t, name=name):
                    return True
            except TypeError:
                continue
        names = ", ".join(t.__name__ for t in args if hasattr(t, "__name__"))
        raise TypeError(
            f"Invalid {name}: Expected one of ({names}), got {type(value).__name__}"
        )
    if origin in (list, tuple):
        if not isinstance(value, origin):
            raise TypeError(f"Invalid {name}: Expected {origin.__name__}")
        return True
    if not isinstance(current_type, type):
        return True
    if not isinstance(value, current_type):
        raise TypeError(
            f"Invalid {name}: Expected {current_type.__name__}, got {type(value).__name__}"
        )
    return True


def type_checked(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            annotations = typing.get_type_hints(func)
        except NameError:
            return func(*args, **kwargs)
        for arg_name, arg_value in kwargs.items():
            expected = annotations.get(arg_name)
            if expected:
                validate_type(arg_value, expected, name=arg_name)
        return func(*args, **kwargs)

    return wrapper


def apply_type_checked(cls: type) -> type:
    for name, method in vars(cls).items():
        if isinstance(method, (classmethod, staticmethod)):
            setattr(cls, name, type(method)(type_checked(method.__func__)))
        elif callable(method) and not name.startswith("__"):
            setattr(cls, name, type_checked(method))
    return cls


def handle_ipc_error(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (
            socket.error,
            ConnectionRefusedError,
            BrokenPipeError,
            json.JSONDecodeError,
            RuntimeError,
        ) as e:
            logger.error(f"jff IPC error in '{func.__name__}': {e}")
            self.is_connected_flag = False
            return None
        except Exception as e:
            logger.error(f"Unexpected error in '{func.__name__}': {e}")
            return None

    return wrapper


@apply_type_checked
class IPC:
    def __init__(self):
        self.is_connected_flag = False
        self.socket_path = _jff_socket_path()
        self.connect_compositor()

    def connect_compositor(self) -> bool:
        try:
            jff_msg("status")
            self.is_connected_flag = True
            self.socket_path = _jff_socket_path()
            logger.info("Connected to jff.")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to jff: {e}")
            self.is_connected_flag = False
            return False

    def ensure_ipc_connection(self) -> bool:
        if not self.is_connected():
            self.connect_compositor()
        return self.is_connected()

    def is_connected(self) -> bool:
        return self.is_connected_flag

    def close(self) -> None:
        self.is_connected_flag = False

    @handle_ipc_error
    def clear_bindings(self):
        return False

    @handle_ipc_error
    def list_views(self) -> List[Dict[str, Any]]:
        r = jff_msg("status")
        if not r.get("ok"):
            return []
        return [self._view_to_jfshell(v) for v in r.get("views", [])]

    @handle_ipc_error
    def get_windows(self):
        return self.list_views()

    @handle_ipc_error
    def list_workspaces(self) -> List[Dict[str, Any]]:
        return self.get_workspaces()

    @handle_ipc_error
    def get_workspaces(self) -> List[Dict[str, Any]]:
        r = jff_msg("workspaces")
        if not r.get("ok"):
            return []
        cur, count = r.get("workspace", 1), r.get("count", 1)
        return [
            {
                "id": i,
                "idx": i,
                "name": str(i),
                "is_active": i == cur,
                "is_focused": i == cur,
                "output": None,
            }
            for i in range(1, count + 1)
        ]

    @handle_ipc_error
    def list_outputs(self) -> List[Dict[str, Any]]:
        """Every monitor jff drives, with the area it covers in the shared space.

        Taken from the ``watch`` snapshot: ``status`` only names monitors that
        have a window on them. Callers index this like a list
        (``outputs[0]["name"]``, ``for o in outputs``).
        """
        snap = jff_snapshot(("outputs",))
        return [self._output_to_jfshell(o) for o in snap.get("outputs", [])]

    @handle_ipc_error
    def get_version(self) -> str:
        return "jff"

    @handle_ipc_error
    def get_focused_view(self) -> Optional[Dict[str, Any]]:
        r = jff_msg("focused")
        if not r.get("ok") or not r.get("view"):
            return None
        return self._view_to_jfshell(r["view"])

    @handle_ipc_error
    def get_view(self, id: int) -> Optional[Dict[str, Any]]:
        r = jff_msg(f"view-info {int(id)}")
        if not r.get("ok") or not r.get("view"):
            return None
        return self._view_to_jfshell(r["view"])

    @handle_ipc_error
    def get_active_workspace_number(self) -> Optional[int]:
        r = jff_msg("workspaces")
        return r.get("workspace") if r.get("ok") else None

    @handle_ipc_error
    def get_focused_workspace(self) -> Optional[Dict[str, Any]]:
        r = jff_msg("workspaces")
        if not r.get("ok"):
            return None
        return {
            "id": r["workspace"],
            "idx": r["workspace"],
            "is_active": True,
            "is_focused": True,
        }

    @handle_ipc_error
    def get_focused_output(self) -> Optional[Dict[str, Any]]:
        fv = self.get_focused_view()
        if not fv or not fv.get("output"):
            return None
        name = fv["output"]
        return self.get_output(name) or {"name": name, "id": name}

    @handle_ipc_error
    def get_workspace_views(self) -> List[Dict[str, Any]]:
        cur = self.get_active_workspace_number()
        if cur is None:
            return []
        return [v for v in self.list_views() if v.get("workspace") == cur]

    @handle_ipc_error
    def get_workspace_by_name(self, name: str):
        return None

    @handle_ipc_error
    def close_view(self, view_id: int) -> bool:
        return bool(jff_msg(f"close {int(view_id)}").get("ok"))

    @handle_ipc_error
    def set_focus(self, view_id: int) -> bool:
        return bool(jff_msg(f"focus {int(view_id)}").get("ok"))

    @handle_ipc_error
    def focus_workspace(self, number: int) -> bool:
        return bool(jff_msg(f"workspace {int(number)}").get("ok"))

    @handle_ipc_error
    def set_workspace(self, number: int) -> bool:
        return self.focus_workspace(number)

    @handle_ipc_error
    def focus_workspace_up(self) -> bool:
        return bool(jff_msg("workspace prev").get("ok"))

    @handle_ipc_error
    def focus_workspace_down(self) -> bool:
        return bool(jff_msg("workspace next").get("ok"))

    @handle_ipc_error
    def move_window_to_workspace(self, ws_reference, view_id=None) -> bool:
        n = ws_reference if isinstance(ws_reference, int) else int(str(ws_reference))
        cmd = f"move-to-workspace {n}" + (
            f" {int(view_id)}" if view_id is not None else ""
        )
        return bool(jff_msg(cmd).get("ok"))

    @handle_ipc_error
    def fullscreen_window(self, view_id=None) -> bool:
        cmd = "fullscreen" + (f" {int(view_id)}" if view_id is not None else "")
        return bool(jff_msg(cmd).get("ok"))

    @handle_ipc_error
    def maximize_column(self, view_id=None) -> bool:
        cmd = "maximize" + (f" {int(view_id)}" if view_id is not None else "")
        return bool(jff_msg(cmd).get("ok"))

    @handle_ipc_error
    def set_view_fullscreen(self, view_id, on: bool = True) -> bool:
        cmd = ("fullscreen" if on else "unfullscreen") + (
            f" {int(view_id)}" if view_id is not None else ""
        )
        return bool(jff_msg(cmd).get("ok"))

    @handle_ipc_error
    def set_view_maximized(self, view_id=None, on: bool = True) -> bool:
        cmd = ("maximize" if on else "unmaximize") + (
            f" {int(view_id)}" if view_id is not None else ""
        )
        return bool(jff_msg(cmd).get("ok"))

    @handle_ipc_error
    def set_tiling_maximized(self, view_id, on: bool = True) -> bool:
        return self.set_view_maximized(view_id, on)

    @handle_ipc_error
    def get_views_from_active_workspace(self) -> List[int]:
        return [v["id"] for v in self.get_workspace_views() or []]

    @handle_ipc_error
    def set_view_always_on_top(self, view_id, on: bool = True) -> bool:
        # jff has no always-on-top state.
        return False

    @handle_ipc_error
    def set_view_sticky(self, view_id, on: bool = True) -> bool:
        # jff has no sticky state.
        return False

    @handle_ipc_error
    def run_cmd(self, cmd: str) -> bool:
        """Run ``cmd`` through jff's ``spawn-sh`` (i.e. ``sh -c <cmd>``).

        The child inherits jff's environment, so it sees WAYLAND_DISPLAY,
        DISPLAY (xwayland-satellite), XCURSOR_*, JFF_SOCKET, etc.
        """
        return bool(jff_msg(f"spawn-sh {cmd}").get("ok"))

    @handle_ipc_error
    def toggle_overview(self) -> bool:
        return bool(jff_msg("toggle-overview").get("ok"))

    @handle_ipc_error
    def open_overview(self) -> bool:
        return self._set_overview(True)

    @handle_ipc_error
    def close_overview(self) -> bool:
        return self._set_overview(False)

    def _set_overview(self, want: bool) -> bool:
        """jff only has ``toggle-overview``, so check the state first.

        The reply says which state it ended in (it can decline, e.g. while a
        window is being moved), so the result is whether ``want`` was reached.
        """
        if self.is_overview_active() == want:
            return True
        r = jff_msg("toggle-overview")
        return bool(r.get("ok")) and bool(r.get("open")) == want

    @handle_ipc_error
    def set_option_values(self, values):
        return {"status": "success"}

    @handle_ipc_error
    def get_option_value(self, option):
        return {"value": "alpha animate"} if option == "core/plugins" else {"value": ""}

    @handle_ipc_error
    def connect_client(self, path):
        return True

    @handle_ipc_error
    def list_input_devices(self):
        return []

    @handle_ipc_error
    def configure_input_device(self, id, enabled):
        pass

    @handle_ipc_error
    def set_view_alpha(self, *a, **k):
        return False

    @handle_ipc_error
    def get_view_alpha(self, view_id):
        return {"alpha": 1.0}

    @handle_ipc_error
    def set_view_property(self, *a, **k):
        return False

    @handle_ipc_error
    def get_view_property(self, *a, **k):
        return None

    @handle_ipc_error
    def configure_view(self, *a, **k):
        return False

    @handle_ipc_error
    def move_cursor(self, x, y) -> bool:
        """Put the pointer at x, y (pixels in the shared space)."""
        r = jff_msg(f"move-cursor {int(round(x))} {int(round(y))}")
        return bool(r.get("ok"))

    @handle_ipc_error
    def get_output(self, output_id):
        for o in self.list_outputs() or []:
            if o["id"] == output_id or o["name"] == output_id:
                return o
        return None

    @handle_ipc_error
    def list_layers(self):
        return jff_snapshot(("layers",)).get("layers", [])

    @handle_ipc_error
    def get_keyboard_layouts(self):
        return {}

    @handle_ipc_error
    def pick_window(self):
        return None

    @handle_ipc_error
    def pick_color(self):
        return None

    @handle_ipc_error
    def get_overview_state(self):
        return {"is_open": self.is_overview_active()}

    @handle_ipc_error
    def quit(self, skip_confirmation=False):
        return False

    @handle_ipc_error
    def toggle_window_floating(self, view_id=None):
        return False

    @handle_ipc_error
    def focus_column(self, number):
        return False

    @handle_ipc_error
    def focus_column_right(self):
        return False

    @handle_ipc_error
    def focus_column_left(self):
        return False

    @handle_ipc_error
    def focus_column_last(self):
        return False

    @handle_ipc_error
    def focus_column_right_or_first(self):
        return False

    @handle_ipc_error
    def focus_column_or_monitor_left(self):
        return False

    @handle_ipc_error
    def focus_column_left_or_last(self):
        return False

    @handle_ipc_error
    def focus_monitor_right(self):
        return False

    @handle_ipc_error
    def focus_monitor_left(self):
        return False

    @handle_ipc_error
    def focus_monitor_up(self):
        return False

    @handle_ipc_error
    def focus_monitor_down(self):
        return False

    @handle_ipc_error
    def center_column(self):
        return False

    @handle_ipc_error
    def set_column_width(self, change):
        return False

    @handle_ipc_error
    def is_overview_active(self) -> bool:
        return bool(jff_snapshot(("modes",)).get("overview"))

    @handle_ipc_error
    def move_column_left(self):
        return False

    @handle_ipc_error
    def move_column_right(self):
        return False

    @handle_ipc_error
    def move_column_to_monitor_left(self):
        return False

    @handle_ipc_error
    def move_column_to_monitor_right(self):
        return False

    @handle_ipc_error
    def move_window_down_or_to_workspace_below(self):
        return False

    @handle_ipc_error
    def move_window_up_or_to_workspace_above(self):
        return False

    @handle_ipc_error
    def set_column_display(self, display):
        return False

    @handle_ipc_error
    def set_dynamic_cast_window(self, view_id=None):
        return False

    @handle_ipc_error
    def set_dynamic_cast_monitor(self, output_name=None):
        return False

    @handle_ipc_error
    def clear_dynamic_cast_target(self):
        return False

    @handle_ipc_error
    def stop_cast(self, session_id):
        return False

    @handle_ipc_error
    def watch(self, events=None):
        return None

    @handle_ipc_error
    def read_next_event(self):
        return None

    @staticmethod
    def _view_to_jfshell(v: dict) -> dict:
        return {
            "id": v.get("id"),
            "title": v.get("title") or "",
            "app_id": v.get("app_id") or "nil",
            "app-id": v.get("app_id") or "nil",
            "pid": -1,
            "workspace_id": v.get("workspace"),
            "workspace": v.get("workspace"),
            "is_focused": bool(v.get("focused")),
            "focused": bool(v.get("focused")),
            "fullscreen": bool(v.get("fullscreen")),
            "maximized": bool(v.get("maximized")),
            "output": v.get("output"),
            "output-name": v.get("output"),
            "output-id": v.get("output"),
            "always-on-top": False,
            "sticky": False,
            "geometry": {
                "x": v.get("x", 0),
                "y": v.get("y", 0),
                "width": v.get("width", 0),
                "height": v.get("height", 0),
            },
            "role": "toplevel",
            "type": "toplevel",
            "mapped": True,
            "focusable": True,
        }

    @staticmethod
    def _output_to_jfshell(o: dict) -> dict:
        geo = {
            "x": o.get("x", 0),
            "y": o.get("y", 0),
            "width": o.get("width", 0),
            "height": o.get("height", 0),
        }
        return {
            "id": o.get("name"),
            "name": o.get("name"),
            "geometry": dict(geo),
            "workarea": dict(geo),
            "logic": dict(geo),
            "scanout": bool(o.get("scanout")),
        }
