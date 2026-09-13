#!/usr/bin/env python3
"""Qualify one native amd64 GUI image using disposable isolated regtest data."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


DATA = "/home/blackcoin/.blackcoin"
REQUIRED_CHECKS = {
    "native-amd64", "immutable-image-identity", "clean-volume", "core-version",
    "unprivileged-runtime", "gui-processes", "novnc-http", "regtest-rpc", "no-wallet",
    "regtest-blocks", "graceful-restart", "persistence", "cleanup",
}
GUI_PROCESSES = {"Xvfb", "fluxbox", "x11vnc", "websockify", "blackcoin-qt"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*args, timeout=30, check=True):
    result = subprocess.run(["docker", *args], text=True, capture_output=True, timeout=timeout)
    require(not check or result.returncode == 0, "Docker operation failed: " + args[0])
    return result


def inspect(kind, name):
    result = json.loads(docker(kind, "inspect", name).stdout)
    require(isinstance(result, list) and len(result) == 1, "ambiguous Docker object identity")
    return result[0]


def validate_version(output, expected_version, expected_source):
    # Qt prints PACKAGE_NAME + " version " + FormatFullVersion() on one line.
    # Compare the whole token: a boundary after vTAG also accepts vTAG-beta1.
    require(isinstance(output, str), "Qt version output is missing")
    reported = re.findall(r"^Blackcoin version ([^\s]+)[ \t]*$", output, re.MULTILINE)
    require(reported == [expected_version], "Qt reported version is not the exact stable release")
    require(re.search(r"(?<![0-9a-f])" + re.escape(expected_source) + r"(?![0-9a-f])", output)
            and not re.search(r"\bdirty\b|[-.]rc[0-9]*\b", output, re.I),
            "Qt source does not match the clean release identity")


def image_identity(image):
    engine = json.loads(docker("info", "--format", "{{json .}}").stdout)
    require(engine.get("OSType") == "linux" and engine.get("Architecture") in ("amd64", "x86_64"),
            "qualification requires a native Linux amd64 engine")
    metadata = inspect("image", image)
    require(metadata.get("Os") == "linux" and metadata.get("Architecture") == "amd64",
            "qualification requires a Linux amd64 image")
    identity = metadata.get("Id", "")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", identity), "image identity is not immutable SHA-256")
    labels = metadata.get("Config", {}).get("Labels") or {}
    version = labels.get("org.opencontainers.image.version", "")
    require(re.fullmatch(r"v(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2,3}", version),
            "image version must be a stable numeric release")
    source = labels.get("org.blackcoin.core.source", "")
    wrapper = labels.get("org.opencontainers.image.revision", "")
    archive = labels.get("org.blackcoin.core.archive.sha256", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source) and re.fullmatch(r"[0-9a-f]{40}", wrapper)
            and re.fullmatch(r"[0-9a-f]{64}", archive), "image provenance labels are incomplete")
    require(metadata["Config"].get("User") in ("1000", "1000:1000"), "image must default to UID 1000")
    return {"schema_version": 1, "image_id": identity, "core_version": version,
            "core_source": source, "wrapper_revision": wrapper}


class Fixture:
    def __init__(self, image_id):
        self.image = image_id
        self.prefix = "blackcoin-gui-test-" + uuid.uuid4().hex
        self.volume, self.network = self.prefix + "-data", self.prefix + "-net"
        self.container = self.prefix + "-gui"
        self.containers, self.created_volume, self.created_network = [], False, False

    def create(self, name, *args):
        # Track names before requests: an interrupted CLI may already have created them.
        self.containers.append(name)
        docker("create", "--name", name, "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
               *args, timeout=45)

    def prepare(self):
        self.created_volume = True
        docker("volume", "create", self.volume)
        self.created_network = True
        docker("network", "create", "--internal", self.network)
        require(inspect("network", self.network).get("Internal") is True, "fixture network is not internal")

    def mounted(self):
        return ("--mount", "type=volume,source=" + self.volume + ",target=" + DATA)

    def one_shot(self, suffix, entrypoint, *args, mounted=False, environment=()):
        name = self.prefix + "-" + suffix
        self.create(name, "--network=none", *(self.mounted() if mounted else ()),
                    *environment, "--entrypoint", entrypoint, self.image, *args)
        result = docker("start", "--attach", name, timeout=60)
        state = inspect("container", name)["State"]
        require(state.get("ExitCode") == 0 and state.get("OOMKilled") is False,
                "one-shot qualification process did not exit cleanly")
        docker("rm", "-v", name)
        self.containers.remove(name)
        return result.stdout

    def start(self):
        self.create(self.container, "--network", self.network, *self.mounted(), "--shm-size=256m",
                    "--env", "BLACKCOIN_NETWORK=regtest", "--env", "BLACKCOIN_TEST_RPC=1", self.image)
        docker("start", self.container)

    def python(self, code):
        return json.loads(docker("exec", self.container, "python3", "-c", code).stdout)

    def rpc(self, command, *args):
        result = docker("exec", self.container, "blackcoin-cli", "-regtest", "-datadir=" + DATA,
                        "-rpcport=35715", "-rpccookiefile=" + DATA + "/regtest/.cookie",
                        command, *args, timeout=20)
        return json.loads(result.stdout)

    def wait_ready(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = inspect("container", self.container)["State"]
            require(state.get("Running") is True and state.get("OOMKilled") is False,
                    "GUI container exited before readiness")
            try:
                info = self.rpc("getblockchaininfo")
                if info.get("chain") == "regtest":
                    gui_health(self)
                    return info
            except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired):
                pass
            time.sleep(1)
        raise RuntimeError("isolated regtest RPC did not become ready within 120 seconds")

    def stop(self):
        docker("stop", "--time", "30", self.container, timeout=45)
        state = inspect("container", self.container)["State"]
        require(state.get("Running") is False and state.get("ExitCode") == 0
                and state.get("OOMKilled") is False, "SIGTERM stop was not clean (exit 0 without OOM required)")

    def cleanup(self):
        errors = []
        operations = [("rm", "-f", "-v", name) for name in reversed(self.containers)]
        if self.created_network:
            operations.append(("network", "rm", self.network))
        if self.created_volume:
            operations.append(("volume", "rm", self.volume))
        for args in operations:
            try:
                if docker(*args, timeout=30, check=False).returncode:
                    errors.append("cleanup failed: " + args[-1])
            except (OSError, subprocess.TimeoutExpired):
                errors.append("cleanup timed out or failed: " + args[-1])
        return errors


def no_wallet(fixture):
    require(fixture.rpc("listwallets") == [], "GUI created or loaded a wallet automatically")
    require(fixture.rpc("listwalletdir") == {"wallets": []}, "wallet directory is not empty")
    result = fixture.python("import os,json; root=" + repr(DATA) + "; "
                            "print(json.dumps([n for p,d,fs in os.walk(root) for n in fs "
                            "if n == 'wallet.dat' or n.endswith(('.sqlite','.sqlite3'))]))")
    require(result == [], "unexpected wallet database exists in fixture data")


def gui_health(fixture):
    metadata = inspect("container", fixture.container)
    host = metadata["HostConfig"]
    require(metadata["Config"].get("User") in ("1000", "1000:1000")
            and host.get("Privileged") is False and "ALL" in host.get("CapDrop", [])
            and not host.get("PortBindings"), "GUI container is not unprivileged and unexposed")
    require(set(metadata["NetworkSettings"]["Networks"]) == {fixture.network}, "unexpected GUI network")
    result = fixture.python("""import glob,json,os
expected = {'Xvfb','fluxbox','x11vnc','websockify','blackcoin-qt'}
found = set()
safe = os.getuid() == 1000
for path in glob.glob('/proc/[0-9]*/cmdline'):
    try:
        args = open(path, 'rb').read(65536).split(b'\\0')
        names = {os.path.basename(a.decode(errors='replace')) for a in args if a}
        matched = expected & names
        if matched:
            status = dict(line.split(':',1) for line in open(path.rsplit('/',1)[0]+'/status') if ':' in line)
            safe = safe and all(int(uid) == 1000 for uid in status['Uid'].split()) and int(status['CapEff'],16) == 0
            found.update(matched)
    except (FileNotFoundError, ProcessLookupError):
        continue
print(json.dumps({'found':sorted(found),'safe':safe}))
""")
    require(result.get("safe") is True and set(result.get("found", [])) == GUI_PROCESSES,
            "all five GUI processes must be live under UID 1000 without capabilities")
    require(fixture.python("import json,urllib.request; "
                           "r=urllib.request.urlopen('http://127.0.0.1:8080/vnc.html',timeout=10); "
                           "print(json.dumps(r.status))") == 200, "noVNC page did not return HTTP 200")


def qualify(image):
    receipt = image_identity(image)
    checks = ["native-amd64", "immutable-image-identity"]
    fixture = Fixture(receipt["image_id"])
    error = None
    try:
        fixture.prepare()
        clean = fixture.one_shot("clean", "python3", "-c", "import os,json; "
                                 "print(json.dumps({'uid':os.getuid(),'files':os.listdir(" + repr(DATA) + ")}))",
                                 mounted=True)
        require(json.loads(clean) == {"uid": 1000, "files": []}, "fresh image volume contains embedded data or secrets")
        checks.append("clean-volume")
        version = fixture.one_shot("version", "blackcoin-qt", "-version",
                                   environment=("--env", "QT_QPA_PLATFORM=offscreen"))
        validate_version(version, receipt["core_version"], receipt["core_source"])
        checks.append("core-version")
        fixture.start()
        info = fixture.wait_ready()
        require(info.get("blocks") == 0 and fixture.rpc("getconnectioncount") == 0,
                "fixture is not a fresh isolated regtest chain")
        checks.append("regtest-rpc")
        checks.extend(("unprivileged-runtime", "gui-processes", "novnc-http"))
        no_wallet(fixture)
        hashes = fixture.rpc("generatetodescriptor", "3", "raw(51)")
        info = fixture.rpc("getblockchaininfo")
        require(isinstance(hashes, list) and len(hashes) == 3
                and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
                and info.get("blocks") == 3 and info.get("bestblockhash") == hashes[-1], "regtest keyless block generation failed")
        checks.append("regtest-blocks")
        marker = fixture.prefix
        fixture.python("import json; p=" + repr(DATA + "/.gui-fixture-marker") + "; "
                       "f=open(p,'x'); f.write(" + repr(marker) + "); f.close(); print(json.dumps(True))")
        fixture.stop()
        docker("start", fixture.container)
        restored = fixture.wait_ready()
        require(restored.get("blocks") == 3 and restored.get("bestblockhash") == hashes[-1], "chain identity did not survive restart")
        require(fixture.python("import json; print(json.dumps(open(" + repr(DATA + "/.gui-fixture-marker")
                               + ").read()))") == marker, "mounted marker did not survive restart")
        require(fixture.rpc("getconnectioncount") == 0, "regtest gained an external peer")
        no_wallet(fixture)
        checks.extend(("no-wallet", "graceful-restart", "persistence"))
        fixture.stop()
        require(inspect("image", image).get("Id") == receipt["image_id"], "input image reference changed during qualification")
    except Exception as caught:
        error = caught
    finally:
        cleanup_errors = fixture.cleanup()
    require(not cleanup_errors, "; ".join(cleanup_errors))
    if error is not None:
        raise error
    checks.append("cleanup")
    require(set(checks) == REQUIRED_CHECKS and len(checks) == len(REQUIRED_CHECKS), "qualification check set is incomplete")
    return {**receipt, "passed": True, "checks": sorted(checks)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(not args.receipt.exists() and not args.receipt.is_symlink(), "receipt already exists; refusing to replace it")
        receipt = qualify(args.image)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.receipt.open("x", encoding="utf-8") as output:
            json.dump(receipt, output, indent=2, sort_keys=True)
            output.write("\n")
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"passed": False, "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
