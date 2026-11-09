import os
import asyncio
import inspect
import threading
import orjson as json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator

from src.core.compositor.ipc import IPC, jff_watch
from src.plugins.core._event_loop import get_global_loop


class EventServer:
    def __init__(self, logger):
        self.logger = logger
        self.ipcet_paths = [self.get_socket_path()]
        self._cleanup_sockets()
        self.ipc = IPC()
        self.compositor = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.event_queue = asyncio.Queue()
        self.clients = []
        self.event_subscribers = {}
        self.command_handlers = {}
        self.loop = get_global_loop
        self._stopping = threading.Event()

    def _cleanup_sockets(self) -> None:
        for path in self.ipcet_paths:
            if os.path.exists(path):
                os.remove(path)

    def reconnect_ipc(self) -> bool:
        try:
            return self.ipc.connect_compositor()
        except Exception as e:
            self.logger.error(f"Failed to connect to jff: {e}")
            return False

    def is_socket_active(self) -> bool:
        if not self.ipc.is_connected():
            return self.reconnect_ipc()
        return True

    @staticmethod
    def _view_msg(v):
        return {
            "id": v["id"],
            "title": v.get("title", "") or "",
            "app-id": v.get("app_id") or "nil",
            "app_id": v.get("app_id") or "nil",
            "fullscreen": bool(v.get("fullscreen")),
            "workspace": v.get("workspace"),
            "output": v.get("output"),
        }

    @classmethod
    def _translate(cls, ev: dict, state: dict) -> Iterator[dict]:
        """Turn one jff ``watch`` event into the panel's internal events.

        ``state`` is owned by ``read_events`` (``views``: id -> last known view,
        ``overview``: bool). Internal names: view-mapped, view-unmapped,
        view-focused, view-title-changed, view-app-id-changed,
        workspace-changed, overview-changed, view-drag-changed.
        """
        kind = ev.get("event")
        views: dict = state["views"]

        if kind == "snapshot":
            old = dict(views)
            new = {v["id"]: v for v in ev.get("views", [])}
            views.clear()
            views.update(new)
            for vid, v in new.items():
                if vid not in old:
                    yield {"event": "view-mapped", "view": cls._view_msg(v)}
                if v.get("focused") and not old.get(vid, {}).get("focused"):
                    yield {"event": "view-focused", "view": cls._view_msg(v)}
            for vid, v in old.items():
                if vid not in new:
                    yield {"event": "view-unmapped", "view": cls._view_msg(v)}
            if "overview" in ev and bool(ev["overview"]) != state["overview"]:
                state["overview"] = bool(ev["overview"])
                yield cls._overview_msg(state["overview"])

        elif kind == "view-opened":
            views[ev["id"]] = ev
            yield {"event": "view-mapped", "view": cls._view_msg(ev)}

        elif kind == "view-closed":
            v = views.pop(ev["id"], {"id": ev["id"]})
            yield {"event": "view-unmapped", "view": cls._view_msg(v)}

        elif kind == "title" and ev["id"] in views:
            views[ev["id"]]["title"] = ev.get("title")
            yield {
                "event": "view-title-changed",
                "view": cls._view_msg(views[ev["id"]]),
            }

        elif kind == "app-id" and ev["id"] in views:
            views[ev["id"]]["app_id"] = ev.get("app_id")
            yield {
                "event": "view-app-id-changed",
                "view": cls._view_msg(views[ev["id"]]),
            }

        elif kind == "focus":
            prev, cur = ev.get("previous"), ev.get("id")
            if prev in views:
                views[prev]["focused"] = False
            if cur is not None and cur in views:
                views[cur]["focused"] = True
                yield {"event": "view-focused", "view": cls._view_msg(views[cur])}

        elif kind in ("maximized", "fullscreen") and ev["id"] in views:
            views[ev["id"]][kind] = bool(ev.get("on"))

        elif kind == "view-workspace" and ev["id"] in views:
            views[ev["id"]]["workspace"] = ev.get("workspace")

        elif kind == "geometry" and ev["id"] in views:
            for key in ("x", "y", "width", "height"):
                views[ev["id"]][key] = ev.get(key)

        elif kind == "workspace":
            yield {
                "event": "workspace-changed",
                "workspace": {"id": ev.get("workspace")},
                "count": ev.get("count"),
            }

        elif kind == "overview":
            state["overview"] = bool(ev.get("open"))
            yield cls._overview_msg(state["overview"])

        elif kind == "drag":
            vid, on = ev.get("id"), bool(ev.get("on"))
            yield {
                "event": "view-drag-changed",
                "type": "view-drag-changed",
                "state": on,
                "payload": {"id": vid, "dragging": on},
                "view": cls._view_msg(views.get(vid, {"id": vid})),
                # Whether the overview was open, so a panel can tell a drag
                # from the overview from a titlebar drag.
                "overview": state["overview"],
            }

    @staticmethod
    def _overview_msg(is_open: bool) -> dict:
        return {
            "event": "overview-changed",
            "type": "overview-changed",
            "state": is_open,
            "payload": {"is_open": is_open},
        }

    def _emit(self, ev: dict) -> bool:
        """Queue ``ev`` for the asyncio side. False means the loop is gone."""
        loop = self.loop
        if not hasattr(loop, "is_closed") or loop.is_closed():
            return False
        try:
            asyncio.run_coroutine_threadsafe(self.event_queue.put(ev), loop)
        except RuntimeError:
            return False
        return True

    def read_events(self) -> None:
        """Follow jff's ``watch`` stream (no polling) and feed ``event_queue``."""
        state: dict[str, Any] = {"views": {}, "overview": False}
        while not self._stopping.is_set():
            if not self.is_socket_active():
                time.sleep(2)
                continue
            try:
                for ev in jff_watch():
                    if self._stopping.is_set():
                        return
                    if ev is None:
                        continue
                    for out in self._translate(ev, state):
                        if not self._emit(out):
                            return
            except Exception as e:
                self.logger.error(f"jff watch interrupted: {e}")
                # Make is_socket_active() check jff again before reconnecting.
                self.ipc.is_connected_flag = False
                time.sleep(1)

    def register_command(self, command_name: str, handler) -> None:
        self.command_handlers[command_name] = handler

    def add_event_subscriber(self, event_type, callback) -> None:
        self.event_subscribers.setdefault(event_type, []).append(callback)

    def _invoke(self, cb: Callable, msg: dict, ev: Any) -> None:
        """Call a subscriber. Plain functions run right away, coroutine
        functions are scheduled (``create_task(cb(msg))`` raised for plain
        functions, which return None, and logged an error on every event)."""
        try:
            result = cb(msg)
            if inspect.isawaitable(result):
                asyncio.ensure_future(result)
        except Exception as e:
            self.logger.error(f"Error in callback for {ev}: {e}")

    def handle_msg(self, msg) -> None:
        ev = msg.get("event")
        for cb in self.event_subscribers.get(ev, []):
            self._invoke(cb, msg, ev)

    async def handle_event(self) -> None:
        while True:
            event = await self.event_queue.get()
            if not event:
                continue
            data = json.dumps(event) + b"\n"
            for client in self.clients[:]:
                try:
                    client.write(data)
                    await client.drain()
                except (ConnectionResetError, BrokenPipeError):
                    self.clients.remove(client)
            ev = event.get("event")
            for cb in self.event_subscribers.get(ev, []):
                self._invoke(cb, event, ev)

    async def handle_client(self, reader, writer) -> None:
        self.clients.append(writer)
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except Exception:
                    writer.write(
                        json.dumps({"status": "error", "message": "Invalid JSON"})
                        + b"\n"
                    )
                    await writer.drain()
                    continue
                cmd = message.get("command")
                args = message.get("args", [])
                if cmd in self.command_handlers:
                    try:
                        resp = self.command_handlers[cmd](args)
                        if isinstance(resp, dict):
                            writer.write(json.dumps(resp) + b"\n")
                            await writer.drain()
                    except Exception as e:
                        self.logger.error(f"Command execution error: {e}")
                else:
                    writer.write(
                        json.dumps({"status": "error", "message": "Unknown command"})
                        + b"\n"
                    )
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        finally:
            if writer in self.clients:
                self.clients.remove(writer)
            writer.close()
            await writer.wait_closed()

    async def start_server(self, path) -> None:
        server = await asyncio.start_unix_server(
            lambda r, w: self.handle_client(r, w), path=path
        )
        async with server:
            await server.serve_forever()

    async def main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.executor.submit(self.read_events)
        servers = [self.start_server(p) for p in self.ipcet_paths]
        try:
            await asyncio.gather(*servers, self.handle_event())
        finally:
            self._stopping.set()
            self._cleanup_sockets()

    def get_socket_path(self) -> str:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        return os.path.join(runtime_dir, "jfshell.sock")
