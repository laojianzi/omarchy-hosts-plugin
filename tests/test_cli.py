from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "omarchy-hosts"


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *args: str, input_text: str | None = None, config_home: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(config_home or Path.home()), "XDG_CONFIG_HOME": str(config_home or Path.home() / ".config")}
        return subprocess.run(
            [sys.executable, "-I", "-B", str(CLI), *args],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "omarchy-hosts 1.0.0")

    def test_json_profile_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_home = Path(temp) / "config"
            payload = json.dumps(
                {
                    "name": "Development",
                    "enabled": True,
                    "entriesText": "127.0.0.1 app.test\n",
                }
            ) + "\n"
            saved = self.run_cli("--json", "profile-save", "-", input_text=payload, config_home=config_home)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            envelope = json.loads(saved.stdout)
            self.assertTrue(envelope["ok"])
            listed = self.run_cli("--json", "list", config_home=config_home)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            data = json.loads(listed.stdout)["data"]
            self.assertEqual(data[0]["name"], "Development")

    def test_invalid_bool_returns_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_cli("--json", "profile-toggle", "missing", "yes", config_home=Path(temp))
            self.assertEqual(result.returncode, 2)
            envelope = json.loads(result.stdout)
            self.assertFalse(envelope["ok"])
            self.assertEqual(envelope["error"]["code"], "invalid_boolean")


if __name__ == "__main__":
    unittest.main()
