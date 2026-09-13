#!/usr/bin/env python3
"""Verify and promote one tested native image using existing Docker authentication."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

import release

REPOSITORY = "qqblackcoin/blackcoin-v4-gui"
DIGEST = r"sha256:[0-9a-f]{64}"
SHA = r"[0-9a-f]{40}"
LEGACY_DIGEST = "sha256:647bf0310aeac2b8acbf572174914b3120b15211e53287496a28fa235f0928b8"
LABELS = {"version": "org.opencontainers.image.version",
          "wrapper": "org.opencontainers.image.revision",
          "source": "org.blackcoin.core.source",
          "archive": "org.blackcoin.core.archive.sha256"}
CHECKS = {"native-amd64", "immutable-image-identity", "clean-volume", "core-version",
          "unprivileged-runtime", "gui-processes", "novnc-http", "regtest-rpc", "no-wallet",
          "regtest-blocks", "graceful-restart", "persistence", "cleanup"}


class WrapperMismatch(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(lock, wrapper):
    release.validate_lock(lock)
    require(isinstance(wrapper, str) and re.fullmatch(SHA, wrapper), "wrapper must be a full commit SHA")
    return {"version": lock["release"]["tag"], "wrapper": wrapper,
            "source": lock["sourceH"], "archive": lock["asset"]["sha256"]}


def tags(lock, wrapper):
    identity(lock, wrapper)
    return (f"{REPOSITORY}:{lock['version_tag']}-{lock['sourceH'][:12]}-{wrapper[:12]}",
            f"{REPOSITORY}:{lock['version_tag']}")


def validate_image(info, expected, require_wrapper=True):
    require(isinstance(info, dict) and info.get("Os") == "linux" and info.get("Architecture") == "amd64",
            "image must be native linux/amd64")
    require(isinstance(info.get("Id"), str) and re.fullmatch(DIGEST, info["Id"]), "invalid image ID")
    labels = info.get("Config", {}).get("Labels") or {}
    for key, label in LABELS.items():
        if key != "wrapper" or require_wrapper:
            if key == "wrapper" and labels.get(label) != expected[key]:
                raise WrapperMismatch("image wrapper identity mismatch")
            require(labels.get(label) == expected[key], f"image {key} identity mismatch")
    require(isinstance(labels.get(LABELS["wrapper"]), str) and
            re.fullmatch(SHA, labels[LABELS["wrapper"]]), "image wrapper provenance is missing")
    return info["Id"]


def validate_version(output, expected):
    require(isinstance(output, str) and
            re.findall(r"^Blackcoin version ([^\s]+)[ \t]*$", output, re.MULTILINE) == [expected["version"]],
            "actual Qt binary version mismatch")
    require(re.search(r"(?<![0-9a-f])" + expected["source"] + r"(?![0-9a-f])", output),
            "actual Qt binary source identity mismatch")
    require(not re.search(r"dirty|\brc[0-9]+\b", output, re.IGNORECASE), "binary is not a clean final release")


def validate_receipt(receipt, expected, image_id):
    require(isinstance(receipt, dict) and type(receipt.get("schema_version")) is int and
            receipt["schema_version"] == 1 and receipt.get("passed") is True,
            "successful GUI test receipt required")
    for key, value in {"image_id": image_id, "core_version": expected["version"],
                       "core_source": expected["source"], "wrapper_revision": expected["wrapper"]}.items():
        require(receipt.get(key) == value, f"test receipt {key} mismatch")
    checks = receipt.get("checks")
    require(isinstance(checks, list) and all(isinstance(x, str) for x in checks)
            and set(checks) == CHECKS and len(set(checks)) == len(checks),
            "test receipt checks missing, unrecognized, or duplicated")


def numeric_version(version):
    require(isinstance(version, str) and re.fullmatch("v" + release.VERSION, version),
            "current or prior version is not a stable numeric release")
    parts = tuple(int(x) for x in version[1:].split("."))
    return parts + (0,) * (4 - len(parts))


def guard_latest(docker, latest, expected, previous=None, allow_legacy=False):
    target = numeric_version(expected["version"])
    if previous is not None:
        require(isinstance(previous, dict) and previous.get("schema_version") == 1 and
                previous.get("published") is True, "invalid durable publication state")
        require(target >= numeric_version(previous.get("core_version")),
                "selected release is older than durable publication state")
    if latest is None:
        return
    if latest["digest"] == LEGACY_DIGEST:
        require(allow_legacy and previous is None, "explicit first-publication legacy bootstrap is required")
        return
    current = docker.remote_identity(latest)
    require(target >= numeric_version(current["version"]), "selected release is older than verified latest")


class Docker:
    def __init__(self):
        require(self.command("info", "--format", "{{.Architecture}} {{.OSType}}").strip() in
                ("x86_64 linux", "amd64 linux"), "Docker engine must be native linux/amd64")
        self.buildx = subprocess.run(["docker", "buildx", "version"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=30, check=False).returncode == 0
        self.versions = {}

    @staticmethod
    def command(*args, missing_ok=False, timeout=180):
        result = subprocess.run(["docker", *args], capture_output=True, encoding="utf-8",
                                timeout=timeout, check=False)
        if result.returncode:
            if missing_ok and re.search(r"manifest unknown|no such manifest|: not found(?:\s|$)", result.stderr, re.I):
                return None
            raise ValueError("Docker operation failed: " + " ".join(args[:3]))
        return result.stdout

    def registry(self, ref):
        if self.buildx:
            output = self.command("buildx", "imagetools", "inspect", "--format", "{{json .Manifest}}", ref,
                                  missing_ok=True)
            if output is None:
                return None
            descriptor = release.decode_json(output)
        else:
            output = self.command("manifest", "inspect", "--verbose", ref, missing_ok=True)
            if output is None:
                return None
            verbose = release.decode_json(output)
            require(isinstance(verbose, dict), "registry reference is not a single native manifest")
            descriptor = verbose.get("Descriptor", {})
        digest = descriptor.get("digest")
        require(isinstance(digest, str) and re.fullmatch(DIGEST, digest), "invalid registry manifest digest")
        immutable = f"{REPOSITORY}@{digest}"
        raw = (self.command("buildx", "imagetools", "inspect", "--raw", immutable) if self.buildx else
               self.command("manifest", "inspect", immutable))
        manifest = release.decode_json(raw)
        require(manifest.get("schemaVersion") == 2 and "manifests" not in manifest,
                "registry reference must contain one native image, not a platform index")
        config = manifest.get("config", {}).get("digest")
        require(isinstance(config, str) and re.fullmatch(DIGEST, config), "invalid registry image config digest")
        return {"digest": digest, "image": immutable, "image_id": config}

    def verify(self, image, expected, require_wrapper=True):
        require(isinstance(image, str) and re.fullmatch(r"[a-z0-9][a-z0-9._:/@-]+", image), "invalid image reference")
        info = release.decode_json(self.command("image", "inspect", image))
        require(isinstance(info, list) and len(info) == 1, "image inspection was ambiguous")
        image_id = validate_image(info[0], expected, require_wrapper)
        if image_id not in self.versions:
            self.versions[image_id] = self.command("run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m", "-e", "HOME=/tmp",
                "-e", "QT_QPA_PLATFORM=offscreen", "--entrypoint", "blackcoin-qt", image, "-version", timeout=60)
        validate_version(self.versions[image_id], expected)
        return image_id

    def remote_identity(self, record):
        self.command("pull", "--platform", "linux/amd64", record["image"], timeout=600)
        infos = release.decode_json(self.command("image", "inspect", record["image"]))
        require(isinstance(infos, list) and len(infos) == 1, "current image inspection was ambiguous")
        labels = infos[0].get("Config", {}).get("Labels") or {}
        expected = {key: labels.get(label) for key, label in LABELS.items()}
        numeric_version(expected["version"])
        require(isinstance(expected["source"], str) and re.fullmatch(SHA, expected["source"]) and
                isinstance(expected["archive"], str) and re.fullmatch(r"[0-9a-f]{64}", expected["archive"]),
                "current latest has no verifiable Core provenance")
        require(self.verify(record["image"], expected) == record["image_id"], "current latest config digest mismatch")
        return expected

    def verify_remote(self, record, expected, require_wrapper=True):
        self.command("pull", "--platform", "linux/amd64", record["image"], timeout=600)
        require(self.verify(record["image"], expected, require_wrapper) == record["image_id"],
                "pulled image ID differs from registry manifest config digest")
        return record


def inspect(docker, lock, wrapper, previous=None, allow_legacy=False):
    expected = identity(lock, wrapper)
    candidate_tag, version_tag = tags(lock, wrapper)
    candidate, version = docker.registry(candidate_tag), docker.registry(version_tag)
    if candidate:
        docker.verify_remote(candidate, expected)
    if version:
        docker.verify_remote(version, expected, require_wrapper=False)
        if not candidate:
            try:
                docker.verify(version["image"], expected)
                candidate = version
            except WrapperMismatch:
                pass  # A valid older-wrapper version tag is preserved, not overwritten.
    latest = docker.registry(REPOSITORY + ":latest")
    guard_latest(docker, latest, expected, previous, allow_legacy)
    current = candidate and version and latest and candidate["digest"] == latest["digest"]
    return {"schema_version": 1, "action": "current" if current else "reuse" if candidate else "build",
            "image": candidate["image"] if candidate else None, "candidate_tag": candidate_tag,
            "version_tag": version_tag, "latest_digest": latest["digest"] if latest else None}


def publish(docker, lock, wrapper, image, receipt, recheck, previous=None, allow_legacy=False):
    expected = identity(lock, wrapper)
    image_id = docker.verify(image, expected)
    validate_receipt(receipt, expected, image_id)
    candidate_tag, version_tag = tags(lock, wrapper)
    latest_before = docker.registry(REPOSITORY + ":latest")
    guard_latest(docker, latest_before, expected, previous, allow_legacy)
    version = docker.registry(version_tag)
    if version:
        docker.verify_remote(version, expected, require_wrapper=False)
    candidate = docker.registry(candidate_tag)
    if candidate:
        docker.verify_remote(candidate, expected)
        require(candidate["image_id"] == image_id, "immutable candidate tag already names different bytes")
    else:
        docker.command("tag", image, candidate_tag)
        docker.command("push", candidate_tag, timeout=900)
        candidate = docker.registry(candidate_tag)
        require(candidate is not None, "candidate tag missing after push")
        docker.verify_remote(candidate, expected)
        require(candidate["image_id"] == image_id, "pushed candidate bytes changed")
    if not version:
        docker.command("tag", candidate["image"], version_tag)
        docker.command("push", version_tag, timeout=900)
        version = docker.registry(version_tag)
        require(version and version["digest"] == candidate["digest"], "version tag digest differs from candidate")
    recheck()
    latest_now = docker.registry(REPOSITORY + ":latest")
    require(latest_now == latest_before, "latest changed concurrently; refusing to overwrite it")
    if not latest_now or latest_now["digest"] != candidate["digest"]:
        docker.command("tag", candidate["image"], REPOSITORY + ":latest")
        docker.command("push", REPOSITORY + ":latest", timeout=900)
    final = docker.registry(REPOSITORY + ":latest")
    require(final and final["digest"] == candidate["digest"], "latest digest verification failed")
    return {"schema_version": 1, "published": True, "image": candidate["image"],
            "image_id": image_id, "core_version": expected["version"], "core_source": expected["source"],
            "wrapper_revision": wrapper, "candidate_tag": candidate_tag, "version_tag": version_tag,
            "latest_digest": final["digest"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--lock", required=True, type=Path)
        command.add_argument("--wrapper", required=True)
        command.add_argument("--output", type=Path)
        command.add_argument("--previous-state", type=Path)
        command.add_argument("--allow-legacy-bootstrap", action="store_true")
        if name == "publish":
            command.add_argument("--image", required=True)
            command.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    lock = release.decode_json(args.lock.read_bytes())
    identity(lock, args.wrapper)
    previous = release.decode_json(args.previous_state.read_bytes()) if args.previous_state else None
    docker = Docker()
    if args.command == "inspect":
        result = inspect(docker, lock, args.wrapper, previous, args.allow_legacy_bootstrap)
    else:
        def recheck():
            subprocess.run([sys.executable, str(Path(__file__).with_name("release.py")), "recheck",
                            "--lock", str(args.lock)], check=True, timeout=180)
        result = publish(docker, lock, args.wrapper, args.image, release.decode_json(args.receipt.read_bytes()), recheck,
                         previous, args.allow_legacy_bootstrap)
    encoded = json.dumps(result, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
