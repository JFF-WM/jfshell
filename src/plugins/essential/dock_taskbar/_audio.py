"""PulseAudio mute control for the dock taskbar menus.

A window is matched to its playback streams by process id. Browsers play their
audio from a child process of the one that owns the window, so the descendants
of every window process are matched too.
"""

import threading
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


def process_family(pids: Iterable[object]) -> Set[int]:
    """The given process ids plus all their descendants.

    Entries that are not process ids (None, junk) are skipped. When psutil is
    missing or a process is gone, only the id itself is kept.
    """
    family: Set[int] = set()
    for raw in pids:
        if raw is None or not str(raw).isdigit():
            continue
        pid = int(raw)
        family.add(pid)
        try:
            import psutil

            family.update(c.pid for c in psutil.Process(pid).children(recursive=True))
        except Exception:
            pass
    return family


def process_parents() -> Dict[int, int]:
    """Parent process id of every running process (empty without psutil)."""
    try:
        import psutil

        return {
            p.pid: p.info.get("ppid") or 0 for p in psutil.process_iter(["ppid"])
        }
    except Exception:
        return {}


def owning_pid(
    pid: int, window_pids: Set[int], parents: Dict[int, int]
) -> Optional[int]:
    """The window process that ``pid`` is, or its nearest ancestor that is one."""
    seen: Set[int] = set()
    while pid and pid not in seen:
        if pid in window_pids:
            return pid
        seen.add(pid)
        pid = parents.get(pid, 0)
    return None


def stream_flags(
    streams: Iterable[Tuple[int, bool, bool]],
    owners: Dict[int, str],
    parents: Dict[int, int],
) -> Dict[str, Tuple[bool, bool]]:
    """(playing, muted) per app, from the playback streams of all apps.

    Args:
        streams: (process id, muted, corked) of every playback stream.
        owners: window process id -> key of the app the window belongs to.
        parents: process id -> parent process id.

    A stream belongs to the app whose window process is the stream's own
    process or its nearest ancestor. Paused (corked) streams are ignored, and
    apps without a running stream are left out. ``playing`` means at least one
    running stream is audible, ``muted`` that all running streams are muted.
    """
    windows = set(owners)
    active: Dict[str, List[bool]] = {}
    for pid, muted, corked in streams:
        if corked:
            continue
        owner = owning_pid(pid, windows, parents)
        if owner is not None:
            active.setdefault(owners[owner], []).append(muted)
    return {key: (not all(m), all(m)) for key, m in active.items()}


def audio_state(
    streams: Iterable[Tuple[int, bool]], family: Set[int]
) -> Tuple[bool, bool]:
    """Whether any stream of ``family`` is muted, and whether any is not.

    Args:
        streams: (process id, muted) of every playback stream.
        family: process ids that belong to the app.

    Returns:
        (muted, unmuted). Both are False when the app has no stream.
    """
    muted = unmuted = False
    for pid, is_muted in streams:
        if pid in family:
            if is_muted:
                muted = True
            else:
                unmuted = True
    return muted, unmuted


class DockTaskbarAudio:
    """Reads and sets the mute state of an app's PulseAudio streams."""

    def __init__(self) -> None:
        """The PulseAudio connection is opened on first use."""
        self._pulse = None
        self._watch_stop: Optional[threading.Event] = None

    def _client(self):
        if self._pulse is None:
            import pulsectl

            self._pulse = pulsectl.Pulse("jfshell-dock-taskbar-audio")
        return self._pulse

    def _streams(self):
        """[(process id, stream)] of every playback stream that reports a pid."""
        result = []
        for stream in self._client().sink_input_list():
            pid = stream.proplist.get("application.process.id")
            if pid is not None and str(pid).isdigit():
                result.append((int(pid), stream))
        return result

    def state(self, pids: Iterable[object]) -> Tuple[bool, bool]:
        """(muted, unmuted) for the streams of these window processes."""
        family = process_family(pids)
        if not family:
            return False, False
        try:
            streams = [(pid, bool(s.mute)) for pid, s in self._streams()]
        except Exception:
            self.close()
            return False, False
        return audio_state(streams, family)

    def set_mute(self, pids: Iterable[object], mute: bool) -> None:
        """Mutes or unmutes every stream of these window processes."""
        family = process_family(pids)
        if not family:
            return
        try:
            client = self._client()
            for pid, stream in self._streams():
                if pid in family:
                    client.mute(stream, mute)
        except Exception:
            self.close()

    def flags(
        self, window_pids: Dict[str, Iterable[object]]
    ) -> Dict[str, Tuple[bool, bool]]:
        """(playing, muted) for each app key of ``window_pids`` (key -> window pids).

        Apps that play nothing are left out.
        """
        owners: Dict[int, str] = {}
        for key, pids in window_pids.items():
            for raw in pids:
                if raw is not None and str(raw).isdigit():
                    owners.setdefault(int(raw), key)
        if not owners:
            return {}
        try:
            streams = [(pid, bool(s.mute), bool(s.corked)) for pid, s in self._streams()]
        except Exception:
            self.close()
            return {}
        if not streams:
            return {}
        return stream_flags(streams, owners, process_parents())

    def start_watch(self, on_change: Callable[[], None]) -> None:
        """Calls ``on_change`` from a worker thread when a playback stream changes."""
        self.stop_watch()
        stop = threading.Event()
        self._watch_stop = stop
        threading.Thread(
            target=self._watch,
            args=(stop, on_change),
            name="dock-taskbar-audio-watch",
            daemon=True,
        ).start()

    def stop_watch(self) -> None:
        """Ends the watcher (its thread leaves on the next PulseAudio event)."""
        if self._watch_stop is not None:
            self._watch_stop.set()
            self._watch_stop = None

    @staticmethod
    def _watch(stop: threading.Event, on_change: Callable[[], None]) -> None:
        """Follows PulseAudio sink-input events in its own client and thread."""
        try:
            import pulsectl
        except ImportError:
            return

        def on_event(_ev):
            if stop.is_set():
                raise pulsectl.PulseLoopStop
            on_change()

        while not stop.is_set():
            try:
                with pulsectl.Pulse("jfshell-dock-taskbar-audio-watch") as pulse:
                    pulse.event_mask_set("sink_input")
                    pulse.event_callback_set(on_event)
                    pulse.event_listen()
            except Exception:
                pass
            stop.wait(2)

    def close(self) -> None:
        """Drops the PulseAudio connection (a new one opens on next use)."""
        if self._pulse is not None:
            try:
                self._pulse.close()
            except Exception:
                pass
            self._pulse = None
