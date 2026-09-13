#!/usr/bin/env python3
"""Offline regression tests for the public stable-release boundary."""

import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("release", Path(__file__).parents[1] / "tools/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
H = "bd5dad9aef27aff1f1bff37d6e5464b8305cd3ca"
TAG_SHA = "a" * 40
VERIFIED = {"verified": True, "reason": "valid"}


def elf(machine=62):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HH", header, 16, 3, machine)
    return bytes(header)


def archive_bytes(entries=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.GNU_FORMAT) as bundle:
        for name, kind, content in entries or [(name, tarfile.REGTYPE, elf())
                                               for name in sorted(release.BINARIES)]:
            member = tarfile.TarInfo(name)
            member.type, member.mode = kind, 0o6777
            member.size = len(content) if kind == tarfile.REGTYPE else 0
            member.linkname = "blackcoind" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            bundle.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


class FakeGitHub:
    def __init__(self, version="30.1.5.1"):
        self.tag = "v" + version
        self.archive = archive_bytes()
        self.bodies = {}
        self.release = {"id": 123, "tag_name": self.tag, "draft": False, "prerelease": False,
                        "published_at": "2026-09-12T14:22:51Z", "assets": []}
        binary = self.add_asset(1, "Blackcoin-" + version + "-Linux-x86_64.tar.gz", self.archive)
        marker = self.add_asset(2, "SOURCE_COMMIT.txt", (H + "\n").encode())
        manifest = (binary["digest"][7:] + "  " + binary["name"] + "\n"
                    + marker["digest"][7:] + "  SOURCE_COMMIT.txt\n").encode()
        self.add_asset(3, "SHA256SUMS.txt", manifest)
        self.reference = {"ref": "refs/tags/" + self.tag, "object": {"sha": TAG_SHA, "type": "tag"}}
        self.tag_object = {"sha": TAG_SHA, "tag": self.tag, "verification": copy.copy(VERIFIED),
                           "object": {"sha": H, "type": "commit"}}
        self.commit = {"sha": H, "commit": {"verification": copy.copy(VERIFIED)}}
        self.calls = []

    def add_asset(self, identifier, name, body):
        item = {"name": name, "id": identifier, "size": len(body),
                "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
                "url": release.API + "/releases/assets/" + str(identifier),
                "browser_download_url": release.SOURCE + "/releases/download/" + self.tag + "/" + name}
        self.release["assets"].append(item)
        self.bodies[item["url"]] = body
        return item

    def api(self, path):
        self.calls.append(path)
        if path in ("/releases/latest", "/releases/123"):
            return copy.deepcopy(self.release)
        if path == "/git/ref/tags/" + self.tag:
            return copy.deepcopy(self.reference)
        if path == "/git/tags/" + TAG_SHA:
            return copy.deepcopy(self.tag_object)
        if path == "/commits/" + H:
            return copy.deepcopy(self.commit)
        raise AssertionError("unexpected API path: " + path)

    def download(self, asset, output):
        data = self.bodies[asset["url"]]
        release.require(len(data) == asset["size"] and hashlib.sha256(data).hexdigest() == asset["sha256"],
                        "asset mismatch")
        output.write(data)

    def small_asset(self, asset):
        output = io.BytesIO()
        self.download(asset, output)
        return output.getvalue()


class ReleaseTests(unittest.TestCase):
    def test_resolve_exact_identity_and_three_or_four_component_versions(self):
        for version in ("30.1.5", "30.1.5.1", "0.0.0", "100.12.23.4"):
            with self.subTest(version=version):
                lock = release.resolve(FakeGitHub(version))
                self.assertEqual(release.validate_lock(lock), lock)
                self.assertEqual(lock["sourceH"], H)
                self.assertEqual(lock["release"]["target_sha"], H)
                self.assertEqual(lock["version_tag"], version)
                self.assertEqual(lock["source_url"], release.SOURCE + "/tree/" + H)

    def test_reject_nonstable_release_and_bad_version_names(self):
        for field, value in [("draft", True), ("prerelease", True), ("draft", 0),
                             ("prerelease", None), ("id", True), ("published_at", "2026-99-12T14:22:51Z")]:
            client = FakeGitHub()
            client.release[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                release.resolve(client)
        for tag in ("master", "unreleased", "v30.1.5-rc1", "30.1.5", "v30.1", "v30.1.5.1.2",
                    "v030.1.5", "v30.1.5/evil", "v30.1.5\n"):
            client = FakeGitHub()
            client.release["tag_name"] = tag
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.resolve(client)

    def test_requires_matching_verified_annotated_tag_and_commit(self):
        mutations = [lambda c: c.reference["object"].update(type="commit"),
                     lambda c: c.reference.update(object=[]),
                     lambda c: c.reference.update(ref="refs/tags/other"),
                     lambda c: c.tag_object.update(tag="other"),
                     lambda c: c.tag_object.update(sha="b" * 40),
                     lambda c: c.tag_object["verification"].update(verified=False),
                     lambda c: c.tag_object["verification"].update(reason="unknown_key"),
                     lambda c: c.tag_object["object"].update(type="tag", sha=TAG_SHA),
                     lambda c: c.tag_object.update(object=[]),
                     lambda c: c.commit.update(sha="b" * 40),
                     lambda c: c.commit.update(commit=[]),
                     lambda c: c.commit["commit"]["verification"].update(verified=False)]
        for index, mutation in enumerate(mutations):
            client = FakeGitHub()
            mutation(client)
            with self.subTest(index=index), self.assertRaises(ValueError):
                release.resolve(client)

    def test_reject_asset_identity_size_digest_and_duplicates(self):
        changes = {"id": True, "size": 0, "digest": "sha256:" + "g" * 64,
                   "url": "https://evil.example/archive", "browser_download_url": "http://github.com/evil"}
        for field, value in changes.items():
            client = FakeGitHub()
            client.release["assets"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                release.resolve(client)
        for duplicate_name in (True, False):
            client = FakeGitHub()
            extra = copy.copy(client.release["assets"][0])
            if not duplicate_name:
                extra["name"] = "other"
            client.release["assets"].append(extra)
            with self.subTest(duplicate_name=duplicate_name), self.assertRaises(ValueError):
                release.resolve(client)

    def test_metadata_bytes_are_verified_against_api_digests(self):
        for asset_index in (1, 2):
            client = FakeGitHub()
            client.bodies[client.release["assets"][asset_index]["url"]] += b"x"
            with self.subTest(asset_index=asset_index), self.assertRaises(ValueError):
                release.resolve(client)

    def test_source_marker_must_equal_tag_commit_even_with_valid_digest(self):
        client = FakeGitHub()
        marker = client.release["assets"][1]
        body = ("b" * 40 + "\n").encode()
        client.bodies[marker["url"]] = body
        marker["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()
        with self.assertRaisesRegex(ValueError, "source marker"):
            release.resolve(client)

    def test_manifest_malformed_duplicate_traversal_and_mismatch(self):
        line = "a" * 64 + "  binary\n"
        for data in (b"", b"bad\n", line.encode() * 2, ("a" * 64 + "  ../binary\n").encode(),
                     ("g" * 64 + "  binary\n").encode()):
            with self.subTest(data=data), self.assertRaises(ValueError):
                release.parse_checksums(data)
        client = FakeGitHub()
        manifest = client.release["assets"][2]
        body = b"a" * 64 + b"  wrong-file\n"
        client.bodies[manifest["url"]] = body
        manifest.update(size=len(body), digest="sha256:" + hashlib.sha256(body).hexdigest())
        with self.assertRaisesRegex(ValueError, "manifest disagrees"):
            release.resolve(client)

    def test_lock_rejects_unknown_duplicate_and_inconsistent_fields(self):
        good = release.resolve(FakeGitHub())
        for field, value in (("schema_version", True), ("sourceH", "b" * 40),
                             ("version_tag", "30.1.6"), ("source_url", "https://evil.example"),
                             ("source_marker_sha256", "bad"), ("extra", 1)):
            lock = copy.deepcopy(good)
            lock[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                release.validate_lock(lock)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            release.decode_json('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):
            release.decode_json('{"value":NaN}')

    def test_fetch_verifies_locked_release_not_latest_and_extracts_all_six(self):
        client = FakeGitHub()
        lock = release.resolve(client)
        client.calls.clear()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary).resolve()
            release.fetch(client, lock, output)
            self.assertIn("/releases/123", client.calls)
            self.assertNotIn("/releases/latest", client.calls)
            self.assertEqual({path.name for path in (output / "bin").iterdir()}, release.BINARIES)
            for path in (output / "bin").iterdir():
                self.assertEqual(path.read_bytes(), elf())
                self.assertEqual(path.stat().st_mode & 0o7777, 0o755)
            with self.assertRaisesRegex(ValueError, "already exists"):
                release.fetch(client, lock, output)

    def test_fetch_refuses_changed_release_and_corrupted_archive(self):
        for metadata_change in (True, False):
            client = FakeGitHub()
            lock = release.resolve(client)
            if metadata_change:
                client.release["assets"][0]["size"] += 1
            else:
                client.bodies[lock["asset"]["url"]] = b"invalid archive"
            with tempfile.TemporaryDirectory() as temporary:
                with self.subTest(metadata_change=metadata_change), self.assertRaises(ValueError):
                    release.fetch(client, lock, Path(temporary).resolve())
                self.assertFalse((Path(temporary) / "bin").exists())

    def test_archive_rejects_extras_missing_duplicate_paths_links_and_wrong_elf(self):
        base = [(name, tarfile.REGTYPE, elf()) for name in sorted(release.BINARIES)]
        cases = [base + [("extra", tarfile.REGTYPE, elf())], base[:-1], base + [base[0]]]
        for name, kind, body in [("../blackcoind", tarfile.REGTYPE, elf()),
                                 ("/blackcoind", tarfile.REGTYPE, elf()),
                                 ("bin/blackcoind", tarfile.REGTYPE, elf()),
                                 ("blackcoind", tarfile.SYMTYPE, b""),
                                 ("blackcoind", tarfile.LNKTYPE, b""),
                                 ("blackcoind", tarfile.DIRTYPE, b""),
                                 ("blackcoind", tarfile.REGTYPE, elf(183)),
                                 ("blackcoind", tarfile.REGTYPE, b"not ELF")]:
            cases.append([entry for entry in base if entry[0] != "blackcoind"] + [(name, kind, body)])
        for index, entries in enumerate(cases):
            with tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "bin"
                with self.subTest(index=index), self.assertRaises(ValueError):
                    release.extract_archive(io.BytesIO(archive_bytes(entries)), destination)
                self.assertFalse(destination.exists())

    def test_recheck_refuses_superseded_or_changed_stable_release(self):
        client = FakeGitHub()
        lock = release.resolve(client)
        release.recheck(client, lock)
        with self.assertRaisesRegex(ValueError, "superseded"):
            release.recheck(FakeGitHub("30.1.5.2"), lock)
        client.release["published_at"] = "2026-09-13T14:22:51Z"
        with self.assertRaisesRegex(ValueError, "superseded"):
            release.recheck(client, lock)

    def test_transport_digest_and_size_enforced(self):
        client = release.GitHub()
        asset = {"url": release.API + "/releases/assets/1", "size": 64, "sha256": "a" * 64}
        for result in ((63, "a" * 64), (64, "b" * 64)):
            with mock.patch.object(client, "transfer", return_value=result), self.assertRaises(ValueError):
                client.download(asset, io.BytesIO())

    def test_only_exact_https_download_hosts_and_no_token_redirect(self):
        for url in ("http://api.github.com/x", "https://api.github.com.evil.example/x",
                    "https://user:pass@github.com/x", "https://github.com:444/x", "https://evil.example/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                release.check_url(url)
        request = release.Request(release.API + "/releases/assets/1", headers={"Authorization": "secret"})
        redirected = release.SafeRedirect().redirect_request(
            request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/example")
        self.assertFalse(redirected.has_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
