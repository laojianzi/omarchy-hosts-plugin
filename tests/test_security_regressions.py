from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from omarchy_hosts import cli
from omarchy_hosts.process_control import ProcessControlError, run_bounded_process
from omarchy_hosts.store import StateStore, StoreError


class RootStateDescriptorRegressions(unittest.TestCase):
    def _patch_root_state(self, path: Path):
        return (
            mock.patch.object(cli, "ROOT_STATE_PATH", path),
            mock.patch.object(cli, "ROOT_STATE_OWNER_UID", os.getuid()),
        )

    def test_root_state_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "state.json"
            path.write_text('{"schemaVersion":1}\n', encoding="utf-8")
            path.chmod(0o600)
            os.link(path, root / "second-link.json")
            first, second = self._patch_root_state(path)
            with first, second:
                self.assertIsNone(cli.read_root_state())

    def test_root_state_group_writable_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            path.write_text('{"schemaVersion":1}\n', encoding="utf-8")
            path.chmod(0o620)
            first, second = self._patch_root_state(path)
            with first, second:
                self.assertIsNone(cli.read_root_state())


class StateDirectoryRegressions(unittest.TestCase):
    def test_symlinked_config_home_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            actual = root / "actual"
            actual.mkdir(mode=0o700)
            link = root / "config"
            link.symlink_to(actual, target_is_directory=True)
            store = StateStore(link)
            with self.assertRaises(StoreError):
                store.load()

    def test_compatibility_lock_is_private_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = StateStore(Path(raw) / "config")
            store.load()
            info = os.lstat(store.lock_path)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)


class BoundedProcessRegressions(unittest.TestCase):
    def test_stderr_limit_is_enforced(self) -> None:
        with self.assertRaises(ProcessControlError) as raised:
            run_bounded_process(
                [sys.executable, "-c", "import os; os.write(2, b'e' * 65536)"],
                timeout=5,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertEqual(raised.exception.reason, "output_limit")

    def test_successful_process_returns_both_streams(self) -> None:
        result = run_bounded_process(
            [
                sys.executable,
                "-c",
                "import os; os.write(1,b'out'); os.write(2,b'err')",
            ],
            timeout=5,
            stdout_limit=1024,
            stderr_limit=1024,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.stderr, b"err")


if __name__ == "__main__":
    unittest.main()
