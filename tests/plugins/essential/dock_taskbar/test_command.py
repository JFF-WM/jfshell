"""Tests: command_from_cmdline, the command of an app that has no desktop entry."""

import importlib.util
import sys
import unittest
from pathlib import Path

_MODEL = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "plugins"
    / "essential"
    / "dock_taskbar"
    / "_model.py"
)
_NAME = "dock_taskbar_model_command_under_test"
_spec = importlib.util.spec_from_file_location(_NAME, _MODEL)
model = importlib.util.module_from_spec(_spec)
sys.modules[_NAME] = model  # dataclasses look their module up here
_spec.loader.exec_module(model)


class CommandFromCmdlineTest(unittest.TestCase):
    def test_program_without_arguments(self):
        self.assertEqual(model.command_from_cmdline(b"flux\0"), "flux")

    def test_arguments_are_dropped(self):
        raw = b"/usr/bin/flux\0--some-flag\0/home/me/file.txt\0"
        self.assertEqual(model.command_from_cmdline(raw), "/usr/bin/flux")

    def test_text_is_accepted_too(self):
        self.assertEqual(model.command_from_cmdline("flux\0x\0"), "flux")

    def test_interpreters_give_nothing(self):
        for program in ("python3", "/usr/bin/python3.12", "bash", "node", "java"):
            raw = f"{program}\0script\0".encode()
            self.assertEqual(model.command_from_cmdline(raw), "", program)

    def test_relative_path_with_a_directory_gives_nothing(self):
        self.assertEqual(model.command_from_cmdline(b"./target/debug/flux\0"), "")

    def test_empty_gives_nothing(self):
        self.assertEqual(model.command_from_cmdline(b""), "")
        self.assertEqual(model.command_from_cmdline(b"\0\0"), "")

    def test_path_with_spaces_is_quoted(self):
        self.assertEqual(
            model.command_from_cmdline(b"/opt/my app/run\0"), "'/opt/my app/run'"
        )


if __name__ == "__main__":
    unittest.main()
