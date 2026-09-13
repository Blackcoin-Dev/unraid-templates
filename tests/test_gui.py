#!/usr/bin/env python3
"""Offline version and combined-readiness regressions; never starts Docker."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("gui_qualification", Path(__file__).parents[1] / "tools/test_gui.py")
gui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gui)
VERSION = "v30.1.5.1"
SOURCE = "bd5dad9aef27aff1f1bff37d6e5464b8305cd3ca"


def reported(version=VERSION, source=SOURCE):
    return "Blackcoin version " + version + "\nSource commit: " + source + "\n"


class GuiQualificationTests(unittest.TestCase):
    def test_exact_stable_version_and_full_source(self):
        gui.validate_version(reported(), VERSION, SOURCE)
        gui.validate_version(reported("v30.1.5"), "v30.1.5", SOURCE)
        for source in (SOURCE[:12], "a" * 40, SOURCE + "a", "a" + SOURCE, SOURCE + " (dirty)"):
            with self.subTest(source=source), self.assertRaises(RuntimeError):
                gui.validate_version(reported(source=source), VERSION, SOURCE)

    def test_prerelease_and_other_suffixes_never_match_stable_version(self):
        for suffix in ("-alpha", "-alpha1", "alpha1", "-beta1", "beta1", ".beta1", "-pre", "pre1",
                       "-preview1", "-rc1", "rc1", ".rc1", "-RC1", "-dirty", "+build1", "-gabcdef",
                       ".0", "1", "_release", "/release"):
            with self.subTest(suffix=suffix), self.assertRaises(RuntimeError):
                gui.validate_version(reported(VERSION + suffix), VERSION, SOURCE)

    def test_only_one_exact_reported_version_line_is_accepted(self):
        for output in ("example " + reported(), reported("v30.1.5.2") + "Expected " + VERSION + "\n",
                       reported() + reported(VERSION + "-beta1"), reported() + reported(),
                       reported().replace(VERSION, VERSION + " beta1")):
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                gui.validate_version(output, VERSION, SOURCE)

    def test_ready_waits_for_gui_and_rpc_in_one_bounded_loop(self):
        fixture = gui.Fixture("sha256:" + "a" * 64)
        state = {"State": {"Running": True, "OOMKilled": False}}
        with mock.patch.object(gui, "inspect", return_value=state), \
                mock.patch.object(fixture, "rpc", return_value={"chain": "regtest"}) as rpc, \
                mock.patch.object(gui, "gui_health", side_effect=[RuntimeError("noVNC starting"), None]) as health, \
                mock.patch.object(gui.time, "monotonic", side_effect=[0, 0, 1]), \
                mock.patch.object(gui.time, "sleep"):
            self.assertEqual(fixture.wait_ready(), {"chain": "regtest"})
            self.assertEqual(rpc.call_count, 2)
            self.assertEqual(health.call_count, 2)

    def test_combined_readiness_cannot_wait_indefinitely(self):
        fixture = gui.Fixture("sha256:" + "a" * 64)
        state = {"State": {"Running": True, "OOMKilled": False}}
        with mock.patch.object(gui, "inspect", return_value=state), \
                mock.patch.object(fixture, "rpc", return_value={"chain": "regtest"}), \
                mock.patch.object(gui, "gui_health", side_effect=RuntimeError("noVNC unavailable")), \
                mock.patch.object(gui.time, "monotonic", side_effect=[0, 0, 121]), \
                mock.patch.object(gui.time, "sleep"), self.assertRaisesRegex(RuntimeError, "120 seconds"):
            fixture.wait_ready()


if __name__ == "__main__":
    unittest.main()
