from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from omarchy_hosts import cli
from omarchy_hosts.securefs import open_directory_path


class CandidateStagingTests(unittest.TestCase):
    def test_candidate_name_binds_exact_bytes_and_cleanup_uses_dirfd(self) -> None:
        state = {
            "schemaVersion": 1,
            "profiles": [],
            "lastApply": None,
        }
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "candidates"
            directory.mkdir(mode=0o700)
            held_fd = open_directory_path(directory)
            try:
                with mock.patch.object(
                    cli,
                    "_runtime_candidate_directory",
                    side_effect=lambda: (os.dup(held_fd), directory),
                ):
                    with cli.stage_candidate(state, "a" * 64) as request:
                        encoded = request.path.read_bytes()
                        digest = hashlib.sha256(encoded).hexdigest()
                        self.assertEqual(request.sha256, digest)
                        match = re.fullmatch(
                            r"request-([0-9a-f]{64})-[0-9a-f]{32}[.]json",
                            request.path.name,
                        )
                        self.assertIsNotNone(match)
                        assert match is not None
                        self.assertEqual(match.group(1), digest)
                        name = request.path.name
                    self.assertFalse((directory / name).exists())
            finally:
                os.close(held_fd)

    def test_candidate_cleanup_stays_on_original_directory_inode(self) -> None:
        state = {
            "schemaVersion": 1,
            "profiles": [],
            "lastApply": None,
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "candidates"
            replacement = root / "replacement"
            directory.mkdir(mode=0o700)
            replacement.mkdir(mode=0o700)
            held_fd = open_directory_path(directory)
            try:
                with mock.patch.object(
                    cli,
                    "_runtime_candidate_directory",
                    side_effect=lambda: (os.dup(held_fd), directory),
                ):
                    with cli.stage_candidate(state, "b" * 64) as request:
                        original = root / "candidates-original"
                        directory.rename(original)
                        replacement.rename(directory)
                        original_name = request.path.name
                        self.assertTrue((original / original_name).exists())
                    self.assertFalse((original / original_name).exists())
                    self.assertEqual(list(directory.iterdir()), [])
            finally:
                os.close(held_fd)


if __name__ == "__main__":
    unittest.main()
