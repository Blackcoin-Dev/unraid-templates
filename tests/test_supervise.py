"""Focused process-group shutdown regressions; no GUI or containers needed."""
import importlib.util
from pathlib import Path
import signal
import subprocess
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "gui_supervise", Path(__file__).resolve().parents[1] / "runtime" / "supervise.py")
supervise = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervise)


class ShutdownTests(unittest.TestCase):
    def process(self):
        return mock.Mock(pid=12345)

    def test_disappearing_group_is_harmless_and_leader_is_reaped(self):
        process = self.process()
        with mock.patch.object(supervise.os, "killpg", side_effect=ProcessLookupError, create=True) as kill:
            self.assertTrue(supervise.stop_process(process, 5))
        self.assertEqual(kill.call_args_list, [mock.call(process.pid, signal.SIGTERM),
                                              mock.call(process.pid, signal.SIGKILL)])
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=5), mock.call(timeout=10)])

    def test_exited_leader_does_not_hide_surviving_descendants(self):
        process = self.process()
        process.returncode = 0
        process.poll.return_value = 0
        with mock.patch.object(supervise.os, "killpg", create=True) as kill:
            self.assertFalse(supervise.stop_process(process, 5))
        self.assertEqual(kill.call_args_list, [mock.call(process.pid, signal.SIGTERM),
                                              mock.call(process.pid, signal.SIGKILL)])
        process.poll.assert_not_called()

    def test_graceful_group_exit_is_clean(self):
        process = self.process()
        with mock.patch.object(supervise.os, "killpg", side_effect=[None, ProcessLookupError()], create=True):
            self.assertTrue(supervise.stop_process(process, 100))

    def test_timeout_is_failure_even_when_group_disappears_before_kill(self):
        process = self.process()
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        with mock.patch.object(supervise.os, "killpg", side_effect=[None, ProcessLookupError()], create=True):
            self.assertFalse(supervise.stop_process(process, 5))
        self.assertEqual(process.wait.call_args_list, [mock.call(timeout=5), mock.call(timeout=10)])

    def test_cleanup_error_does_not_skip_other_groups_and_qt_goes_first(self):
        display, vnc, qt = self.process(), self.process(), self.process()
        with mock.patch.object(supervise, "stop_process", side_effect=[OSError("fixture race"), True, True]) as stop, \
                mock.patch.object(supervise.sys, "stderr"):
            self.assertFalse(supervise.stop_children([display, vnc, qt], qt))
        self.assertEqual(stop.call_args_list, [mock.call(qt, 100), mock.call(vnc, 5), mock.call(display, 5)])

    def test_partial_startup_cleanup_needs_no_qt(self):
        display, vnc = self.process(), self.process()
        with mock.patch.object(supervise, "stop_process", side_effect=[False, True]) as stop:
            self.assertFalse(supervise.stop_children([display, vnc], None))
        self.assertEqual(stop.call_args_list, [mock.call(vnc, 5), mock.call(display, 5)])
        self.assertTrue(supervise.stop_children([], None))


if __name__ == "__main__":
    unittest.main()
