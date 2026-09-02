"""Command-line backend used by the native Omarchy panel and terminal users."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import threading
from typing import Any, Iterator, Mapping, Sequence

from . import __version__
from .engine import (
    MAX_HOSTS_BYTES,
    HostsError,
    build_plan,
    is_sha256,
    normalize_profiles,
    profiles_config_sha256,
)
from .process_control import ProcessControlError, run_bounded_process
from .securefs import (
    SecurePathError,
    create_private_file_at,
    open_directory_at,
    open_directory_path,
    open_regular_file_at,
    read_fd_bounded,
    read_regular_file_at,
    validate_directory_fd,
    write_all,
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
ROOT_STATE_OWNER_UID = 0
MAX_JSON_OUTPUT_BYTES = 8 * 1024 * 1024
HELPER_TIMEOUT_SECONDS = 180.0
HELPER_STDOUT_LIMIT = 256 * 1024
HELPER_STDERR_LIMIT = 128 * 1024


class CliError(HostsError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def emit_json(
    ok: bool,
    *,
    data: Any = None,
    error: Mapping[str, Any] | None = None,
) -> None:
    envelope: dict[str, Any] = {"ok": ok}
    if ok:
        envelope["data"] = data
    else:
        envelope["error"] = dict(
            error
            or {"code": "unknown", "message": "Unknown error", "details": {}}
        )
    encoded = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    if len(encoded) > MAX_JSON_OUTPUT_BYTES:
        encoded = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "response_too_large",
                    "message": "Backend response exceeded the panel safety limit",
                    "details": {"maximumBytes": MAX_JSON_OUTPUT_BYTES},
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    sys.stdout.buffer.flush()


def read_hosts(path: Path = HOSTS_PATH) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CliError("hosts_read_failed", f"Cannot open {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CliError(
                "hosts_not_regular",
                f"{path} must be a single-link regular file and not a symlink",
            )
        if info.st_size > MAX_HOSTS_BYTES:
            raise CliError("hosts_too_large", f"{path} exceeds the safety size limit")
        try:
            return read_fd_bounded(fd, MAX_HOSTS_BYTES)
        except SecurePathError as exc:
            raise CliError("hosts_too_large", f"{path} exceeds the safety size limit") from exc
    finally:
        os.close(fd)


def read_root_state() -> dict[str, Any] | None:
    directory_fd = -1
    try:
        directory_fd = open_directory_path(ROOT_STATE_PATH.parent, create=False)
        validate_directory_fd(
            directory_fd,
            owner_uid=ROOT_STATE_OWNER_UID,
            forbidden_mode_bits=0o022,
        )
        encoded, _ = read_regular_file_at(
            directory_fd,
            ROOT_STATE_PATH.name,
            maximum=MAX_ROOT_STATE_BYTES,
            owner_uid=ROOT_STATE_OWNER_UID,
            require_single_link=True,
            forbidden_mode_bits=0o022,
        )
        raw = json.loads(encoded.decode("utf-8"))
    except (
        FileNotFoundError,
        OSError,
        SecurePathError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        return None
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
    return raw if isinstance(raw, dict) and raw.get("schemaVersion") == 1 else None


def _secure_root_file(path: Path, *, executable: bool = False) -> bool:
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = open_directory_path(path.parent, create=False)
        validate_directory_fd(
            directory_fd,
            owner_uid=0,
            forbidden_mode_bits=0o022,
        )
        file_fd, info = open_regular_file_at(
            directory_fd,
            path.name,
            owner_uid=0,
            require_single_link=True,
            forbidden_mode_bits=0o022,
        )
        return not executable or bool(stat.S_IMODE(info.st_mode) & 0o111)
    except (FileNotFoundError, OSError, SecurePathError):
        return False
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


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


def build_view_state(
    store: StateStore,
    *,
    include_diff: bool = True,
) -> dict[str, Any]:
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
                and transaction.get("afterSha256")
                == hashlib.sha256(current).hexdigest()
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
            "canApply": bool(
                plan_data and plan_data["changed"] and helper["ready"]
            ),
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


def _runtime_candidate_directory() -> tuple[int, Path]:
    uid = os.getuid()
    expected = Path(f"/run/user/{uid}")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or expected)
    if runtime != expected:
        raise CliError(
            "runtime_dir_unsafe",
            f"XDG_RUNTIME_DIR must be exactly {expected} for privileged apply",
            {"runtimeDir": str(runtime)},
        )

    runtime_fd = root_fd = candidate_fd = -1
    try:
        runtime_fd = open_directory_path(runtime, create=False)
        validate_directory_fd(
            runtime_fd,
            owner_uid=uid,
            forbidden_mode_bits=0o077,
        )
        root_fd = open_directory_at(
            runtime_fd,
            "omarchy-hosts",
            create=True,
            mode=0o700,
        )
        validate_directory_fd(
            root_fd,
            owner_uid=uid,
            forbidden_mode_bits=0o077,
            force_mode=0o700,
        )
        candidate_fd = open_directory_at(
            root_fd,
            "candidates",
            create=True,
            mode=0o700,
        )
        validate_directory_fd(
            candidate_fd,
            owner_uid=uid,
            forbidden_mode_bits=0o077,
            force_mode=0o700,
        )
        result = candidate_fd
        candidate_fd = -1
        return result, expected / "omarchy-hosts" / "candidates"
    except SecurePathError as exc:
        raise CliError(
            "runtime_dir_unsafe",
            f"Unsafe candidate directory: {exc}",
        ) from exc
    except OSError as exc:
        raise CliError(
            "runtime_dir_missing",
            f"Cannot open candidate directory: {exc}",
        ) from exc
    finally:
        for fd in (candidate_fd, root_fd, runtime_fd):
            if fd >= 0:
                os.close(fd)


@dataclass(frozen=True)
class CandidateRequest:
    path: Path
    sha256: str


@contextmanager
def stage_candidate(
    state: Mapping[str, Any],
    base_sha256: str,
) -> Iterator[CandidateRequest]:
    normalized = normalize_profiles(state["profiles"])
    profiles = [
        {
            "id": profile["id"],
            "name": profile["name"],
            "description": "",
            "enabled": True,
            "entries": [
                {
                    "address": entry["address"],
                    "names": list(entry["names"]),
                }
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
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_HOSTS_BYTES:
        raise CliError(
            "candidate_too_large",
            "Enabled profiles exceed the privileged candidate size limit",
        )

    digest = hashlib.sha256(encoded).hexdigest()
    directory_fd, directory_path = _runtime_candidate_directory()
    name = ""
    file_fd = -1
    try:
        file_fd, name = create_private_file_at(
            directory_fd,
            prefix=f"request-{digest}-",
            suffix=".json",
            mode=0o600,
        )
        try:
            write_all(file_fd, encoded)
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
            file_fd = -1
        os.fsync(directory_fd)
        yield CandidateRequest(path=directory_path / name, sha256=digest)
    except (OSError, SecurePathError) as exc:
        raise CliError(
            "candidate_stage_failed",
            f"Cannot stage privileged candidate: {exc}",
        ) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if name:
            try:
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        os.close(directory_fd)


def _decode_helper_output(stdout: str, stderr: str) -> dict[str, Any]:
    candidates = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip().startswith("{")
    ]
    for line in reversed(candidates):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    message = (
        stderr.strip()
        or stdout.strip()
        or "Privileged helper returned no structured response"
    )
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
        result = run_bounded_process(
            command,
            timeout=HELPER_TIMEOUT_SECONDS,
            stdout_limit=HELPER_STDOUT_LIMIT,
            stderr_limit=HELPER_STDERR_LIMIT,
        )
    except ProcessControlError as exc:
        if exc.reason == "timeout":
            raise CliError(
                "helper_timeout",
                "Administrator authorization or the privileged helper timed out",
                {"reason": exc.reason},
            ) from exc
        raise CliError(
            "helper_output_limit",
            "Privileged helper output exceeded the safety limit",
            {"reason": exc.reason},
        ) from exc
    except OSError as exc:
        raise CliError(
            "helper_launch_failed",
            f"Cannot launch pkexec: {exc}",
        ) from exc

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode == 126:
        raise CliError("authorization_cancelled", "Authentication was cancelled")
    if result.returncode == 127 and not stdout.strip():
        raise CliError(
            "authorization_failed",
            stderr.strip() or "Authorization failed",
        )
    envelope = _decode_helper_output(stdout, stderr)
    if not envelope.get("ok"):
        raw_error = envelope.get("error")
        if isinstance(raw_error, Mapping):
            raise CliError(
                str(raw_error.get("code") or "helper_failed"),
                str(raw_error.get("message") or "Privileged helper failed"),
                raw_error.get("details")
                if isinstance(raw_error.get("details"), Mapping)
                else {},
            )
        raise CliError("helper_failed", "Privileged helper failed")
    if result.returncode != 0:
        raise CliError(
            "helper_failed",
            stderr.strip() or f"Helper exited with {result.returncode}",
        )
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
                {
                    "expectedSha256": expected_base_sha256,
                    "actualSha256": current_sha,
                },
            )
    if expected_config_sha256 is not None:
        if not is_sha256(expected_config_sha256):
            raise CliError(
                "invalid_hash",
                "Expected profile configuration hash is invalid",
            )
        if expected_config_sha256 != config_sha:
            raise CliError(
                "preview_stale",
                "Hosts profiles changed after the reviewed preview; refresh and review again",
                {
                    "expectedSha256": expected_config_sha256,
                    "actualSha256": config_sha,
                },
            )
    plan = build_plan(current, state["profiles"])
    if not plan.changed:
        return {
            "noOp": True,
            "message": "/etc/hosts is already in sync",
            **plan.to_dict(include_diff=False),
        }
    with stage_candidate(state, plan.current_sha256) as candidate:
        result = run_helper(["apply", str(candidate.path)])
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


def undo_last_apply(
    store: StateStore,
    *,
    expected_after_sha256: str | None = None,
) -> dict[str, Any]:
    arguments = ["undo"]
    if expected_after_sha256 is not None:
        if not is_sha256(expected_after_sha256):
            raise CliError(
                "invalid_hash",
                "Expected undo transaction hash is invalid",
            )
        arguments.append(expected_after_sha256)
    result = run_helper(arguments)
    store.update_last_apply(None)
    return result


def doctor(store: StateStore) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    add(
        "Omarchy CLI",
        shutil.which("omarchy") is not None,
        shutil.which("omarchy") or "not found",
    )
    add(
        "Omarchy shell IPC",
        shutil.which("omarchy-shell") is not None,
        shutil.which("omarchy-shell") or "not found",
    )
    helper = helper_status()
    add("Privileged helper", helper["installed"], helper["path"])
    add(
        "Helper implementation",
        helper["implementationInstalled"],
        str(HELPER_IMPLEMENTATION_PATH),
    )
    add(
        "Helper policy engine",
        helper["engineInstalled"],
        str(HELPER_ENGINE_PATH),
    )
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
            raise CliError(
                "payload_too_large",
                "Profile payload exceeds the state size limit",
            )
        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeError as exc:
            raise CliError(
                "invalid_json",
                f"Profile payload is not valid UTF-8: {exc}",
            ) from exc
    else:
        raw = argument
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliError(
            "invalid_json",
            f"Profile payload is invalid JSON: {exc}",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CliError("invalid_payload", "Profile payload must be a JSON object")
    return payload


def _print_human(command: str, data: Any) -> None:
    if command in {"ui-state", "status"}:
        status = data["status"]
        summary = data["summary"]
        print(status["label"])
        print(
            f"Profiles: {summary['enabledProfileCount']} enabled / "
            f"{summary['profileCount']} total"
        )
        if status.get("error"):
            print(f"Error: {status['error']['message']}")
        return
    if command == "list":
        if not data:
            print("No profiles")
        for profile in data:
            marker = "●" if profile["enabled"] else "○"
            print(
                f"{marker} {profile['id']}: {profile['name']} "
                f"({profile['entryCount']} entries)"
            )
        return
    if command == "diff":
        print(data.get("diff") or "No changes")
        return
    if command == "doctor":
        for check in data["checks"]:
            print(
                f"{'OK' if check['ok'] else 'FAIL':4}  "
                f"{check['name']}: {check['detail']}"
            )
        return
    if command in {
        "apply",
        "undo",
        "profile-save",
        "profile-toggle",
        "profile-delete",
    }:
        if isinstance(data, Mapping) and data.get("message"):
            print(data["message"])
        else:
            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                    default=_json_default,
                )
            )
        return
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omarchy-hosts",
        description="Native hosts profile manager for Omarchy 4",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a stable JSON envelope",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ui-state", help="return the complete panel model")
    sub.add_parser("status", help="show synchronization status")
    sub.add_parser("list", help="list profiles")
    sub.add_parser("diff", help="preview the proposed /etc/hosts change")
    apply_parser = sub.add_parser(
        "apply",
        help="authorize and atomically apply enabled profiles",
    )
    apply_parser.add_argument("--expect-base-sha256")
    apply_parser.add_argument("--expect-config-sha256")
    undo_parser = sub.add_parser(
        "undo",
        help="undo the last unchanged apply transaction",
    )
    undo_parser.add_argument("--expect-after-sha256")
    sub.add_parser("doctor", help="check integration and dependencies")

    save = sub.add_parser(
        "profile-save",
        help="create or update a profile from a JSON payload",
    )
    save.add_argument(
        "payload",
        nargs="?",
        default="-",
        help="JSON object or '-' to read one JSON line from stdin",
    )
    toggle = sub.add_parser(
        "profile-toggle",
        help="enable or disable a profile",
    )
    toggle.add_argument("profile_id")
    toggle.add_argument("enabled")
    delete = sub.add_parser("profile-delete", help="delete a profile")
    delete.add_argument("profile_id")
    return parser


def _termination_requested(signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt(f"received signal {signum}")


def _install_termination_handlers() -> None:
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, _termination_requested)


def main(argv: Sequence[str] | None = None) -> int:
    _install_termination_handlers()
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
            data = build_plan(
                read_hosts(),
                state["profiles"],
            ).to_dict(include_diff=True)
        elif args.command == "apply":
            data = apply_profiles(
                store,
                expected_base_sha256=args.expect_base_sha256,
                expected_config_sha256=args.expect_config_sha256,
            )
        elif args.command == "undo":
            data = undo_last_apply(
                store,
                expected_after_sha256=args.expect_after_sha256,
            )
        elif args.command == "doctor":
            data = doctor(store)
        elif args.command == "profile-save":
            data = store.save_profile(read_profile_payload(args.payload))
        elif args.command == "profile-toggle":
            data = store.toggle_profile(
                args.profile_id,
                parse_bool_literal(args.enabled),
            )
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
                print(
                    json.dumps(exc.details, ensure_ascii=False, indent=2),
                    file=sys.stderr,
                )
        return 2
    except KeyboardInterrupt:
        error = {
            "code": "interrupted",
            "message": "Operation interrupted",
            "details": {},
        }
        if args.json:
            emit_json(False, error=error)
        else:
            print("omarchy-hosts: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        error = {
            "code": "internal_error",
            "message": str(exc) or type(exc).__name__,
            "details": {},
        }
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
