"""Focused offline tests for retryable, release-bound binary staging."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import reconcile
import release

H = "a" * 40
LOCK = {"schema_version": 1,
        "release": {"id": 1, "tag": "v30.1.5.1", "published_at": "2026-09-12T14:22:51Z", "target_sha": H},
        "asset": {"name": "Blackcoin-30.1.5.1-Linux-x86_64.tar.gz", "id": 2,
                  "url": release.API + "/releases/assets/2", "size": 100, "sha256": "b" * 64},
        "checksum_manifest_sha256": "c" * 64, "source_marker_sha256": "d" * 64,
        "sourceH": H, "version_tag": "30.1.5.1", "source_url": release.SOURCE + "/tree/" + H}


class ContextCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.build = Path(self.temporary.name).resolve() / "context"
        self.fetch = mock.patch.object(release, "fetch", side_effect=self.fetch_binaries).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(reconcile.shutil, "copy2").start()
        mock.patch.object(reconcile.shutil, "copytree").start()

    @staticmethod
    def fetch_binaries(client, lock, build):
        (build / "bin").mkdir()
        for name in release.BINARIES:
            (build / "bin" / name).write_bytes((name + lock["asset"]["sha256"]).encode())

    def prepare(self, lock=LOCK):
        reconcile.prepare_context(None, lock, self.build)

    def test_exact_complete_cache_reused_without_download(self):
        self.prepare()
        metadata = json.loads((self.build / "binary-checksums.json").read_text())
        self.assertEqual(metadata["lock"], LOCK)
        self.assertEqual(set(metadata["binaries"]), release.BINARIES)
        self.prepare()
        self.assertEqual(self.fetch.call_count, 1)

    def test_interruption_between_binary_publish_and_metadata_retries_only_stage(self):
        with mock.patch.object(reconcile, "write_json", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.build / "bin").is_dir())
        self.assertFalse((self.build / "binary-checksums.json").exists())
        marker = self.build / "other-stage.receipt"
        marker.write_text("keep")
        self.prepare()
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(marker.read_text(), "keep")
        self.assertTrue((self.build / "binary-checksums.json").is_file())

    def test_changed_full_lock_refetches_even_when_cached_hashes_match(self):
        self.prepare()
        changed = copy.deepcopy(LOCK)
        changed["asset"]["sha256"] = "e" * 64
        self.prepare(changed)
        self.assertEqual(self.fetch.call_count, 2)
        self.assertEqual(json.loads((self.build / "binary-checksums.json").read_text())["lock"], changed)

    def test_corrupt_or_malformed_cache_refetches(self):
        self.prepare()
        (self.build / "bin" / "blackcoin-qt").write_bytes(b"corrupt")
        self.prepare()
        self.assertEqual(self.fetch.call_count, 2)
        (self.build / "binary-checksums.json").write_text("{")
        self.prepare()
        self.assertEqual(self.fetch.call_count, 3)

    def test_unknown_files_and_symlinks_fail_without_discarding(self):
        self.prepare()
        unknown = self.build / "bin" / "not-generated"
        unknown.write_text("preserve")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(unknown.read_text(), "preserve")
        unknown.unlink()
        binary = self.build / "bin" / "blackcoin-qt"
        binary.unlink()
        binary.symlink_to(self.build / "binary-checksums.json")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertTrue(binary.is_symlink())
        self.assertEqual(self.fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
