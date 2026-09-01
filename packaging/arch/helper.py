#!/usr/bin/env python3
"""Root-owned transactional writer for Omarchy Hosts.

The command surface is intentionally tiny: apply one validated candidate from
the caller's XDG runtime directory, or undo the last unchanged transaction
created by that same caller. Installed execution uses Python isolated mode and
imports only the root-owned engine.py installed beside this file.
"""

from __future__ import annotations

import ctypes
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Mapping


def _load_policy_engine() -> Any:
    """Load the exact adjacent/root-owned engine even under Python ``-I``.

    Isolated mode intentionally removes the script directory from ``sys.path``.
    Importing by a verified file path keeps the helper independent of PATH,
    PYTHONPATH, the caller's working directory, and user site packages.
    """

    helper_path = Path(__file__).resolve(strict=True)
    candidates = (
        helper_path.parent / "engine.py",  # installed/package layout
        helper_path.parent.parent / "src" / "omarchy_hosts" / "engine.py",  # source tree/tests
    )
    expected_uid = os.geteuid()
    helper_info = helper_path.lstat()
    helper_parent_info = helper_path.parent.lstat()
    if (
        stat.S_ISLNK(helper_info.st_mode)
        or not stat.S_ISREG(helper_info.st_mode)
        or helper_info.st_uid != expected_uid
        or helper_info.st_nlink != 1
        or stat.S_IMODE(helper_info.st_mode) & 0o022
        or stat.S_ISLNK(helper_parent_info.st_mode)
        or not stat.S_ISDIR(helper_parent_info.st_mode)
        or helper_parent_info.st_uid != expected_uid
        or stat.S_IMODE(helper_parent_info.st_mode) & 0o022
    ):
        raise RuntimeError(f"Privileged helper path is not securely owned: {helper_path}")
    for candidate in candidates:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(f"Cannot inspect policy engine {candidate}: {exc}") from exc
        parent_info = candidate.parent.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != expected_uid
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
            or stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != expected_uid
            or stat.S_IMODE(parent_info.st_mode) & 0o022
        ):
            raise RuntimeError(f"Policy engine is not a secure single-link file: {candidate}")
        spec = importlib.util.spec_from_file_location("_omarchy_hosts_policy_engine", candidate)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load policy engine: {candidate}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    raise RuntimeError("The root-owned Omarchy Hosts policy engine is missing")


_engine = _load_policy_engine()
MAX_HOSTS_BYTES = _engine.MAX_HOSTS_BYTES
HostsError = _engine.HostsError
build_plan = _engine.build_plan
is_sha256 = _engine.is_sha256
normalize_profiles = _engine.normalize_profiles
profiles_config_sha256 = _engine.profiles_config_sha256
sha256_bytes = _engine.sha256_bytes

TARGET = Path("/etc/hosts")
LOCK_PATH = Path("/run/lock/omarchy-hosts.lock")
STATE_DIR = Path("/var/lib/omarchy-hosts")
BACKUP_DIR = STATE_DIR / "backups"
STATE_PATH = STATE_DIR / "state.json"
MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATE_AGE = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_BACKUPS = 20
BACKUP_RE = re.compile(r"^hosts-\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{12}$")
RENAME_EXCHANGE = 2


def _rename_exchange(source_dir_fd: int, source: str, destination_dir_fd: int, destination: str) -> None:
    """Atomically exchange two names in one filesystem directory.

    A normal rename can overwrite a version that appeared after our final
    preflight check. ``RENAME_EXCHANGE`` preserves both versions, allowing us
    to inspect the displaced inode and roll the swap back without losing a
    concurrent writer's bytes.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PrivilegedError(
            "atomic_exchange_unavailable",
            "This system does not expose renameat2(RENAME_EXCHANGE); refusing a non-atomic fallback",
        )
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_dir_fd,
        os.fsencode(source),
        destination_dir_fd,
        os.fsencode(destination),
        RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}:
            raise PrivilegedError(
                "atomic_exchange_unavailable",
                "The /etc filesystem does not support atomic rename exchange; refusing a weaker write path",
                {"errno": error_number},
            )
        raise OSError(error_number, os.strerror(error_number))


class PrivilegedError(HostsError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_for_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _emit(ok: bool, payload: Mapping[str, Any]) -> None:
    envelope = {"ok": ok, "data" if ok else "error": dict(payload)}
    print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")), flush=True)


def _audit(message: str) -> None:
    try:
        import syslog

        syslog.openlog("omarchy-hosts-helper")
        syslog.syslog(syslog.LOG_AUTHPRIV | syslog.LOG_NOTICE, message)
    except Exception:
        pass


def caller_uid_from_environment() -> int:
    raw = os.environ.get("PKEXEC_UID", "")
    if not raw.isascii() or not raw.isdigit():
        raise PrivilegedError(
            "invalid_invocation",
            "This helper must be invoked through pkexec; PKEXEC_UID is missing",
        )
    uid = int(raw, 10)
    if uid <= 0:
        raise PrivilegedError("invalid_invocation", "Root may not be used as the plugin caller")
    return uid


def _parse_candidate_created_at(value: Any, *, now: datetime | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise PrivilegedError("candidate_time", "Candidate creation time is missing")
    try:
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrivilegedError("candidate_time", "Candidate creation time is invalid") from exc
    if created.tzinfo is None:
        raise PrivilegedError("candidate_time", "Candidate creation time must include a timezone")
    created = created.astimezone(timezone.utc)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if created > reference + MAX_FUTURE_SKEW:
        raise PrivilegedError("candidate_time", "Candidate creation time is too far in the future")
    if reference - created > MAX_CANDIDATE_AGE:
        raise PrivilegedError("candidate_expired", "Candidate request expired; refresh the preview and apply again")
    return created.isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_candidate_payload(
    raw: Any,
    caller_uid: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PrivilegedError("candidate_invalid", "Candidate root must be an object")
    payload = dict(raw)
    if payload.get("schemaVersion") != 1:
        raise PrivilegedError("candidate_version", "Unsupported candidate schema")
    if payload.get("requestUid") != caller_uid:
        raise PrivilegedError("candidate_uid", "Candidate caller uid does not match PKEXEC_UID")
    if not is_sha256(payload.get("baseSha256")) or not is_sha256(payload.get("configSha256")):
        raise PrivilegedError("candidate_hash", "Candidate hashes are missing or invalid")
    payload["createdAt"] = _parse_candidate_created_at(payload.get("createdAt"), now=now)
    profiles = normalize_profiles(payload.get("profiles"))
    if any(profile["enabled"] is not True for profile in profiles):
        raise PrivilegedError("candidate_profiles", "A privileged candidate may contain enabled profiles only")
    computed_config_sha = profiles_config_sha256(profiles)
    if computed_config_sha != payload["configSha256"]:
        raise PrivilegedError(
            "candidate_hash",
            "Candidate profile hash does not match its contents",
            {"expectedSha256": payload["configSha256"], "actualSha256": computed_config_sha},
        )
    payload["profiles"] = profiles
    return payload


def _ensure_privileged_directory(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    info = path.lstat()
    expected_uid = os.geteuid()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid:
        raise PrivilegedError("unsafe_directory", f"Unsafe privileged directory: {path}")
    os.chmod(path, mode)


def _read_fd_all(fd: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(131072, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise PrivilegedError("file_too_large", f"Input exceeds the {maximum} byte safety limit")
        chunks.append(chunk)
    return b"".join(chunks)


def read_candidate(path_text: str, caller_uid: int) -> dict[str, Any]:
    supplied = Path(path_text)
    if not supplied.is_absolute():
        raise PrivilegedError("candidate_path", "Candidate path must be absolute")
    runtime = Path(f"/run/user/{caller_uid}")
    allowed = runtime / "omarchy-hosts" / "candidates"
    try:
        runtime_real = runtime.resolve(strict=True)
        allowed_real = allowed.resolve(strict=True)
        supplied_parent_real = supplied.parent.resolve(strict=True)
    except OSError as exc:
        raise PrivilegedError("candidate_path", f"Cannot resolve candidate path: {exc}") from exc
    if runtime_real != runtime or supplied_parent_real != allowed_real:
        raise PrivilegedError(
            "candidate_path",
            "Candidate must be a direct child of the caller's Omarchy Hosts runtime directory",
            {"allowedDirectory": str(allowed)},
        )
    for directory in (runtime, runtime / "omarchy-hosts", allowed):
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != caller_uid:
            raise PrivilegedError("candidate_path", f"Unsafe candidate directory: {directory}")
        if directory != runtime and stat.S_IMODE(info.st_mode) & 0o077:
            raise PrivilegedError("candidate_permissions", f"Candidate directory must be mode 0700: {directory}")

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(supplied, flags)
    except OSError as exc:
        raise PrivilegedError("candidate_open", f"Cannot open candidate: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != caller_uid or info.st_nlink != 1:
            raise PrivilegedError("candidate_unsafe", "Candidate must be a single-link regular file owned by the caller")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PrivilegedError("candidate_permissions", "Candidate must not be accessible by group or other users")
        if info.st_size > MAX_CANDIDATE_BYTES:
            raise PrivilegedError("candidate_too_large", "Candidate exceeds the safety size limit")
        encoded = _read_fd_all(fd, MAX_CANDIDATE_BYTES)
    finally:
        os.close(fd)
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivilegedError("candidate_invalid", f"Candidate is not valid UTF-8 JSON: {exc}") from exc
    return _validate_candidate_payload(payload, caller_uid)


def read_regular(
    path: Path,
    maximum: int = MAX_HOSTS_BYTES,
    *,
    expected_uid: int | None = None,
) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise PrivilegedError("file_read", f"Cannot inspect {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise PrivilegedError("file_unsafe", f"{path} must be a single-link regular file")
    if expected_uid is not None and before.st_uid != expected_uid:
        raise PrivilegedError("file_unsafe", f"{path} must be owned by uid {expected_uid}")
    if before.st_size > maximum:
        raise PrivilegedError("file_too_large", f"{path} exceeds the safety size limit")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise PrivilegedError("file_read", f"Cannot open {path}: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise PrivilegedError("file_race", f"{path} changed while being opened")
        data = _read_fd_all(fd, maximum)
    finally:
        os.close(fd)
    return data, opened


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise PrivilegedError("write_failed", "Short write")
        view = view[written:]


def _copy_xattrs(source: Path, destination_fd: int) -> None:
    try:
        names = os.listxattr(source, follow_symlinks=False)
    except (AttributeError, OSError):
        return
    for name in names:
        try:
            value = os.getxattr(source, name, follow_symlinks=False)
            os.setxattr(destination_fd, name, value)
        except OSError as exc:
            if exc.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM, errno.EACCES}:
                raise


def atomic_replace(path: Path, data: bytes, expected_sha256: str, original: os.stat_result) -> None:
    parent = path.parent
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        dir_flags |= os.O_NOFOLLOW
    dir_fd = os.open(parent, dir_flags)
    temp_name = f".{path.name}.omarchy-hosts-{secrets.token_hex(8)}.tmp"
    temp_fd = -1
    exchanged = False
    temp_removed = False
    preserve_recovery = False
    proposed_sha256 = sha256_bytes(data)
    proposed_stat: os.stat_result | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temp_fd = os.open(temp_name, flags, stat.S_IMODE(original.st_mode), dir_fd=dir_fd)
        os.fchmod(temp_fd, stat.S_IMODE(original.st_mode))
        os.fchown(temp_fd, original.st_uid, original.st_gid)
        _write_all(temp_fd, data)
        _copy_xattrs(path, temp_fd)
        os.fsync(temp_fd)
        proposed_stat = os.fstat(temp_fd)
        os.close(temp_fd)
        temp_fd = -1

        latest, latest_stat = read_regular(path, expected_uid=original.st_uid)
        if sha256_bytes(latest) != expected_sha256 or (latest_stat.st_dev, latest_stat.st_ino) != (
            original.st_dev,
            original.st_ino,
        ):
            raise PrivilegedError(
                "hosts_stale",
                "/etc/hosts changed during the transaction; refresh the preview and apply again",
            )
        _rename_exchange(dir_fd, temp_name, dir_fd, path.name)
        exchanged = True
        os.fsync(dir_fd)

        # The name that used to be /etc/hosts is now at temp_name. This is the
        # atomic compare-and-swap check: if it is not the inode/hash we reviewed,
        # another writer won the race immediately before the exchange.
        displaced_path = parent / temp_name
        displaced, displaced_stat = read_regular(displaced_path, expected_uid=original.st_uid)
        displaced_matches = (
            sha256_bytes(displaced) == expected_sha256
            and (displaced_stat.st_dev, displaced_stat.st_ino) == (original.st_dev, original.st_ino)
        )
        if not displaced_matches:
            try:
                installed, installed_stat = read_regular(path, expected_uid=original.st_uid)
                installed_is_ours = (
                    proposed_stat is not None
                    and sha256_bytes(installed) == proposed_sha256
                    and (installed_stat.st_dev, installed_stat.st_ino)
                    == (proposed_stat.st_dev, proposed_stat.st_ino)
                )
            except HostsError:
                installed_is_ours = False
            if installed_is_ours:
                _rename_exchange(dir_fd, temp_name, dir_fd, path.name)
                exchanged = False
                os.fsync(dir_fd)
                os.unlink(temp_name, dir_fd=dir_fd)
                temp_removed = True
                os.fsync(dir_fd)
                raise PrivilegedError(
                    "hosts_stale",
                    "/etc/hosts changed at the commit boundary; the concurrent version was restored",
                    {
                        "expectedSha256": expected_sha256,
                        "actualSha256": sha256_bytes(displaced),
                    },
                )
            preserve_recovery = True
            raise PrivilegedError(
                "transaction_race",
                "Multiple writers changed /etc/hosts at the commit boundary; a displaced version was retained for manual recovery",
                {"recoveryPath": str(displaced_path)},
            )

        # Also verify the destination still names our prepared inode. A writer
        # that changed it immediately after the exchange is newer and must not
        # be overwritten by an automatic rollback.
        try:
            installed, installed_stat = read_regular(path, expected_uid=original.st_uid)
        except HostsError as exc:
            preserve_recovery = True
            raise PrivilegedError(
                "hosts_changed_after_commit",
                "/etc/hosts changed immediately after the atomic exchange; the pre-commit file was retained",
                {"recoveryPath": str(displaced_path), "cause": exc.message},
            ) from exc
        if (
            proposed_stat is None
            or sha256_bytes(installed) != proposed_sha256
            or (installed_stat.st_dev, installed_stat.st_ino)
            != (proposed_stat.st_dev, proposed_stat.st_ino)
        ):
            preserve_recovery = True
            raise PrivilegedError(
                "hosts_changed_after_commit",
                "/etc/hosts changed immediately after the atomic exchange; the pre-commit file was retained",
                {"recoveryPath": str(displaced_path)},
            )

        os.unlink(temp_name, dir_fd=dir_fd)
        temp_removed = True
        os.fsync(dir_fd)
    except Exception:
        try:
            if temp_fd >= 0:
                os.close(temp_fd)
        except OSError:
            pass
        # Before an exchange, temp_name is only our proposed file and is safe
        # to remove. After an exchange it may be the sole copy of a concurrent
        # writer's version, so preserve it unless a verified rollback restored
        # that version to the target.
        if not temp_removed and not exchanged:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(dir_fd)


def create_backup(content: bytes) -> str:
    _ensure_privileged_directory(STATE_DIR, 0o755)
    _ensure_privileged_directory(BACKUP_DIR, 0o700)
    name = f"hosts-{_timestamp_for_filename()}-{sha256_bytes(content)[:12]}"
    dir_fd = os.open(BACKUP_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return name


def _atomic_state_write(payload: Mapping[str, Any]) -> None:
    _ensure_privileged_directory(STATE_DIR, 0o755)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    dir_fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    temp_name = f".state-{secrets.token_hex(8)}.tmp"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temp_name, flags, 0o644, dir_fd=dir_fd)
        try:
            os.fchmod(fd, 0o644)
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temp_name, STATE_PATH.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)


def read_privileged_state() -> dict[str, Any]:
    try:
        STATE_PATH.lstat()
    except FileNotFoundError:
        return {"schemaVersion": 1, "lastTransaction": None}
    except OSError as exc:
        raise PrivilegedError("state_invalid", f"Cannot inspect privileged state: {exc}") from exc
    data, info = read_regular(STATE_PATH, 128 * 1024, expected_uid=os.geteuid())
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise PrivilegedError("state_unsafe", "Privileged state must not be writable by group or other users")
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PrivilegedError("state_invalid", f"Privileged state is invalid: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise PrivilegedError("state_invalid", "Privileged state has an unsupported schema")
    return raw


def read_backup(name: str) -> bytes:
    if not BACKUP_RE.fullmatch(name):
        raise PrivilegedError("backup_invalid", "Backup name is invalid")
    data, info = read_regular(BACKUP_DIR / name, MAX_HOSTS_BYTES, expected_uid=os.geteuid())
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PrivilegedError("backup_unsafe", "Backup permissions are unsafe")
    return data


def rotate_backups(keep_name: str | None = None) -> None:
    try:
        entries: list[str] = []
        for entry in BACKUP_DIR.iterdir():
            try:
                info = entry.lstat()
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and BACKUP_RE.fullmatch(entry.name):
                entries.append(entry.name)
        entries.sort()
    except OSError:
        return
    protected = {keep_name} if keep_name else set()
    removable = [name for name in entries if name not in protected]
    excess = max(0, len(entries) - MAX_BACKUPS)
    for name in removable[:excess]:
        try:
            (BACKUP_DIR / name).unlink()
        except OSError:
            pass


def _locked() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(LOCK_PATH, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise PrivilegedError("lock_unsafe", f"Unsafe transaction lock: {LOCK_PATH}")
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        os.close(fd)
        raise


def _rollback_after_metadata_failure(
    restore_content: bytes,
    expected_current_sha256: str,
    *,
    metadata_error: Exception,
    recovery_backup: str,
) -> None:
    """Restore the pre-transaction bytes when durable metadata cannot commit."""

    try:
        latest, latest_stat = read_regular(TARGET, expected_uid=os.geteuid())
        actual_sha = sha256_bytes(latest)
        if actual_sha != expected_current_sha256:
            raise PrivilegedError(
                "rollback_stale",
                "The target changed before transaction metadata could be rolled back",
                {"expectedSha256": expected_current_sha256, "actualSha256": actual_sha},
            )
        atomic_replace(TARGET, restore_content, expected_current_sha256, latest_stat)
    except Exception as rollback_error:
        raise PrivilegedError(
            "transaction_state_failed",
            "The hosts file changed but transaction metadata could not be committed or rolled back; restore the recorded backup manually",
            {
                "recoveryBackup": recovery_backup,
                "metadataError": str(metadata_error),
                "rollbackError": str(rollback_error),
            },
        ) from metadata_error
    raise PrivilegedError(
        "transaction_rolled_back",
        "Transaction metadata could not be committed, so /etc/hosts was restored unchanged",
        {"recoveryBackup": recovery_backup, "metadataError": str(metadata_error)},
    ) from metadata_error


def apply(candidate_path: str, caller_uid: int) -> dict[str, Any]:
    candidate = read_candidate(candidate_path, caller_uid)
    lock_fd = _locked()
    try:
        current, current_stat = read_regular(TARGET, expected_uid=os.geteuid())
        current_sha = sha256_bytes(current)
        if current_sha != candidate["baseSha256"]:
            raise PrivilegedError(
                "hosts_stale",
                "/etc/hosts changed after the preview; refresh and review the new diff",
                {"expectedSha256": candidate["baseSha256"], "actualSha256": current_sha},
            )
        plan = build_plan(current, candidate["profiles"])
        if not plan.changed:
            return {
                "noOp": True,
                "message": "/etc/hosts is already in sync",
                "beforeSha256": current_sha,
                "afterSha256": current_sha,
                "managedSha256": plan.managed_sha256,
                "configSha256": candidate["configSha256"],
                "appliedAt": _utc_now(),
                "backup": "",
            }
        backup = create_backup(current)
        atomic_replace(TARGET, plan.desired, current_sha, current_stat)
        applied_at = _utc_now()
        transaction = {
            "action": "apply",
            "callerUid": caller_uid,
            "appliedAt": applied_at,
            "beforeSha256": current_sha,
            "afterSha256": plan.desired_sha256,
            "managedSha256": plan.managed_sha256,
            "configSha256": candidate["configSha256"],
            "backup": backup,
        }
        try:
            _atomic_state_write({"schemaVersion": 1, "lastTransaction": transaction})
        except Exception as exc:
            _rollback_after_metadata_failure(
                current,
                plan.desired_sha256,
                metadata_error=exc,
                recovery_backup=backup,
            )
        rotate_backups(backup)
        _audit(f"apply uid={caller_uid} before={current_sha[:12]} after={plan.desired_sha256[:12]} backup={backup}")
        return {"noOp": False, "message": "Enabled hosts profiles were applied atomically", **transaction}
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def undo(caller_uid: int, expected_after_sha256: str | None = None) -> dict[str, Any]:
    lock_fd = _locked()
    try:
        state = read_privileged_state()
        transaction = state.get("lastTransaction")
        if not isinstance(transaction, Mapping) or transaction.get("action") != "apply":
            raise PrivilegedError("nothing_to_undo", "There is no completed apply transaction to undo")
        if transaction.get("callerUid") != caller_uid:
            raise PrivilegedError("undo_owner", "Only the user who applied the transaction may undo it")
        after_sha = transaction.get("afterSha256")
        backup_name = transaction.get("backup")
        if not is_sha256(after_sha) or not isinstance(backup_name, str):
            raise PrivilegedError("state_invalid", "The last transaction metadata is incomplete")
        if expected_after_sha256 is not None:
            if not is_sha256(expected_after_sha256):
                raise PrivilegedError("invalid_hash", "Expected undo transaction hash is invalid")
            if expected_after_sha256 != after_sha:
                raise PrivilegedError(
                    "undo_preview_stale",
                    "The last apply transaction changed after the UI loaded; refresh before undoing",
                    {"expectedSha256": expected_after_sha256, "actualSha256": after_sha},
                )
        current, current_stat = read_regular(TARGET, expected_uid=os.geteuid())
        current_sha = sha256_bytes(current)
        if current_sha != after_sha:
            raise PrivilegedError(
                "undo_stale",
                "/etc/hosts changed since the last apply; refusing to overwrite newer changes",
                {"expectedSha256": after_sha, "actualSha256": current_sha},
            )
        restored = read_backup(backup_name)
        safety_backup = create_backup(current)
        atomic_replace(TARGET, restored, current_sha, current_stat)
        restored_sha = sha256_bytes(restored)
        undone_at = _utc_now()
        try:
            _atomic_state_write(
                {
                    "schemaVersion": 1,
                    "lastTransaction": None,
                    "lastUndo": {
                        "callerUid": caller_uid,
                        "undoneAt": undone_at,
                        "restoredSha256": restored_sha,
                        "safetyBackup": safety_backup,
                    },
                }
            )
        except Exception as exc:
            _rollback_after_metadata_failure(
                current,
                restored_sha,
                metadata_error=exc,
                recovery_backup=safety_backup,
            )
        rotate_backups(safety_backup)
        _audit(f"undo uid={caller_uid} from={current_sha[:12]} restored={restored_sha[:12]} backup={safety_backup}")
        return {
            "message": "The last apply transaction was undone",
            "undoneAt": undone_at,
            "beforeSha256": current_sha,
            "afterSha256": restored_sha,
            "safetyBackup": safety_backup,
        }
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077)
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if os.geteuid() != 0:
            raise PrivilegedError("not_root", "The helper must run as root through pkexec")
        caller_uid = caller_uid_from_environment()
        if not args:
            raise PrivilegedError("usage", "Expected 'apply CANDIDATE' or 'undo [EXPECTED_AFTER_SHA256]'")
        command = args[0]
        if command == "apply" and len(args) == 2:
            data = apply(args[1], caller_uid)
        elif command == "undo" and len(args) in {1, 2}:
            data = undo(caller_uid, args[1] if len(args) == 2 else None)
        else:
            raise PrivilegedError("usage", "Expected 'apply CANDIDATE' or 'undo [EXPECTED_AFTER_SHA256]'")
        _emit(True, data)
        return 0
    except HostsError as exc:
        _emit(False, exc.to_dict())
        return 2
    except Exception as exc:
        error = PrivilegedError("internal_error", str(exc) or type(exc).__name__)
        _emit(False, error.to_dict())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
