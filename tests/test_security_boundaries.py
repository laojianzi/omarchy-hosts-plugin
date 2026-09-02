from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from omarchy_hosts import cli
from omarchy_hosts.process_control import ProcessControlError, run_bounded_process
from omarchy_hosts.securefs import (
    create_private_file_at,
    open_directory_path,
    write_all,
)
from omarchy_hosts.store import StateStore


class DescriptorRelativeStateTests(unittest.TestCase):
    def test_state_write_remains_bound_to_open_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config"
            victim = Path(raw) / "victim"
            victim.mkdir()
            store = StateStore(config)

            with store._state_directory_fd() as directory_fd:
                original = store.directory.with_name("hosts-original")
                store.directory.rename(original)
                store.directory.symlink_to(victim, target_is_directory=True)
                store._write_unlocked(store.default_state(), directory_fd)

            self.assertTrue((original / "state.json").is_file())
            self.assertFalse((victim / "state.json").exists())

    def test_private_temp_file_remains_bound_to_open_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            managed = root / "managed"
            victim = root / "victim"
            managed.mkdir(mode=0o700)
            victim.mkdir(mode=0o700)
            directory_fd = open_directory_path(managed)
            try:
                displaced = root / "managed-original"
                managed.rename(displaced)
                managed.symlink_to(victim, target_is_directory=True)
                fd, name = create_private_file_at(
                    directory_fd,
                    prefix="request-",
                    suffix=".json",
                    mode=0o600,
                )
                try:
                    write_all(fd, b"{}\n")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self.assertEqual((displaced / name).read_bytes(), b"{}\n")
                self.assertFalse((victim / name).exists())
                os.unlink(name, dir_fd=directory_fd)
            finally:
                os.close(directory_fd)


class RootStateReadTests(unittest.TestCase):
    def test_root_state_is_read_once_through_nofollow_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            path.write_text(
                '{"schemaVersion":1,"lastTransaction":null}\n',
                encoding="utf-8",
            )
            path.chmod(0o600)
            with (
                mock.patch.object(cli, "ROOT_STATE_PATH", path),
                mock.patch.object(cli, "ROOT_STATE_OWNER_UID", os.getuid()),
            ):
                state = cli.read_root_state()
            self.assertEqual(
                state,
                {"schemaVersion": 1, "lastTransaction": None},
            )

    def test_root_state_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target.json"
            target.write_text('{"schemaVersion":1}\n', encoding="utf-8")
            link = root / "state.json"
            link.symlink_to(target)
            with (
                mock.patch.object(cli, "ROOT_STATE_PATH", link),
                mock.patch.object(cli, "ROOT_STATE_OWNER_UID", os.getuid()),
            ):
                self.assertIsNone(cli.read_root_state())

    def test_root_state_size_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            path.write_bytes(b"{" + b"x" * (cli.MAX_ROOT_STATE_BYTES + 1))
            path.chmod(0o600)
            with (
                mock.patch.object(cli, "ROOT_STATE_PATH", path),
                mock.patch.object(cli, "ROOT_STATE_OWNER_UID", os.getuid()),
            ):
                self.assertIsNone(cli.read_root_state())


class ProcessBoundaryTests(unittest.TestCase):
    def test_timeout_terminates_process(self) -> None:
        with self.assertRaises(ProcessControlError) as raised:
            run_bounded_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout=0.2,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertEqual(raised.exception.reason, "timeout")

    def test_stdout_limit_terminates_process(self) -> None:
        with self.assertRaises(ProcessControlError) as raised:
            run_bounded_process(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'x' * 65536)",
                ],
                timeout=5,
                stdout_limit=1024,
                stderr_limit=1024,
            )
        self.assertEqual(raised.exception.reason, "output_limit")

    def test_timeout_terminates_descendant_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            pid_path = Path(raw) / "child.pid"
            source = (
                "import pathlib, subprocess, sys, time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); "
                "time.sleep(30)"
            )
            with self.assertRaises(ProcessControlError):
                run_bounded_process(
                    [sys.executable, "-c", source],
                    timeout=1.5,
                    stdout_limit=1024,
                    stderr_limit=1024,
                )
            child_pid = int(pid_path.read_text())
            deadline = time.monotonic() + 3
            running = True
            while time.monotonic() < deadline:
                try:
                    stat_text = Path(f"/proc/{child_pid}/stat").read_text()
                except FileNotFoundError:
                    running = False
                    break
                fields = stat_text.split()
                if len(fields) > 2 and fields[2] == "Z":
                    running = False
                    break
                time.sleep(0.05)
            self.assertFalse(
                running,
                f"descendant process {child_pid} survived group teardown",
            )

    def test_run_helper_maps_timeout_to_stable_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            helper = Path(raw) / "helper.py"
            helper.write_text(
                "import time; time.sleep(30)\n",
                encoding="utf-8",
            )
            helper.chmod(0o755)
            ready = {
                "ready": True,
                "installed": True,
                "implementationInstalled": True,
                "engineInstalled": True,
                "policyInstalled": True,
                "pkexecInstalled": True,
                "path": str(helper),
            }
            with (
                mock.patch.object(cli, "helper_status", return_value=ready),
                mock.patch.object(cli, "PKEXEC_PATH", Path(sys.executable)),
                mock.patch.object(cli, "HELPER_PATH", helper),
                mock.patch.object(cli, "HELPER_TIMEOUT_SECONDS", 0.2),
            ):
                with self.assertRaises(cli.CliError) as raised:
                    cli.run_helper([])
            self.assertEqual(raised.exception.code, "helper_timeout")


if __name__ == "__main__":
    unittest.main()
