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
