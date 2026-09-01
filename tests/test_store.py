from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from omarchy_hosts.store import StateStore, StoreError


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config_home = Path(self.temp.name)
        self.store = StateStore(self.config_home)

    def test_default_state_and_secure_paths(self) -> None:
        self.assertEqual(self.store.load()["profiles"], [])
        self.assertEqual(stat.S_IMODE(self.store.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.lock_path.stat().st_mode), 0o600)

    def test_save_profile_generates_slug_and_persists_mode(self) -> None:
        saved = self.store.save_profile(
            {
                "name": "Local Development",
                "description": "stack",
                "enabled": True,
                "entriesText": "127.0.0.1 app.test api.test\n",
            }
        )
        self.assertEqual(saved["id"], "local-development")
        self.assertEqual(saved["entryCount"] if "entryCount" in saved else len(saved["entries"]), 1)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        persisted = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["profiles"][0]["id"], "local-development")

    def test_update_preserves_created_timestamp(self) -> None:
        created = self.store.save_profile({"name": "Dev", "entriesText": "127.0.0.1 a.test\n"})
        updated = self.store.save_profile(
            {"id": created["id"], "name": "Dev updated", "entriesText": "127.0.0.1 b.test\n"}
        )
        self.assertEqual(updated["createdAt"], created["createdAt"])
        self.assertEqual(updated["id"], created["id"])

    def test_toggle_and_delete(self) -> None:
        saved = self.store.save_profile({"name": "Dev", "entriesText": "127.0.0.1 a.test\n"})
        self.assertTrue(self.store.toggle_profile(saved["id"], True)["enabled"])
        self.assertEqual(self.store.delete_profile(saved["id"])["id"], saved["id"])
        self.assertEqual(self.store.load()["profiles"], [])

    def test_repeated_names_receive_unique_generated_ids(self) -> None:
        first = self.store.save_profile({"name": "Dev", "entries": []})
        second = self.store.save_profile({"name": "Dev", "entries": []})
        self.assertEqual(first["id"], "dev")
        self.assertNotEqual(second["id"], first["id"])
        self.assertTrue(second["id"].startswith("dev-"))

    def test_insecure_state_mode_is_rejected(self) -> None:
        self.store.save_profile({"name": "Dev", "entries": []})
        os.chmod(self.store.path, 0o644)
        with self.assertRaises(StoreError) as raised:
            self.store.load()
        self.assertEqual(raised.exception.code, "state_permissions")

    def test_state_symlink_is_rejected(self) -> None:
        self.store.directory.mkdir(parents=True, mode=0o700)
        victim = self.config_home / "victim.json"
        victim.write_text("{}", encoding="utf-8")
        self.store.path.symlink_to(victim)
        with self.assertRaises(StoreError) as raised:
            self.store.load()
        self.assertEqual(raised.exception.code, "state_unsafe")

    def test_state_hardlink_is_rejected(self) -> None:
        self.store.save_profile({"name": "Dev", "entries": []})
        os.link(self.store.path, self.config_home / "state-copy.json")
        with self.assertRaises(StoreError) as raised:
            self.store.load()
        self.assertEqual(raised.exception.code, "state_unsafe")

    def test_last_apply_round_trip(self) -> None:
        metadata = {"afterSha256": "a" * 64, "backup": "hosts.example.bak"}
        self.store.update_last_apply(metadata)
        self.assertEqual(self.store.load()["lastApply"], metadata)
        self.store.update_last_apply(None)
        self.assertIsNone(self.store.load()["lastApply"])


if __name__ == "__main__":
    unittest.main()
