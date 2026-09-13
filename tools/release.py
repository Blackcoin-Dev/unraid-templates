#!/usr/bin/env python3
"""Resolve and verify official stable Blackcoin Linux x86-64 GUI releases."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tarfile
import tempfile
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = "Blackcoin-Dev/Blackcoin"
API = "https://api.github.com/repos/" + REPOSITORY
SOURCE = "https://github.com/" + REPOSITORY
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?"
SHA256 = r"[0-9a-f]{64}"
SHA1 = r"[0-9a-f]{40}"
BINARIES = frozenset({"blackcoind", "blackcoin-cli", "blackcoin-qt", "blackcoin-tx",
                      "blackcoin-util", "blackcoin-wallet"})
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
DOWNLOAD_HOSTS = frozenset({"api.github.com", "github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com"})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def positive_int(value):
    return type(value) is int and value > 0


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def decode_json(data):
    return json.loads(data, object_pairs_hook=unique_object,
                      parse_constant=lambda _: require(False, "non-finite JSON value"))


def check_url(url):
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in DOWNLOAD_HOSTS
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and not parsed.fragment, "unapproved download URL")


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        check_url(newurl)
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self):
        self.opener = build_opener(SafeRedirect())

    def transfer(self, url, limit, destination, binary=False):
        check_url(url)
        require(url.startswith(API + "/"), "initial request must use the official repository API")
        request = Request(url, headers={"Accept": "application/octet-stream" if binary else
                                       "application/vnd.github+json",
                                       "User-Agent": "blackcoin-unraid-release-verifier",
                                       "X-GitHub-Api-Version": "2022-11-28"})
        if os.environ.get("GH_TOKEN"):
            request.add_unredirected_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
        digest, count = hashlib.sha256(), 0
        try:
            with self.opener.open(request, timeout=30) as response:
                check_url(response.url)
                while True:
                    chunk = response.read(min(1024 * 1024, limit - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    require(count <= limit, "download exceeds permitted size")
                    digest.update(chunk)
                    destination.write(chunk)
        except HTTPError as error:
            raise ValueError("GitHub request failed with HTTP " + str(error.code)) from None
        except (URLError, TimeoutError):
            raise ValueError("GitHub request failed or timed out") from None
        return count, digest.hexdigest()

    def api(self, path):
        output = io.BytesIO()
        self.transfer(API + path, 4 * 1024 * 1024, output)
        value = decode_json(output.getvalue())
        require(isinstance(value, dict), "GitHub API response must be an object")
        return value

    def download(self, asset, destination):
        size, digest = self.transfer(asset["url"], asset["size"], destination, binary=True)
        require(size == asset["size"] and digest == asset["sha256"],
                "asset bytes do not match pinned size and SHA-256")

    def small_asset(self, asset):
        require(asset["size"] <= 1024 * 1024, "metadata asset is too large")
        output = io.BytesIO()
        self.download(asset, output)
        return output.getvalue()


def stable_release(release):
    require(release.get("draft") is False and release.get("prerelease") is False,
            "release must be published stable, not draft or prerelease")
    tag = release.get("tag_name")
    require(matches("v" + VERSION, tag), "release tag must be numeric vN.N.N or vN.N.N.N")
    require(positive_int(release.get("id")), "invalid release ID")
    published = release.get("published_at")
    require(matches(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", published),
            "invalid release publication time")
    datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
    return tag


def verified(value):
    require(isinstance(value, dict) and value.get("verified") is True
            and value.get("reason") == "valid", "GitHub signature verification is not valid")


def source_commit(client, tag):
    reference = client.api("/git/ref/tags/" + tag)
    require(reference.get("ref") == "refs/tags/" + tag, "tag reference mismatch")
    obj = reference.get("object", {})
    require(isinstance(obj, dict) and obj.get("type") == "tag", "release must use a signed annotated tag")
    visited = set()
    while obj.get("type") == "tag":
        sha = obj.get("sha")
        require(matches(SHA1, sha) and sha not in visited and len(visited) < 8,
                "invalid or cyclic tag identity")
        visited.add(sha)
        annotated = client.api("/git/tags/" + sha)
        require(annotated.get("sha") == sha, "tag object identity mismatch")
        if len(visited) == 1:
            require(annotated.get("tag") == tag, "annotated release tag name mismatch")
        verified(annotated.get("verification"))
        obj = annotated.get("object", {})
        require(isinstance(obj, dict), "invalid tag target object")
    sha = obj.get("sha")
    require(obj.get("type") == "commit" and matches(SHA1, sha), "tag does not resolve to a commit")
    commit = client.api("/commits/" + sha)
    require(commit.get("sha") == sha, "source commit identity mismatch")
    require(isinstance(commit.get("commit"), dict), "invalid source commit object")
    verified(commit["commit"].get("verification"))
    return sha


def select_asset(release, name):
    assets = release.get("assets")
    require(isinstance(assets, list) and all(isinstance(item, dict) for item in assets),
            "invalid release asset inventory")
    selected = [item for item in assets if item.get("name") == name]
    require(len(selected) == 1, "release must contain exactly one " + name)
    item = selected[0]
    require(positive_int(item.get("id")) and positive_int(item.get("size"))
            and item["size"] <= MAX_ARCHIVE, "invalid asset ID or size")
    require(matches("sha256:" + SHA256, item.get("digest")), "asset needs a valid API SHA-256 digest")
    url = API + "/releases/assets/" + str(item["id"])
    require(item.get("url") == url, "asset API URL does not match its ID")
    require(item.get("browser_download_url") == SOURCE + "/releases/download/"
            + release["tag_name"] + "/" + name, "asset download URL mismatch")
    require(sum(other.get("id") == item["id"] for other in assets) == 1, "duplicate asset ID")
    return {"name": name, "id": item["id"], "url": url, "size": item["size"],
            "sha256": item["digest"][7:]}


def parse_checksums(data):
    result = {}
    for line in data.decode("ascii").splitlines():
        match = re.fullmatch(r"(" + SHA256 + r") [ *]([A-Za-z0-9][A-Za-z0-9._+-]*)", line)
        require(match is not None, "malformed checksum manifest")
        digest, name = match.groups()
        require(name not in result, "duplicate checksum manifest entry")
        result[name] = digest
    require(result, "empty checksum manifest")
    return result


def resolve(client, release_id=None):
    release = client.api("/releases/" + (str(release_id) if release_id is not None else "latest"))
    tag = stable_release(release)
    if release_id is not None:
        require(release["id"] == release_id, "release ID changed")
    sha = source_commit(client, tag)
    archive = select_asset(release, "Blackcoin-" + tag[1:] + "-Linux-x86_64.tar.gz")
    marker = select_asset(release, "SOURCE_COMMIT.txt")
    manifest = select_asset(release, "SHA256SUMS.txt")
    require(client.small_asset(marker) == (sha + "\n").encode("ascii"),
            "source marker does not match the verified tag commit")
    checksums = parse_checksums(client.small_asset(manifest))
    require(checksums.get(archive["name"]) == archive["sha256"]
            and checksums.get(marker["name"]) == marker["sha256"],
            "checksum manifest disagrees with API asset digests")
    return {"schema_version": 1,
            "release": {"id": release["id"], "tag": tag, "published_at": release["published_at"],
                        "target_sha": sha},
            "asset": archive, "checksum_manifest_sha256": manifest["sha256"],
            "source_marker_sha256": marker["sha256"], "sourceH": sha,
            "version_tag": tag[1:], "source_url": SOURCE + "/tree/" + sha}


def validate_lock(lock):
    require(isinstance(lock, dict) and set(lock) == {
        "schema_version", "release", "asset", "checksum_manifest_sha256",
        "source_marker_sha256", "sourceH", "version_tag", "source_url"}, "invalid lock fields")
    require(type(lock["schema_version"]) is int and lock["schema_version"] == 1, "unsupported lock schema")
    release, asset = lock["release"], lock["asset"]
    require(isinstance(release, dict) and set(release) == {"id", "tag", "published_at", "target_sha"},
            "invalid locked release fields")
    stable_release({**release, "tag_name": release["tag"], "draft": False, "prerelease": False})
    require(matches(SHA1, lock["sourceH"]) and release["target_sha"] == lock["sourceH"]
            and lock["version_tag"] == release["tag"][1:]
            and lock["source_url"] == SOURCE + "/tree/" + lock["sourceH"], "inconsistent source identity")
    require(isinstance(asset, dict) and set(asset) == {"name", "id", "url", "size", "sha256"},
            "invalid locked asset fields")
    require(asset["name"] == "Blackcoin-" + lock["version_tag"] + "-Linux-x86_64.tar.gz"
            and positive_int(asset["id"]) and positive_int(asset["size"])
            and asset["size"] <= MAX_ARCHIVE
            and asset["url"] == API + "/releases/assets/" + str(asset["id"]), "invalid locked asset identity")
    for digest in (asset["sha256"], lock["checksum_manifest_sha256"], lock["source_marker_sha256"]):
        require(matches(SHA256, digest), "invalid locked SHA-256")
    return lock


def validate_elf(header):
    require(len(header) >= 64 and header[:7] == b"\x7fELF\x02\x01\x01"
            and struct.unpack_from("<H", header, 18)[0] == 62
            and struct.unpack_from("<H", header, 16)[0] in (2, 3), "binary is not ELF64 little-endian x86-64")


def extract_archive(archive, destination):
    """Validate every member before writing; do not honor archive paths or modes."""
    with tarfile.open(fileobj=archive, mode="r:gz") as bundle:
        members, names, total = [], set(), 0
        for member in bundle:
            require(member.name in BINARIES and member.name not in names and member.isreg()
                    and not member.issparse() and not member.pax_headers,
                    "archive must contain exactly the six unique root-level regular binaries")
            require(64 <= member.size <= MAX_UNPACKED, "invalid unpacked binary size")
            names.add(member.name)
            total += member.size
            require(total <= MAX_UNPACKED, "archive exceeds unpacked size limit")
            with bundle.extractfile(member) as source:
                validate_elf(source.read(64))
            members.append(member)
        require(names == BINARIES, "archive is missing required binaries")
        destination.mkdir(mode=0o755)
        for member in members:
            target = destination / member.name
            with bundle.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            require(target.stat().st_size == member.size, "truncated archive member")
            target.chmod(0o755)


def safe_directory(path):
    path = path.absolute()
    require(all(not item.is_symlink() for item in (path, *path.parents)), "output path must not use symlinks")
    path.mkdir(parents=True, exist_ok=True)
    require(path.is_dir(), "output must be a directory")
    return path


def fetch(client, lock, output):
    validate_lock(lock)
    require(resolve(client, lock["release"]["id"]) == lock, "locked release identity changed")
    output = safe_directory(Path(output))
    require(not (output / "bin").exists() and not (output / "bin").is_symlink(),
            "output/bin already exists; refusing to replace it")
    with tempfile.TemporaryDirectory(prefix=".verified-release-", dir=output) as temporary:
        stage = Path(temporary)
        with (stage / "archive.tar.gz").open("w+b") as archive:
            client.download(lock["asset"], archive)
            archive.seek(0)
            extract_archive(archive, stage / "bin")
        (stage / "bin").rename(output / "bin")


def recheck(client, lock):
    validate_lock(lock)
    require(resolve(client) == lock, "stable release was superseded or changed; promotion refused")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resolve").add_argument("--output", type=Path, required=True)
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("--lock", type=Path, required=True)
    fetch_parser.add_argument("--output", type=Path, required=True)
    commands.add_parser("recheck").add_argument("--lock", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        client = GitHub()
        if args.command == "resolve":
            lock = resolve(client)
            safe_directory(args.output.parent)
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(lock, output, indent=2, sort_keys=True)
                output.write("\n")
        else:
            require(not args.lock.is_symlink() and args.lock.stat().st_size <= 16384,
                    "invalid lock file")
            lock = validate_lock(decode_json(args.lock.read_bytes()))
            if args.command == "fetch":
                fetch(client, lock, args.output)
            else:
                recheck(client, lock)
        print(json.dumps({"ok": True, "command": args.command, "version": lock["version_tag"],
                          "sourceH": lock["sourceH"]}, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, UnicodeError):
        # Do not print request details, tokens, redirect queries, or remote response bodies.
        print(json.dumps({"ok": False, "command": args.command,
                          "error": "release verification failed; no promotion authorized"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
