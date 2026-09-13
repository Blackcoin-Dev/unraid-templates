#!/usr/bin/env python3
"""Print the real Qt version using its shipped xcb plugin and an isolated display."""
import os
import signal
import subprocess
import sys
import time


def probe():
    environment = dict(os.environ, DISPLAY=":99", QT_QPA_PLATFORM="xcb")
    display = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x16", "-nolisten", "tcp"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if display.poll() is not None:
                raise RuntimeError("isolated version display exited before readiness")
            ready = subprocess.run(["xdpyinfo", "-display", ":99"], env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            if ready.returncode == 0 and display.poll() is None:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("isolated version display did not become ready")
        # No datadir, wallet, RPC, or server options: this only prints version identity.
        return subprocess.run(["blackcoin-qt", "-version"], env=environment, timeout=30).returncode
    finally:
        if display.poll() is None:
            display.terminate()
        try:
            display.wait(timeout=5)
        except subprocess.TimeoutExpired:
            display.kill()
            display.wait(timeout=5)


def interrupted(_signal, _frame):
    raise RuntimeError("version probe interrupted")


def main():
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        return probe()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("Qt version probe failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
