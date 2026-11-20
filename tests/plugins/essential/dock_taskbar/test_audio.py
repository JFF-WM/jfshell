"""Tests: the mute helpers of the dock taskbar."""

import importlib.util
import os
import types
import unittest
from pathlib import Path

_PATH = (
    Path(__file__).resolve().parents[4]
    / "src/plugins/essential/dock_taskbar/_audio.py"
)
_spec = importlib.util.spec_from_file_location("dock_taskbar_audio", _PATH)
assert _spec is not None and _spec.loader is not None
audio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio)


class StateTests(unittest.TestCase):
    STREAMS = [(10, True), (10, False), (20, True), (30, False)]

    def test_mixed_streams_report_both(self):
        self.assertEqual(audio.audio_state(self.STREAMS, {10}), (True, True))

    def test_all_muted(self):
        self.assertEqual(audio.audio_state(self.STREAMS, {20}), (True, False))

    def test_none_muted(self):
        self.assertEqual(audio.audio_state(self.STREAMS, {30}), (False, True))

    def test_app_without_streams(self):
        self.assertEqual(audio.audio_state(self.STREAMS, {99}), (False, False))
        self.assertEqual(audio.audio_state([], {10}), (False, False))


class FamilyTests(unittest.TestCase):
    def test_keeps_ids_and_skips_junk(self):
        family = audio.process_family([os.getpid(), None, "x", "12"])
        self.assertIn(os.getpid(), family)
        self.assertIn(12, family)
        self.assertNotIn(None, family)

    def test_empty(self):
        self.assertEqual(audio.process_family([None, ""]), set())


class NoPulseTests(unittest.TestCase):
    class Broken(audio.DockTaskbarAudio):
        def _client(self):
            raise RuntimeError("no pulse")

    def test_state_without_pulse(self):
        self.assertEqual(self.Broken().state([os.getpid()]), (False, False))

    def test_set_mute_without_pulse_does_not_raise(self):
        self.Broken().set_mute([os.getpid()], True)

    def test_no_pids_never_touches_pulse(self):
        self.assertEqual(self.Broken().state([None]), (False, False))
        self.Broken().set_mute([None], True)


class OwningPidTests(unittest.TestCase):
    def test_own_pid_and_ancestors(self):
        parents = {102: 101, 101: 100}
        self.assertEqual(audio.owning_pid(100, {100}, parents), 100)
        self.assertEqual(audio.owning_pid(102, {100}, parents), 100)

    def test_nearest_window_ancestor_wins(self):
        parents = {102: 101, 101: 100}
        self.assertEqual(audio.owning_pid(102, {100, 101}, parents), 101)

    def test_no_owner_and_cycles(self):
        self.assertIsNone(audio.owning_pid(300, {100}, {300: 1}))
        self.assertIsNone(audio.owning_pid(5, {100}, {5: 6, 6: 5}))

    def test_process_parents_knows_this_process(self):
        parents = audio.process_parents()
        if parents:  # empty without psutil
            self.assertIn(os.getpid(), parents)


class StreamFlagsTests(unittest.TestCase):
    OWNERS = {100: "browser", 200: "player"}
    PARENTS = {101: 100, 102: 101, 201: 200, 300: 1}

    def flags(self, streams):
        return audio.stream_flags(streams, self.OWNERS, self.PARENTS)

    def test_stream_of_the_window_process(self):
        self.assertEqual(self.flags([(200, False, False)]), {"player": (True, False)})

    def test_stream_of_a_descendant_process(self):
        self.assertEqual(self.flags([(102, False, False)]), {"browser": (True, False)})

    def test_running_muted_stream(self):
        self.assertEqual(self.flags([(100, True, False)]), {"browser": (False, True)})

    def test_audible_stream_wins_over_muted_one(self):
        streams = [(100, True, False), (101, False, False)]
        self.assertEqual(self.flags(streams), {"browser": (True, False)})

    def test_paused_streams_are_ignored(self):
        self.assertEqual(self.flags([(100, False, True)]), {})
        streams = [(100, False, True), (101, True, False)]
        self.assertEqual(self.flags(streams), {"browser": (False, True)})

    def test_unrelated_streams_are_ignored(self):
        self.assertEqual(self.flags([(300, False, False), (999, False, False)]), {})

    def test_two_apps(self):
        streams = [(100, False, False), (201, True, False)]
        self.assertEqual(
            self.flags(streams),
            {"browser": (True, False), "player": (False, True)},
        )


class FlagsTests(unittest.TestCase):
    class Playing(audio.DockTaskbarAudio):
        def _streams(self):
            stream = types.SimpleNamespace(mute=False, corked=False)
            return [(os.getpid(), stream)]

    class Broken(audio.DockTaskbarAudio):
        def _client(self):
            raise RuntimeError("no pulse")

    def test_flags_by_key(self):
        result = self.Playing().flags({"me": [os.getpid()], "other": [None, "x"]})
        self.assertEqual(result, {"me": (True, False)})

    def test_no_window_pids(self):
        self.assertEqual(self.Playing().flags({"a": [None]}), {})
        self.assertEqual(self.Playing().flags({}), {})

    def test_without_pulse(self):
        self.assertEqual(self.Broken().flags({"me": [os.getpid()]}), {})

    def test_stop_watch_without_start(self):
        audio.DockTaskbarAudio().stop_watch()


if __name__ == "__main__":
    unittest.main()
