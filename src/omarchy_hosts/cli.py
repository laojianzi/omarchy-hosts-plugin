"""Command-line backend used by the native Omarchy panel and terminal users."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from . import __version__
from .engine import (
    MAX_HOSTS_BYTES,
    HostsError,
    build_plan,
    is_sha256,
    normalize_profiles,
    profiles_config_sha256,
)
from .store import MAX_STATE_BYTES, StateStore, utc_now

HOSTS_PATH = Path("/etc/hosts")
HELPER_PATH = Path("/usr/lib/omarchy-hosts/omarchy-hosts-helper")
HELPER_IMPLEMENTATION_PATH = Path("/usr/lib/omarchy-hosts/helper.py")
HELPER_ENGINE_PATH = Path("/usr/lib/omarchy-hosts/engine.py")
POLICY_PATH = Path("/usr/share/polkit-1/actions/io.omarchy.hosts.policy")
ROOT_STATE_PATH = Path("/var/lib/omarchy-hosts/state.json")
PKEXEC_PATH = Path("/usr/bin/pkexec")
MAX_ROOT_STATE_BYTES = 128 * 1024


class CliError(HostsError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def emit_json(ok: bool, *, data: Any = None, error: Mapping[str, Any] | None = None) -> None:
    envelope: dict[str, Any] = {"ok": ok}
    if ok:
        envelope["data"] = data
    else:
        envelope["error"] = dict(error or {"code": "unknown", "message": "Unknown error", "details": {}})
    print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=_json_default))


def read_hosts(path: Path = HOSTS_PATH) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise CliError("hosts_read_failed", f"Cannot inspect {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise CliError("hosts_not_regular", f"{path} must be a single-link regular file and not a symlink")
    if before.st_size > MAX_HOSTS_BYTES:
        raise CliError("hosts_too_large", f"{path} exceeds the safety size limit")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CliError("hosts_read_failed", f"Cannot open {path}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise CliError("hosts_race", f"{path} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(131072, MAX_HOSTS_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_HOSTS_BYTES:
                raise CliError("hosts_too_large", f"{path} exceeds the safety size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_root_state() -> dict[str, Any] | None:
    try:
        info = ROOT_STATE_PATH.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            return None
        if info.st_size > MAX_ROOT_STATE_BYTES:
            return None
        raw = json.loads(ROOT_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) and raw.get("schemaVersion") == 1 else None


def _secure_root_file(path: Path, *, executable: bool = False) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
        return False
    if stat.S_IMODE(info.st_mode) & 0o022:
        return False
    return not executable or os.access(path, os.X_OK)


def helper_status() -> dict[str, Any]:
    helper_installed = _secure_root_file(HELPER_PATH, executable=True)
    implementation_installed = _secure_root_file(HELPER_IMPLEMENTATION_PATH)
    engine_installed = _secure_root_file(HELPER_ENGINE_PATH)
    policy_installed = _secure_root_file(POLICY_PATH)
    pkexec_installed = _secure_root_file(PKEXEC_PATH, executable=True)
    return {
        "installed": helper_installed,
        "path": str(HELPER_PATH),
        "implementationInstalled": implementation_installed,
        "engineInstalled": engine_installed,
        "policyInstalled": policy_installed,
        "pkexecInstalled": pkexec_installed,
        "ready": (
            helper_installed
            and implementation_installed
            and engine_installed
            and policy_installed
            and pkexec_installed
        ),
    }


def _status_kind(plan: Any, state: Mapping[str, Any]) -> str:
    if not plan.changed:
        if plan.enabled_profile_count == 0 and not plan.managed_present:
            return "idle"
        return "synced"
    last_apply = state.get("lastApply")
    config_sha = profiles_config_sha256(state["profiles"])
    if isinstance(last_apply, Mapping) and last_apply.get("configSha256") == config_sha:
        previous_managed = str(last_apply.get("managedSha256") or "")
        if previous_managed and plan.actual_managed_sha256 != previous_managed:
            return "drift"
    return "pending"


def build_view_state(store: StateStore, *, include_diff: bool = True) -> dict[str, Any]:
    state = store.load()
    profiles = store.profiles_for_ui(state)
    helper = helper_status()
    plan_data: dict[str, Any] | None = None
    planning_error: dict[str, Any] | None = None
    status_kind = "error"
    current: bytes | None = None
    config_sha = profiles_config_sha256(state["profiles"])
    try:
        current = read_hosts()
        plan = build_plan(current, state["profiles"])
        plan_data = plan.to_dict(include_diff=include_diff)
        plan_data["configSha256"] = config_sha
        status_kind = _status_kind(plan, state)
    except HostsError as exc:
        planning_error = exc.to_dict()

    labels = {
        "idle": "No managed hosts",
        "synced": "Applied and in sync",
        "pending": "Changes waiting to be applied",
        "drift": "Managed block changed outside Omarchy Hosts",
        "error": "Action required",
    }

    can_undo = False
    undo_after_sha256 = ""
    if current is not None:
        root_state = read_root_state() or {}
        transaction = root_state.get("lastTransaction")
        if isinstance(transaction, Mapping) and transaction.get("action") == "apply":
            can_undo = (
                transaction.get("callerUid") == os.getuid()
                and is_sha256(transaction.get("afterSha256"))
                and transaction.get("afterSha256") == hashlib.sha256(current).hexdigest()
            )
            if can_undo:
                undo_after_sha256 = str(transaction["afterSha256"])

    enabled_count = sum(1 for profile in profiles if profile["enabled"])
    total_entries = sum(profile["entryCount"] for profile in profiles)
    pending_count = 0 if plan_data is None or not plan_data["changed"] else 1
    return {
        "version": __version__,
        "profiles": profiles,
        "configPath": str(store.path),
        "hostsPath": str(HOSTS_PATH),
        "helper": helper,
        "status": {
            "kind": status_kind,
            "label": labels[status_kind],
            "error": planning_error,
            "canApply": bool(plan_data and plan_data["changed"] and helper["ready"]),
            "canUndo": can_undo and helper["ready"],
            "undoAfterSha256": undo_after_sha256,
            "pendingCount": pending_count,
        },
        "summary": {
            "profileCount": len(profiles),
            "enabledProfileCount": enabled_count,
            "configuredEntryCount": total_entries,
        },
        "plan": plan_data,
        "lastApply": state.get("lastApply"),
    }


def _runtime_candidate_directory() -> Path:
    uid = os.getuid()
    runtime_raw = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    runtime = Path(runtime_raw)
    expected = Path(f"/run/user/{uid}")
    try:
        if runtime.resolve(strict=True) != expected.resolve(strict=True):
            raise CliError(
                "runtime_dir_unsafe",
                f"XDG_RUNTIME_DIR must resolve to {expected} for privileged apply",
                {"runtimeDir": str(runtime)},
            )
        info = runtime.lstat()
    except OSError as exc:
        raise CliError("runtime_dir_missing", f"Cannot use runtime directory {runtime}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise CliError("runtime_dir_unsafe", f"Runtime directory has unsafe ownership or type: {runtime}")

    candidate_root = runtime / "omarchy-hosts"
    candidate_dir = candidate_root / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in (candidate_root, candidate_dir):
        directory_info = directory.lstat()
        if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != uid:
            raise CliError("runtime_dir_unsafe", f"Unsafe candidate directory: {directory}")
        os.chmod(directory, 0o700)
    return candidate_dir


def stage_candidate(state: Mapping[str, Any], base_sha256: str) -> Path:
    normalized = normalize_profiles(state["profiles"])
    profiles = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "description": "",
            "enabled": True,
            "entries": [
                {"address": entry["address"], "names": list(entry["names"])}
                for entry in profile["entries"]
            ],
        }
        for profile in normalized
        if profile["enabled"]
    ]
    payload = {
        "schemaVersion": 1,
        "requestUid": os.getuid(),
        "baseSha256": base_sha256,
        "configSha256": profiles_config_sha256(state["profiles"]),
        "createdAt": utc_now(),
        "profiles": profiles,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_HOSTS_BYTES:
        raise CliError("candidate_too_large", "Enabled profiles exceed the privileged candidate size limit")
    directory = _runtime_candidate_directory()
    fd, raw_path = tempfile.mkstemp(prefix="request-", suffix=".json", dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return path
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _decode_helper_output(stdout: str, stderr: str) -> dict[str, Any]:
    candidates = [line.strip() for line in stdout.splitlines() if line.strip().startswith("{")]
    for line in reversed(candidates):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    message = stderr.strip() or stdout.strip() or "Privileged helper returned no structured response"
    raise CliError("helper_protocol", message)


def run_helper(arguments: Sequence[str]) -> dict[str, Any]:
    helper = helper_status()
    if not helper["ready"]:
        raise CliError(
            "helper_missing",
            "The root-owned helper is not installed; run 'makepkg -si' in packaging/arch first",
            helper,
        )
    command = [str(PKEXEC_PATH), str(HELPER_PATH), *arguments]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise CliError("helper_launch_failed", f"Cannot launch pkexec: {exc}") from exc
    if result.returncode == 126:
        raise CliError("authorization_cancelled", "Authentication was cancelled")
    if result.returncode == 127 and not result.stdout.strip():
        raise CliError("authorization_failed", result.stderr.strip() or "Authorization failed")
    envelope = _decode_helper_output(result.stdout, result.stderr)
    if not envelope.get("ok"):
        raw_error = envelope.get("error")
        if isinstance(raw_error, Mapping):
            raise CliError(
                str(raw_error.get("code") or "helper_failed"),
                str(raw_error.get("message") or "Privileged helper failed"),
                raw_error.get("details") if isinstance(raw_error.get("details"), Mapping) else {},
            )
        raise CliError("helper_failed", "Privileged helper failed")
    if result.returncode != 0:
        raise CliError("helper_failed", result.stderr.strip() or f"Helper exited with {result.returncode}")
    data = envelope.get("data")
    return dict(data) if isinstance(data, Mapping) else {"result": data}


def apply_profiles(
    store: StateStore,
    *,
    expected_base_sha256: str | None = None,
    expected_config_sha256: str | None = None,
) -> dict[str, Any]:
    state = store.load()
    current = read_hosts()
    config_sha = profiles_config_sha256(state["profiles"])
    current_sha = hashlib.sha256(current).hexdigest()
    if expected_base_sha256 is not None:
        if not is_sha256(expected_base_sha256):
            raise CliError("invalid_hash", "Expected base hash is invalid")
        if expected_base_sha256 != current_sha:
            raise CliError(
                "preview_stale",
                "/etc/hosts changed after the reviewed preview; refresh and review again",
                {"expectedSha256": expected_base_sha256, "actualSha256": current_sha},
            )
    if expected_config_sha256 is not None:
        if not is_sha256(expected_config_sha256):
            raise CliError("invalid_hash", "Expected profile configuration hash is invalid")
        if expected_config_sha256 != config_sha:
            raise CliError(
                "preview_stale",
                "Hosts profiles changed after the reviewed preview; refresh and review again",
                {"expectedSha256": expected_config_sha256, "actualSha256": config_sha},
            )
    plan = build_plan(current, state["profiles"])
    if not plan.changed:
        return {"noOp": True, "message": "/etc/hosts is already in sync", **plan.to_dict(include_diff=False)}
    candidate = stage_candidate(state, plan.current_sha256)
    try:
        result = run_helper(["apply", str(candidate)])
    finally:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    metadata = {
        "appliedAt": str(result.get("appliedAt") or utc_now()),
        "beforeSha256": str(result.get("beforeSha256") or plan.current_sha256),
        "afterSha256": str(result.get("afterSha256") or plan.desired_sha256),
        "managedSha256": str(result.get("managedSha256") or plan.managed_sha256),
        "configSha256": str(result.get("configSha256") or config_sha),
        "backup": str(result.get("backup") or ""),
    }
    store.update_last_apply(metadata)
    return result


def undo_last_apply(store: StateStore, *, expected_after_sha256: str | None = None) -> dict[str, Any]:
    arguments = ["undo"]
    if expected_after_sha256 is not None:
        if not is_sha256(expected_after_sha256):
            raise CliError("invalid_hash", "Expected undo transaction hash is invalid")
        arguments.append(expected_after_sha256)
    result = run_helper(arguments)
    store.update_last_apply(None)
    return result


def doctor(store: StateStore) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    add("Omarchy CLI", shutil.which("omarchy") is not None, shutil.which("omarchy") or "not found")
    add("Omarchy shell IPC", shutil.which("omarchy-shell") is not None, shutil.which("omarchy-shell") or "not found")
    helper = helper_status()
    add("Privileged helper", helper["installed"], helper["path"])
    add("Helper implementation", helper["implementationInstalled"], str(HELPER_IMPLEMENTATION_PATH))
    add("Helper policy engine", helper["engineInstalled"], str(HELPER_ENGINE_PATH))
    add("Polkit policy", helper["policyInstalled"], str(POLICY_PATH))
    add("pkexec", helper["pkexecInstalled"], str(PKEXEC_PATH))
    try:
        store.load()
    except HostsError as exc:
        add("State file", False, exc.message)
    else:
        add("State file", True, str(store.path))
    try:
        read_hosts()
    except HostsError as exc:
        add("/etc/hosts", False, exc.message)
    else:
        add("/etc/hosts", True, "readable regular file")
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def parse_bool_literal(value: str) -> bool:
    text = value.strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise CliError("invalid_boolean", "Expected literal 'true' or 'false'")


def read_profile_payload(argument: str) -> Mapping[str, Any]:
    if argument == "-":
        raw_bytes = sys.stdin.buffer.readline(MAX_STATE_BYTES + 1)
        if len(raw_bytes) > MAX_STATE_BYTES:
            raise CliError("payload_too_large", "Profile payload exceeds the state size limit")
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeError as exc:
            raise CliError("invalid_json", f"Profile payload is not valid UTF-8: {exc}") from exc
    else:
        raw = argument
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError("invalid_json", f"Profile payload is invalid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise CliError("invalid_payload", "Profile payload must be a JSON object")
    return payload


def _print_human(command: str, data: Any) -> None:
    if command in {"ui-state", "status"}:
        status = data["status"]
        summary = data["summary"]
        print(status["label"])
        print(f"Profiles: {summary['enabledProfileCount']} enabled / {summary['profileCount']} total")
        if status.get("error"):
            print(f"Error: {status['error']['message']}")
        return
    if command == "list":
        if not data:
            print("No profiles")
        for profile in data:
            marker = "●" if profile["enabled"] else "○"
            print(f"{marker} {profile['id']}: {profile['name']} ({profile['entryCount']} entries)")
        return
    if command == "diff":
        print(data.get("diff") or "No changes")
        return
    if command == "doctor":
        for check in data["checks"]:
            print(f"{'OK' if check['ok'] else 'FAIL':4}  {check['name']}: {check['detail']}")
        return
    if command in {"apply", "undo", "profile-save", "profile-toggle", "profile-delete"}:
        if isinstance(data, Mapping) and data.get("message"):
            print(data["message"])
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2, default=_json_default))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omarchy-hosts", description="Native hosts profile manager for Omarchy 4")
    parser.add_argument("--json", action="store_true", help="emit a stable JSON envelope")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ui-state", help="return the complete panel model")
    sub.add_parser("status", help="show synchronization status")
    sub.add_parser("list", help="list profiles")
    sub.add_parser("diff", help="preview the proposed /etc/hosts change")
    apply_parser = sub.add_parser("apply", help="authorize and atomically apply enabled profiles")
    apply_parser.add_argument("--expect-base-sha256")
    apply_parser.add_argument("--expect-config-sha256")
    undo_parser = sub.add_parser("undo", help="undo the last unchanged apply transaction")
    undo_parser.add_argument("--expect-after-sha256")
    sub.add_parser("doctor", help="check integration and dependencies")

    save = sub.add_parser("profile-save", help="create or update a profile from a JSON payload")
    save.add_argument("payload", nargs="?", default="-", help="JSON object or '-' to read one JSON line from stdin")
    toggle = sub.add_parser("profile-toggle", help="enable or disable a profile")
    toggle.add_argument("profile_id")
    toggle.add_argument("enabled")
    delete = sub.add_parser("profile-delete", help="delete a profile")
    delete.add_argument("profile_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = StateStore()
    try:
        if args.command == "ui-state":
            data = build_view_state(store, include_diff=True)
        elif args.command == "status":
            data = build_view_state(store, include_diff=False)
        elif args.command == "list":
            data = store.profiles_for_ui()
        elif args.command == "diff":
            state = store.load()
            data = build_plan(read_hosts(), state["profiles"]).to_dict(include_diff=True)
        elif args.command == "apply":
            data = apply_profiles(
                store,
                expected_base_sha256=args.expect_base_sha256,
                expected_config_sha256=args.expect_config_sha256,
            )
        elif args.command == "undo":
            data = undo_last_apply(store, expected_after_sha256=args.expect_after_sha256)
        elif args.command == "doctor":
            data = doctor(store)
        elif args.command == "profile-save":
            data = store.save_profile(read_profile_payload(args.payload))
        elif args.command == "profile-toggle":
            data = store.toggle_profile(args.profile_id, parse_bool_literal(args.enabled))
        elif args.command == "profile-delete":
            data = store.delete_profile(args.profile_id)
        else:
            parser.error("unknown command")
            return 2
    except HostsError as exc:
        if args.json:
            emit_json(False, error=exc.to_dict())
        else:
            print(f"omarchy-hosts: {exc.message}", file=sys.stderr)
            if exc.details:
                print(json.dumps(exc.details, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        error = {"code": "interrupted", "message": "Operation interrupted", "details": {}}
        if args.json:
            emit_json(False, error=error)
        else:
            print("omarchy-hosts: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # Last-resort envelope; traceback only with explicit debug env.
        error = {"code": "internal_error", "message": str(exc) or type(exc).__name__, "details": {}}
        if os.environ.get("OMARCHY_HOSTS_DEBUG") == "1":
            raise
        if args.json:
            emit_json(False, error=error)
        else:
            print(f"omarchy-hosts: {error['message']}", file=sys.stderr)
        return 1

    if args.json:
        emit_json(True, data=data)
    else:
        _print_human(args.command, data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
