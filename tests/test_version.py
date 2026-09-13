"""Offline regressions for the isolated xcb version probe."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("qt_version_probe", Path(__file__).parents[1] / "runtime/version.py")
version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version)


class VersionProbeTests(unittest.TestCase):
    def display(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        return process

    def test_xcb_probe_preserves_home_only_invokes_qt_version_and_cleans_display(self):
        display = self.display()
        with mock.patch.object(version.subprocess, "Popen", return_value=display) as spawn, \
                mock.patch.object(version.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                mock.patch.dict(version.os.environ, {"HOME": "/home/blackcoin", "QT_QPA_PLATFORM": "offscreen"}):
            self.assertEqual(version.probe(), 0)
        self.assertEqual(spawn.call_args.args[0], ["Xvfb", ":99", "-screen", "0", "1280x800x16", "-nolisten", "tcp"])
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [["xdpyinfo", "-display", ":99"], ["blackcoin-qt", "-version"]])
        environment = run.call_args.kwargs["env"]
        self.assertEqual((environment["HOME"], environment["DISPLAY"], environment["QT_QPA_PLATFORM"]),
                         ("/home/blackcoin", ":99", "xcb"))
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        display.terminate.assert_called_once()
        display.wait.assert_called_once_with(timeout=5)

    def test_readiness_is_bounded_and_failure_does_not_launch_qt(self):
        display = self.display()
        with mock.patch.object(version.subprocess, "Popen", return_value=display), \
                mock.patch.object(version.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, \
                mock.patch.object(version.time, "monotonic", side_effect=[0, 0, 11]), \
                mock.patch.object(version.time, "sleep"), self.assertRaisesRegex(RuntimeError, "did not become ready"):
            version.probe()
        self.assertEqual(run.call_count, 1)
        display.terminate.assert_called_once()

    def test_qt_exit_status_is_not_hidden(self):
        display = self.display()
        with mock.patch.object(version.subprocess, "Popen", return_value=display), \
                mock.patch.object(version.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0),
                                                                           subprocess.CompletedProcess([], 7)]):
            self.assertEqual(version.probe(), 7)
        display.terminate.assert_called_once()

    def test_qt_timeout_still_cleans_and_kills_unresponsive_display(self):
        display = self.display()
        display.wait.side_effect = [subprocess.TimeoutExpired("Xvfb", 5), 0]
        with mock.patch.object(version.subprocess, "Popen", return_value=display), \
                mock.patch.object(version.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0),
                                                                           subprocess.TimeoutExpired("blackcoin-qt", 30)]), \
                self.assertRaises(subprocess.TimeoutExpired):
            version.probe()
        display.terminate.assert_called_once()
        display.kill.assert_called_once()
        self.assertEqual(display.wait.call_count, 2)


if __name__ == "__main__":
    unittest.main()
