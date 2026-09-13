#!/usr/bin/env python3
"""Reconcile one fixed public wrapper with the newest verified stable Core release."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import promote
import release

SOURCE = Path(__file__).resolve().parent.parent


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def prepare_context(client, lock, build):
    release.validate_lock(lock)
    build = release.safe_directory(build)
    binaries = build / "bin"
    checksums = build / "binary-checksums.json"
    release.require(not binaries.is_symlink() and not checksums.is_symlink()
                    and not checksums.with_suffix(".tmp").is_symlink(), "generated cache contains a symlink")
    if binaries.exists():
        release.require(binaries.is_dir() and all(p.name in release.BINARIES and p.is_file()
                        and not p.is_symlink() for p in binaries.iterdir()),
                        "refusing to discard unrecognized cache contents")

    def hashes():
        result = {}
        for path in binaries.iterdir():
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path.name] = digest.hexdigest()
        return result

    cached = None
    if checksums.exists():
        release.require(checksums.is_file() and checksums.stat().st_size <= 16384, "invalid cache metadata")
        try:
            cached = release.decode_json(checksums.read_bytes())
        except (ValueError, UnicodeError):
            pass  # An interrupted metadata write is a retryable staging failure.
    reusable = (binaries.exists() and isinstance(cached, dict)
                and set(cached) == {"schema_version", "lock", "binaries"}
                and type(cached["schema_version"]) is int and cached["schema_version"] == 1
                and cached["lock"] == lock and isinstance(cached["binaries"], dict)
                and set(cached["binaries"]) == release.BINARIES and cached["binaries"] == hashes())
    if not reusable:
        # Only these known generated paths are replaced; keep source and other stages.
        if binaries.exists():
            shutil.rmtree(binaries)
        checksums.unlink(missing_ok=True)
        release.fetch(client, lock, build)
        write_json(checksums, {"schema_version": 1, "lock": lock, "binaries": hashes()})
    for name in ("Dockerfile", ".dockerignore", "LICENSE", "COPYING.core"):
        shutil.copy2(SOURCE / name, build / name)
    shutil.copytree(SOURCE / "runtime", build / "runtime", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def reconcile(args):
    state = release.safe_directory(args.state)
    with (state / "reconcile.lock").open("a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = release.GitHub()
        lock = release.resolve(client)
        expected = promote.identity(lock, args.wrapper)
        docker = promote.Docker()
        previous_path = state / "last-publication.json"
        previous = release.decode_json(previous_path.read_bytes()) if previous_path.exists() else None
        action = promote.inspect(docker, lock, args.wrapper, previous, args.allow_legacy_bootstrap)
        write_json(state / "last-resolved.json", lock)
        if action["action"] == "current":
            # Keep the numeric floor when first observing an already-current registry.
            if not args.validate_only and (previous is None or previous.get("latest_digest") != action["latest_digest"]):
                write_json(previous_path, {"schema_version": 1, "published": True, "observed_current": True,
                    "core_version": expected["version"], "core_source": expected["source"],
                    "wrapper_revision": args.wrapper, "image": action["image"],
                    "image_id": docker.verify(action["image"], expected), "latest_digest": action["latest_digest"]})
            print(json.dumps({"status": "current", "version": lock["version_tag"],
                              "digest": action["latest_digest"]}), flush=True)
            return
        key = lock["version_tag"] + "-" + args.wrapper
        run = state / "builds" / key
        run.mkdir(parents=True, exist_ok=True)
        write_json(run / "release-lock.json", lock)
        image = action["image"] or action["candidate_tag"]
        if action["action"] == "build":
            local = subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
            if local.returncode == 0:
                docker.verify(image, expected)
                print("Reusing the already-built exact candidate for targeted qualification.", flush=True)
            else:
                prepare_context(client, lock, run / "context")
                command = ["docker", "build", "--platform", "linux/amd64", "--provenance=false",
                           "--tag", image]
                for name, value in {"CORE_VERSION": expected["version"], "CORE_SOURCE": expected["source"],
                                    "CORE_ARCHIVE_SHA256": expected["archive"],
                                    "WRAPPER_REVISION": expected["wrapper"]}.items():
                    command += ["--build-arg", name + "=" + value]
                subprocess.run(command + [str(run / "context")], check=True, timeout=1800)
        image_id = docker.verify(image, expected)
        receipt = run / "gui-receipt.json"
        if receipt.exists():
            promote.validate_receipt(release.decode_json(receipt.read_bytes()), expected, image_id)
            print("Reusing successful qualification of the exact immutable image ID.", flush=True)
        else:
            subprocess.run([sys.executable, str(SOURCE / "tools/test_gui.py"), "--image", image_id,
                            "--receipt", str(receipt)], check=True, timeout=600)
        if args.validate_only:
            print(json.dumps({"status": "validated", "image_id": image_id}), flush=True)
            return
        result = promote.publish(docker, lock, args.wrapper, image_id,
                                 release.decode_json(receipt.read_bytes()), lambda: release.recheck(client, lock),
                                 previous, args.allow_legacy_bootstrap)
        write_json(run / "publication.json", result)
        write_json(state / "last-publication.json", result)
        print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wrapper", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--allow-legacy-bootstrap", action="store_true")
    try:
        reconcile(parser.parse_args())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit("Reconciliation failed: " + str(error))
