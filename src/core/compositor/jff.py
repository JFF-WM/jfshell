import json
import logging
import os
import socket
from typing import Optional

logger = logging.getLogger(__name__)


class JffSocket:
    """Client for the jff compositor's line-based IPC socket.

    The socket is at $JFF_SOCKET (exported by jff itself) or, failing that, the
    first live ``jff.*.sock`` in $XDG_RUNTIME_DIR. Each request is one line of
    text; each reply is one line of JSON (``{"ok": true, ...}`` or
    ``{"ok": false, "error": "..."}``).
    """

    def __init__(self) -> None:
        self.socket_path = self._find_socket()

    def _find_socket(self) -> Optional[str]:
        path = os.environ.get("JFF_SOCKET")
        if path and os.path.exists(path):
            return path
        runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        try:
            for name in os.listdir(runtime):
                if name.startswith("jff.") and name.endswith(".sock"):
                    full = os.path.join(runtime, name)
                    if os.path.exists(full):
                        return full
        except OSError:
            pass
        return None

    def query(self, command: str) -> dict:
        """Send one request line and return the parsed JSON reply."""
        if not self.socket_path:
            raise RuntimeError("jff IPC socket not found (is jff running?)")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(self.socket_path)
            sock.sendall(command.encode("utf-8") + b"\n")
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        line = b"".join(chunks).split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))

    def spawn_sh(self, command_line: str) -> bool:
        """Run ``command_line`` through ``sh -c`` inside the compositor's env."""
        reply = self.query(f"spawn-sh {command_line}")
        return bool(reply.get("ok"))
