from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from omarchy_hosts.engine import profiles_config_sha256, sha256_bytes


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "system" / "helper.py"
spec = importlib.util.spec_from_file_location("omarchy_hosts_test_helper", HELPER_PATH)
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helper
spec.loader.exec_module(helper)


def enabled_profile() -> dict[str, object]:
    return {
        "id": "development",
        "name": "Development",
        "description": "",
        "enabled": True,
        "entries": [{"address": "127.0.0.1", "names": ["app.test"]}],
    }


class CandidateValidationTests(unittest.TestCase):
    def payload(self, *, uid: int = 1000, created: datetime | None = None) -> dict[str, object]:
        profiles = [enabled_profile()]
        when = created or datetime.now(timezone.utc)
        return {
            "schemaVersion": 1,
            "requestUid": uid,
            "createdAt": when.isoformat().replace("+00:00", "Z"),
            "baseSha256": "a" * 64,
            "configSha256": profiles_config_sha256(profiles),
            "profiles": profiles,
        }

    def test_valid_payload_is_normalized(self) -> None:
        now = datetime.now(timezone.utc)
        result = helper._validate_candidate_payload(self.payload(created=now), 1000, now=now)
        self.assertEqual(result["profiles"][0]["id"], "development")

    def test_wrong_uid_is_rejected(self) -> None:
        with self.assertRaises(helper.PrivilegedError) as raised:
            helper._validate_candidate_payload(self.payload(uid=1001), 1000)
        self.assertEqual(raised.exception.code, "candidate_uid")

    def test_expired_payload_is_rejected(self) -> None:
        now = datetime.now(timezone.utc)
        with self.assertRaises(helper.PrivilegedError) as raised:
            helper._validate_candidate_payload(
                self.payload(created=now - timedelta(minutes=16)), 1000, now=now
            )
        self.assertEqual(raised.exception.code, "candidate_expired")

    def test_disabled_profile_is_rejected(self) -> None:
        payload = self.payload()
        payload["profiles"][0]["enabled"] = False  # type: ignore[index]
        payload["configSha256"] = profiles_config_sha256(payload["profiles"])
        with self.assertRaises(helper.PrivilegedError) as raised:
            helper._validate_candidate_payload(payload, 1000)
        self.assertEqual(raised.exception.code, "candidate_profiles")

    def test_tampered_profile_hash_is_rejected(self) -> None:
        payload = self.payload()
        payload["profiles"][0]["entries"][0]["names"] = ["changed.test"]  # type: ignore[index]
        with self.assertRaises(helper.PrivilegedError) as raised:
            helper._validate_candidate_payload(payload, 1000)
        self.assertEqual(raised.exception.code, "candidate_hash")


class CandidateFileTests(unittest.TestCase):
    def _payload_bytes(self, uid: int) -> bytes:
        profiles = [enabled_profile()]
        payload = {
            "schemaVersion": 1,
            "requestUid": uid,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "baseSha256": "a" * 64,
            "configSha256": profiles_config_sha256(profiles),
            "profiles": profiles,
        }
        return (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")

    def test_candidate_filename_binds_exact_bytes(self) -> None:
        uid = os.getuid()
        encoded = self._payload_bytes(uid)
        digest = hashlib.sha256(encoded).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            name = f"request-{digest}-{'1' * 32}.json"
            candidate = directory / name
            candidate.write_bytes(encoded)
            candidate.chmod(0o600)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(
                    helper,
                    "_open_candidate_directory",
                    side_effect=lambda _uid: os.dup(directory_fd),
                ):
                    result = helper.read_candidate(
                        f"/run/user/{uid}/omarchy-hosts/candidates/{name}",
                        uid,
                    )
            finally:
                os.close(directory_fd)
        self.assertEqual(result["requestUid"], uid)
        self.assertEqual(result["profiles"][0]["id"], "development")

    def test_candidate_content_substitution_is_rejected(self) -> None:
        uid = os.getuid()
        original = self._payload_bytes(uid)
        digest = hashlib.sha256(original).hexdigest()
        tampered = original.replace(b"app.test", b"api.test")
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            name = f"request-{digest}-{'2' * 32}.json"
            candidate = directory / name
            candidate.write_bytes(tampered)
            candidate.chmod(0o600)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(
                    helper,
                    "_open_candidate_directory",
                    side_effect=lambda _uid: os.dup(directory_fd),
                ):
                    with self.assertRaises(helper.PrivilegedError) as raised:
                        helper.read_candidate(
                            f"/run/user/{uid}/omarchy-hosts/candidates/{name}",
                            uid,
                        )
            finally:
                os.close(directory_fd)
        self.assertEqual(raised.exception.code, "candidate_changed")

    def test_candidate_hardlink_is_rejected(self) -> None:
        uid = os.getuid()
        encoded = self._payload_bytes(uid)
        digest = hashlib.sha256(encoded).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            name = f"request-{digest}-{'3' * 32}.json"
            candidate = directory / name
            candidate.write_bytes(encoded)
            candidate.chmod(0o600)
            os.link(candidate, directory / "second-link.json")
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(
                    helper,
                    "_open_candidate_directory",
                    side_effect=lambda _uid: os.dup(directory_fd),
                ):
                    with self.assertRaises(helper.PrivilegedError) as raised:
                        helper.read_candidate(
                            f"/run/user/{uid}/omarchy-hosts/candidates/{name}",
                            uid,
                        )
            finally:
                os.close(directory_fd)
        self.assertEqual(raised.exception.code, "candidate_unsafe")


class WatchdogTests(unittest.TestCase):
    def test_hard_deadline_aborts_stuck_helper(self) -> None:
        def blocked(_argv: list[str] | None = None) -> int:
            time.sleep(5)
            return 0

        with mock.patch.object(helper, "PRIVILEGED_DEADLINE_SECONDS", 0.05):
            with self.assertRaises(helper.PrivilegedError) as raised:
                with helper._privileged_watchdog():
                    blocked([])
        self.assertEqual(raised.exception.code, "helper_timeout")


class AtomicReplaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.target = self.directory / "hosts"
        self.original = b"127.0.0.1 localhost\n"
        self.proposed = self.original + b"127.0.0.1 app.test\n"
        self.target.write_bytes(self.original)
        os.chmod(self.target, 0o644)

    def call_replace(self) -> None:
        original_stat = self.target.stat()
        helper.atomic_replace(
            self.target,
            self.proposed,
            sha256_bytes(self.original),
            original_stat,
        )

    def test_atomic_replace_installs_complete_content(self) -> None:
        try:
            self.call_replace()
        except helper.PrivilegedError as exc:
            if exc.code == "atomic_exchange_unavailable":
                self.skipTest(exc.message)
            raise
        self.assertEqual(self.target.read_bytes(), self.proposed)
        self.assertEqual(list(self.directory.glob(".hosts.omarchy-hosts-*.tmp")), [])

    def test_pre_exchange_content_race_restores_concurrent_version(self) -> None:
        real_exchange = helper._rename_exchange
        concurrent = b"127.0.0.1 localhost\n10.0.0.2 concurrent.test\n"

        def inject_race(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
            self.target.write_bytes(concurrent)
            real_exchange(source_fd, source, destination_fd, destination)

        with mock.patch.object(helper, "_rename_exchange", side_effect=inject_race):
            with self.assertRaises(helper.PrivilegedError) as raised:
                self.call_replace()
        if raised.exception.code == "atomic_exchange_unavailable":
            self.skipTest(raised.exception.message)
        self.assertEqual(raised.exception.code, "hosts_stale")
        self.assertEqual(self.target.read_bytes(), concurrent)
        self.assertEqual(list(self.directory.glob(".hosts.omarchy-hosts-*.tmp")), [])

    def test_post_exchange_race_preserves_newer_target_and_recovery(self) -> None:
        real_exchange = helper._rename_exchange
        concurrent = b"127.0.0.1 localhost\n10.0.0.3 newer.test\n"

        def inject_race(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
            real_exchange(source_fd, source, destination_fd, destination)
            replacement = self.directory / "newer"
            replacement.write_bytes(concurrent)
            os.chmod(replacement, 0o644)
            os.replace(replacement, self.target)

        with mock.patch.object(helper, "_rename_exchange", side_effect=inject_race):
            with self.assertRaises(helper.PrivilegedError) as raised:
                self.call_replace()
        if raised.exception.code == "atomic_exchange_unavailable":
            self.skipTest(raised.exception.message)
        self.assertEqual(raised.exception.code, "hosts_changed_after_commit")
        self.assertEqual(self.target.read_bytes(), concurrent)
        recovery = list(self.directory.glob(".hosts.omarchy-hosts-*.tmp"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
