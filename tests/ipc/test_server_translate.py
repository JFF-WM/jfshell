import unittest

from src.ipc.server import EventServer


def _state():
    return {"windows": {}, "overview": False, "annotations": {}}


def _ann(aid, window=3, path="/menu"):
    return {
        "id": aid,
        "window": window,
        "pid": 42,
        "interface": "com.canonical.dbusmenu",
        "service_name": ":1.7",
        "object_path": path,
    }


def _run(events, state=None):
    state = state if state is not None else _state()
    out = []
    for ev in events:
        out.extend(EventServer._translate(ev, state))
    return out


class DbusAnnotationTests(unittest.TestCase):
    def test_a_new_annotation_is_forwarded(self):
        ev = {"event": "dbus-annotation", **_ann(1)}
        (msg,) = _run([ev])
        assert msg["event"] == "dbus-annotation"
        assert msg["annotation"] == _ann(1)

    def test_a_removed_annotation_is_forwarded_and_forgotten(self):
        state = _state()
        out = _run(
            [
                {"event": "dbus-annotation", **_ann(1)},
                {"event": "dbus-annotation-removed", "id": 1},
            ],
            state,
        )
        assert out[-1] == {"event": "dbus-annotation-removed", "id": 1}
        assert state["annotations"] == {}

    def test_removing_an_unknown_annotation_still_reports_it(self):
        out = _run([{"event": "dbus-annotation-removed", "id": 9}])
        assert out == [{"event": "dbus-annotation-removed", "id": 9}]

    def test_the_snapshot_reports_what_changed_since_the_last_one(self):
        state = _state()
        first = {
            "event": "snapshot",
            "windows": [],
            "dbus_annotations": [_ann(1), _ann(2)],
        }
        out = _run([first], state)
        assert [m["annotation"]["id"] for m in out] == [1, 2]
        second = {
            "event": "snapshot",
            "windows": [],
            "dbus_annotations": [_ann(2, path="/other"), _ann(3)],
        }
        out = _run([second], state)
        kinds = [(m["event"], m.get("id") or m["annotation"]["id"]) for m in out]
        assert kinds == [
            ("dbus-annotation", 2),
            ("dbus-annotation", 3),
            ("dbus-annotation-removed", 1),
        ]

    def test_a_snapshot_without_annotations_leaves_them_alone(self):
        state = _state()
        _run([{"event": "dbus-annotation", **_ann(1)}], state)
        out = _run([{"event": "snapshot", "windows": []}], state)
        assert out == []
        assert 1 in state["annotations"]

    def test_a_state_from_before_annotations_existed_still_works(self):
        state = {"windows": {}, "overview": False}
        out = _run([{"event": "dbus-annotation", **_ann(1)}], state)
        assert out[0]["annotation"]["id"] == 1


class OverviewOutputTests(unittest.TestCase):
    def test_an_event_carries_the_monitor(self):
        (msg,) = _run([{"event": "overview", "open": True, "output": "DP-1"}])
        assert msg["event"] == "overview-changed"
        assert msg["state"] is True
        assert msg["output"] == "DP-1"
        assert msg["payload"] == {"is_open": True, "output": "DP-1"}

    def test_closing_reports_the_monitor_it_was_open_on(self):
        state = _state()
        _run([{"event": "overview", "open": True, "output": "DP-1"}], state)
        (msg,) = _run([{"event": "overview", "open": False}], state)
        assert msg["state"] is False
        assert msg["output"] == "DP-1"
        assert state["overview_output"] is None

    def test_moving_to_another_monitor_sends_a_new_event(self):
        out = _run(
            [
                {"event": "overview", "open": True, "output": "DP-1"},
                {"event": "overview", "open": True, "output": "DP-2"},
            ]
        )
        assert [m["output"] for m in out] == ["DP-1", "DP-2"]
        assert all(m["state"] for m in out)

    def test_the_snapshot_reports_the_monitor(self):
        snap = {
            "event": "snapshot",
            "windows": [],
            "overview": True,
            "overview_output": "DP-2",
        }
        (msg,) = _run([snap])
        assert msg["state"] is True
        assert msg["output"] == "DP-2"

    def test_a_snapshot_with_the_same_overview_says_nothing(self):
        state = _state()
        snap = {
            "event": "snapshot",
            "windows": [],
            "overview": True,
            "overview_output": "DP-2",
        }
        _run([snap], state)
        assert _run([snap], state) == []

    def test_a_snapshot_that_moves_the_overview_reports_it(self):
        state = _state()
        base = {"event": "snapshot", "windows": [], "overview": True}
        _run([{**base, "overview_output": "DP-1"}], state)
        (msg,) = _run([{**base, "overview_output": "DP-2"}], state)
        assert msg["output"] == "DP-2"

    def test_a_snapshot_that_closes_it_reports_the_old_monitor(self):
        state = _state()
        base = {"event": "snapshot", "windows": []}
        _run([{**base, "overview": True, "overview_output": "DP-1"}], state)
        (msg,) = _run([{**base, "overview": False, "overview_output": None}], state)
        assert msg["state"] is False
        assert msg["output"] == "DP-1"

    def test_an_old_jff_without_the_field_gives_no_monitor(self):
        (msg,) = _run([{"event": "overview", "open": True}])
        assert msg["output"] is None
