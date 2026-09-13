import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import promote
import release

H, W = "a" * 40, "b" * 40
ID, DIGEST = "sha256:" + "c" * 64, "sha256:" + "d" * 64
LOCK = {"schema_version": 1,
        "release": {"id": 1, "tag": "v30.1.5.1", "published_at": "2026-09-12T14:22:51Z", "target_sha": H},
        "asset": {"name": "Blackcoin-30.1.5.1-Linux-x86_64.tar.gz", "id": 2,
                  "url": release.API + "/releases/assets/2", "size": 100, "sha256": "e" * 64},
        "checksum_manifest_sha256": "f" * 64, "source_marker_sha256": "1" * 64,
        "sourceH": H, "version_tag": "30.1.5.1", "source_url": release.SOURCE + "/tree/" + H}
EXPECTED = promote.identity(LOCK, W)
INFO = {"Id": ID, "Os": "linux", "Architecture": "amd64",
        "Config": {"Labels": {label: EXPECTED[key] for key, label in promote.LABELS.items()}}}
RECORD = {"digest": DIGEST, "image": promote.REPOSITORY + "@" + DIGEST, "image_id": ID}
RECEIPT = {"schema_version": 1, "image_id": ID, "core_version": EXPECTED["version"],
           "core_source": H, "wrapper_revision": W, "passed": True, "checks": sorted(promote.CHECKS)}


class FakeDocker:
    def __init__(self, registry=None):
        self.records = registry or {}
        self.events = []
        self.infos = {ID: copy.deepcopy(INFO)}

    def registry(self, ref):
        return copy.deepcopy(self.records.get(ref))

    def verify(self, image, expected, require_wrapper=True):
        record = next((r for r in self.records.values() if r["image"] == image), RECORD)
        return promote.validate_image(self.infos[record["image_id"]], expected, require_wrapper)

    def verify_remote(self, record, expected, require_wrapper=True):
        promote.validate_image(self.infos[record["image_id"]], expected, require_wrapper)

    def remote_identity(self, record):
        labels = self.infos[record["image_id"]]["Config"]["Labels"]
        expected = {key: labels.get(label) for key, label in promote.LABELS.items()}
        promote.numeric_version(expected["version"])
        promote.validate_image(self.infos[record["image_id"]], expected)
        return expected

    def command(self, *args, **kwargs):
        self.events.append(args)
        if args[0] == "push":
            self.records[args[1]] = copy.deepcopy(RECORD)


class PromotionTests(unittest.TestCase):
    def test_identity_rejects_platform_labels_and_bad_id(self):
        self.assertEqual(promote.validate_image(INFO, EXPECTED), ID)
        for field, value in (("Architecture", "arm64"), ("Os", "windows"), ("Id", "wrong")):
            with self.assertRaises(ValueError):
                promote.validate_image({**INFO, field: value}, EXPECTED)
        for label in promote.LABELS.values():
            changed = copy.deepcopy(INFO)
            changed["Config"]["Labels"][label] = "wrong"
            with self.assertRaises(ValueError):
                promote.validate_image(changed, EXPECTED)

    def test_actual_binary_requires_exact_version_full_source_and_clean(self):
        output = "Blackcoin version v30.1.5.1\nSource commit: " + H
        promote.validate_version(output, EXPECTED)
        for changed in (output.replace("v30.1.5.1", "v30.1.5.10"), output.replace(H, H[:12]),
                        output + " (dirty)", output.replace("v30.1.5.1", "v30.1.5.1rc1"),
                        *[output.replace("v30.1.5.1", "v30.1.5.1" + suffix)
                          for suffix in ("-alpha1", "-beta1", "-pre1", "-rc1", "+build1")],
                        output + "\nBlackcoin version v30.1.5.1"):
            with self.assertRaises(ValueError):
                promote.validate_version(changed, EXPECTED)

    def test_receipt_binds_every_identity_and_all_checks(self):
        promote.validate_receipt(RECEIPT, EXPECTED, ID)
        for field in ("image_id", "core_version", "core_source", "wrapper_revision", "passed"):
            with self.assertRaises(ValueError):
                promote.validate_receipt({**RECEIPT, field: "wrong"}, EXPECTED, ID)
        for checks in ([], RECEIPT["checks"][:-1], RECEIPT["checks"] + [RECEIPT["checks"][0]], [True]):
            with self.assertRaises(ValueError):
                promote.validate_receipt({**RECEIPT, "checks": checks}, EXPECTED, ID)

    def test_inspect_build_reuse_and_current(self):
        candidate, version = promote.tags(LOCK, W)
        for records, action in (({}, "build"), ({candidate: RECORD}, "reuse"),
                                ({candidate: RECORD, version: RECORD, promote.REPOSITORY + ":latest": RECORD}, "current")):
            docker = FakeDocker(records)
            self.assertEqual(promote.inspect(docker, LOCK, W)["action"], action)
            self.assertEqual(docker.events, [])

    def test_old_wrapper_version_is_preserved(self):
        candidate, version = promote.tags(LOCK, W)
        old = {"digest": "sha256:" + "2" * 64, "image": promote.REPOSITORY + "@sha256:" + "2" * 64,
               "image_id": "sha256:" + "3" * 64}
        docker = FakeDocker({version: old})
        docker.infos[old["image_id"]] = copy.deepcopy(INFO)
        docker.infos[old["image_id"]]["Id"] = old["image_id"]
        docker.infos[old["image_id"]]["Config"]["Labels"][promote.LABELS["wrapper"]] = "4" * 40
        result = promote.publish(docker, LOCK, W, "local:candidate", RECEIPT, lambda: None)
        self.assertTrue(result["published"])
        self.assertNotIn(("push", version), docker.events)
        self.assertEqual(docker.records[version], old)
        self.assertIn(("push", candidate), docker.events)

    def test_recheck_failure_prevents_latest_write(self):
        docker = FakeDocker()
        def stale():
            raise ValueError("release superseded")
        with self.assertRaisesRegex(ValueError, "superseded"):
            promote.publish(docker, LOCK, W, "local:candidate", RECEIPT, stale)
        self.assertNotIn(("push", promote.REPOSITORY + ":latest"), docker.events)

    def test_concurrent_latest_change_is_not_overwritten(self):
        docker = FakeDocker()
        def changed():
            docker.records[promote.REPOSITORY + ":latest"] = copy.deepcopy(RECORD)
        with self.assertRaisesRegex(ValueError, "changed concurrently"):
            promote.publish(docker, LOCK, W, "local:candidate", RECEIPT, changed)
        self.assertNotIn(("push", promote.REPOSITORY + ":latest"), docker.events)

    def test_wrong_existing_version_fails_before_any_push(self):
        _, version = promote.tags(LOCK, W)
        docker = FakeDocker({version: RECORD})
        docker.infos[ID]["Config"]["Labels"][promote.LABELS["source"]] = "0" * 40
        with self.assertRaises(ValueError):
            promote.publish(docker, LOCK, W, "local:candidate", RECEIPT, lambda: None)
        self.assertEqual(docker.events, [])

    def test_current_latest_or_durable_state_prevents_downgrade(self):
        docker = FakeDocker()
        docker.infos[ID]["Config"]["Labels"][promote.LABELS["version"]] = "v30.1.6"
        with self.assertRaisesRegex(ValueError, "older than verified latest"):
            promote.guard_latest(docker, RECORD, EXPECTED)
        with self.assertRaisesRegex(ValueError, "older than durable"):
            promote.guard_latest(docker, None, EXPECTED,
                {"schema_version": 1, "published": True, "core_version": "v30.1.5.2"})
        self.assertEqual(promote.numeric_version("v30.1.5"), promote.numeric_version("v30.1.5.0"))

    def test_legacy_bootstrap_is_exact_explicit_and_one_time(self):
        docker = FakeDocker()
        legacy = {**RECORD, "digest": promote.LEGACY_DIGEST}
        with self.assertRaisesRegex(ValueError, "explicit first-publication"):
            promote.guard_latest(docker, legacy, EXPECTED)
        promote.guard_latest(docker, legacy, EXPECTED, allow_legacy=True)
        with self.assertRaisesRegex(ValueError, "explicit first-publication"):
            promote.guard_latest(docker, legacy, EXPECTED,
                {"schema_version": 1, "published": True, "core_version": "v30.1.5.1"}, True)
        docker.infos[ID]["Config"]["Labels"] = {}
        with self.assertRaises(ValueError):
            promote.guard_latest(docker, RECORD, EXPECTED, allow_legacy=True)


if __name__ == "__main__":
    unittest.main()
