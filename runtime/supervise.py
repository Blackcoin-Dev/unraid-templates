#!/usr/bin/env python3
"""Unprivileged, fail-closed GUI supervision; never create or unlock a wallet."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

DATA = Path("/home/blackcoin/.blackcoin")
stopping = False


def stop_requested(_signal, _frame):
    global stopping
    stopping = True


def stop_process(process, grace):
    # Each child owns a session. Its descendants may outlive the session leader.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    forced = False
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        forced = True
    # Reap the leader, but do not mistake its exit for the whole group's exit.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    else:
        forced = True
    process.wait(timeout=10)
    return not forced


def stop_children(children, qt):
    clean = True
    ordered = ([qt] if qt is not None else []) + [child for child in reversed(children) if child is not qt]
    for child in ordered:
        try:
            if not stop_process(child, 100 if child is qt else 5):
                clean = False
        except (OSError, subprocess.SubprocessError) as error:
            print("GUI child cleanup failed: " + str(error), file=sys.stderr)
            clean = False
    return clean


def main():
    if len(sys.argv) != 1:
        raise ValueError("launcher arguments are not supported; use the mounted Core configuration")
    if os.getuid() != 1000 or not DATA.is_dir() or not os.access(DATA, os.W_OK | os.X_OK):
        raise ValueError("datadir must exist and be writable by UID/GID 1000; no ownership changes are made")
    network = os.environ.get("BLACKCOIN_NETWORK", "main")
    if network not in ("main", "regtest"):
        raise ValueError("BLACKCOIN_NETWORK must be main or regtest")
    test_rpc = os.environ.get("BLACKCOIN_TEST_RPC", "0")
    if test_rpc not in ("0", "1") or (test_rpc == "1" and network != "regtest"):
        raise ValueError("test RPC is permitted only on isolated regtest")
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    children, qt = [], None

    def start(command):
        process = subprocess.Popen(command, start_new_session=True)
        children.append(process)
        return process

    result = 1
    try:
        xvfb = start(["Xvfb", ":0", "-screen", "0", "1280x800x16", "-nolisten", "tcp"])
        for _ in range(100):
            if stopping or xvfb.poll() is not None:
                raise RuntimeError("display stopped before becoming ready")
            probe = subprocess.run(["xdpyinfo", "-display", ":0"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=2)
            if probe.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("display did not become ready")
        start(["fluxbox"])
        start(["x11vnc", "-display", ":0", "-nopw", "-listen", "localhost", "-rfbport", "5900",
               "-xkb", "-forever", "-shared"])
        start(["websockify", "--web=/usr/share/novnc/", "8080", "localhost:5900"])
        command = ["blackcoin-qt", "-datadir=" + str(DATA), "-chain=" + network,
                   "-staking=0", "-autostartstaking=0", "-powmining=0",
                   "-server=" + test_rpc]
        if network == "regtest":
            command += ["-dnsseed=0", "-fixedseeds=0", "-connect=0", "-listen=0", "-discover=0"]
        if test_rpc == "1":
            command += ["-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=35715"]
        qt = start(command)
        while not stopping:
            if qt.poll() is not None:
                result = 0 if qt.returncode == 0 else 1
                break
            if any(child.poll() is not None for child in children if child is not qt):
                raise RuntimeError("a required GUI service stopped")
            time.sleep(0.2)
        else:
            result = 0
    finally:
        if not stop_children(children, qt):
            result = 1
    return result


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("GUI stopped: " + str(error), file=sys.stderr)
        sys.exit(1)
