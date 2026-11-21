import unittest

from src.plugins.experimental.global_menu._menu_model import (
    MODES,
    normalize_mode,
    use_compact,
)


class NormalizeModeTests(unittest.TestCase):
    def test_known_modes_are_kept(self):
        for mode in MODES:
            self.assertEqual(normalize_mode(mode), mode)

    def test_case_and_spaces_are_ignored(self):
        self.assertEqual(normalize_mode("  Compact "), "compact")

    def test_missing_or_unknown_falls_back_to_auto(self):
        for value in (None, "", "tiny", 3, True):
            self.assertEqual(normalize_mode(value), "auto")


class UseCompactTests(unittest.TestCase):
    def test_compact_mode_is_always_compact(self):
        self.assertTrue(use_compact("compact", 5000, 100))

    def test_full_mode_is_never_compact(self):
        self.assertFalse(use_compact("full", 10, 900))

    def test_auto_stays_full_when_the_row_fits(self):
        self.assertFalse(use_compact("auto", 900, 900))
        self.assertFalse(use_compact("auto", 1200, 900))

    def test_auto_collapses_when_the_row_does_not_fit(self):
        self.assertTrue(use_compact("auto", 899, 900))

    def test_auto_with_an_empty_row_is_not_compact(self):
        self.assertFalse(use_compact("auto", 0, 0))
