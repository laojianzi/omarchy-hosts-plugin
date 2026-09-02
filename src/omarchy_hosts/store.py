"""User-owned Omarchy config persistence with descriptor-relative transactions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import unicodedata
from typing import Any, Iterator, Mapping

from .engine import (
    HostsError,
    SCHEMA_VERSION,
    entries_to_text,
    normalize_profile,
    normalize_profile_id,
    normalize_profiles,
    parse_entries_text,
)
from .securefs import (
    SecurePathError,
    atomic_write_bytes_at,
    open_directory_at,
    open_directory_path,
    open_regular_file_at,
    read_regular_file_at,
    validate_directory_fd,
)

MAX_STATE_BYTES = 4 * 1024 * 1024
_STATE_NAME = "state.json"
_LOCK_NAME = ".state.lock"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class StoreError(HostsError):
    pass


class StateStore:
    def __init__(self, config_home: Path | None = None):
        if config_home is None:
            configured = os.environ.get("XDG_CONFIG_HOME")
            config_home = Path(configured) if configured else Path.home() / ".config"
        self.config_home = Path(config_home)
        if not self.config_home.is_absolute():
            self.config_home = self.config_home.absolute()
        self.directory = self.config_home / "omarchy" / "hosts"
        self.path = self.directory / _STATE_NAME
        self.lock_path = self.directory / _LOCK_NAME
        self.uid = os.getuid()

    @staticmethod
    def default_state() -> dict[str, Any]:
        return {"schemaVersion": SCHEMA_VERSION, "profiles": [], "lastApply": None}

    @contextmanager
    def _state_directory_fd(self) -> Iterator[int]:
        """Open/create the managed directory chain without following symlinks."""

        config_fd = omarchy_fd = hosts_fd = -1
        try:
            config_fd = open_directory_path(
                self.config_home,
                create=True,
                create_mode=0o700,
            )
            validate_directory_fd(
                config_fd,
                owner_uid=self.uid,
                forbidden_mode_bits=0o022,
            )
            omarchy_fd = open_directory_at(
                config_fd,
                "omarchy",
                create=True,
                mode=0o700,
            )
            validate_directory_fd(
                omarchy_fd,
                owner_uid=self.uid,
                forbidden_mode_bits=0o022,
                force_mode=0o700,
            )
            hosts_fd = open_directory_at(
                omarchy_fd,
                "hosts",
                create=True,
                mode=0o700,
            )
            validate_directory_fd(
                hosts_fd,
                owner_uid=self.uid,
                forbidden_mode_bits=0o022,
                force_mode=0o700,
            )
            yield hosts_fd
        except SecurePathError as exc:
            raise StoreError(
                "state_directory_unsafe",
                f"Unsafe state directory chain {self.directory}: {exc}",
            ) from exc
        except OSError as exc:
            raise StoreError(
                "state_directory",
                f"Cannot open state directory {self.directory}: {exc}",
            ) from exc
        finally:
            for fd in (hosts_fd, omarchy_fd, config_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _ensure_directory(self) -> None:
        with self._state_directory_fd():
            pass

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[int]:
        """Lock the state file through a lock opened relative to the held directory."""

        with self._state_directory_fd() as directory_fd:
            lock_fd = -1
            directory_locked = False
            try:
                fcntl.flock(
                    directory_fd,
                    fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
                )
                directory_locked = True
                lock_fd, _ = open_regular_file_at(
                    directory_fd,
                    _LOCK_NAME,
                    flags=os.O_RDWR | os.O_CREAT,
                    mode=0o600,
                    owner_uid=self.uid,
                    require_single_link=True,
                    force_mode=0o600,
                )
                fcntl.flock(
                    lock_fd,
                    fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
                )
                yield directory_fd
            except SecurePathError as exc:
                raise StoreError(
                    "state_lock_unsafe",
                    f"Unsafe state lock {self.lock_path}: {exc}",
                ) from exc
            except OSError as exc:
                raise StoreError("state_lock", f"Cannot lock state: {exc}") from exc
            finally:
                if lock_fd >= 0:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                    os.close(lock_fd)
                if directory_locked:
                    try:
                        fcntl.flock(directory_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass

    def _read_unlocked(self, directory_fd: int | None = None) -> dict[str, Any]:
        if directory_fd is None:
            with self._state_directory_fd() as opened_fd:
                return self._read_unlocked(opened_fd)
        try:
            payload, _ = read_regular_file_at(
                directory_fd,
                _STATE_NAME,
                maximum=MAX_STATE_BYTES,
                owner_uid=self.uid,
                require_single_link=True,
                forbidden_mode_bits=0o077,
            )
        except FileNotFoundError:
            return self.default_state()
        except SecurePathError as exc:
            message = str(exc)
            code = "state_too_large" if "exceeds" in message else "state_unsafe"
            if "permissions" in message:
                code = "state_permissions"
            raise StoreError(code, f"Unsafe state file {self.path}: {message}") from exc
        except OSError as exc:
            code = "state_unsafe" if exc.errno == errno.ELOOP else "state_read_failed"
            raise StoreError(
                code,
                f"Cannot read {self.path}: {exc}",
            ) from exc

        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise StoreError(
                "state_invalid",
                f"Cannot read {self.path}; fix or move the invalid JSON before continuing: {exc}",
                {"path": str(self.path)},
            ) from exc
        return self._normalize_state(raw)

    @staticmethod
    def _clean_timestamp(value: Any) -> str:
        text = str(value or "")
        return text[:64] if "\x00" not in text and "\n" not in text and "\r" not in text else ""

    def _normalize_state(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise StoreError("state_invalid", "State root must be a JSON object")
        if raw.get("schemaVersion") != SCHEMA_VERSION:
            raise StoreError(
                "state_version",
                f"Unsupported state schema: {raw.get('schemaVersion')!r}",
                {"supported": SCHEMA_VERSION},
            )
        raw_profiles = raw.get("profiles", [])
        normalized = normalize_profiles(raw_profiles)
        profiles: list[dict[str, Any]] = []
        for index, profile in enumerate(normalized):
            source = raw_profiles[index] if isinstance(raw_profiles[index], Mapping) else {}
            profile["createdAt"] = self._clean_timestamp(source.get("createdAt"))
            profile["updatedAt"] = self._clean_timestamp(source.get("updatedAt"))
            profiles.append(profile)
        last_apply = raw.get("lastApply")
        if last_apply is not None and not isinstance(last_apply, Mapping):
            last_apply = None
        return {
            "schemaVersion": SCHEMA_VERSION,
            "profiles": profiles,
            "lastApply": dict(last_apply) if isinstance(last_apply, Mapping) else None,
        }

    def _validate_existing_state_name(self, directory_fd: int) -> None:
        try:
            info = os.stat(
                _STATE_NAME,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StoreError(
                "state_write_failed",
                f"Cannot inspect {self.path}: {exc}",
            ) from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self.uid
            or info.st_nlink != 1
        ):
            raise StoreError("state_unsafe", f"Unsafe state file: {self.path}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise StoreError(
                "state_permissions",
                f"State file must have mode 0600: chmod 600 {self.path}",
            )

    def _write_unlocked(
        self,
        state: Mapping[str, Any],
        directory_fd: int | None = None,
    ) -> None:
        if directory_fd is None:
            with self._state_directory_fd() as opened_fd:
                self._write_unlocked(state, opened_fd)
                return
        normalized = self._normalize_state(state)
        payload = (
            json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_STATE_BYTES:
            raise StoreError(
                "state_too_large",
                f"State file exceeds {MAX_STATE_BYTES // 1024} KiB",
            )
        self._validate_existing_state_name(directory_fd)
        try:
            atomic_write_bytes_at(
                directory_fd,
                _STATE_NAME,
                payload,
                mode=0o600,
                prefix=".state-",
            )
        except (OSError, SecurePathError) as exc:
            raise StoreError(
                "state_write_failed",
                f"Cannot atomically write {self.path}: {exc}",
            ) from exc

    def load(self) -> dict[str, Any]:
        with self._lock(exclusive=False) as directory_fd:
            return self._read_unlocked(directory_fd)

    def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock(exclusive=True) as directory_fd:
            self._write_unlocked(state, directory_fd)
            return self._read_unlocked(directory_fd)

    @staticmethod
    def _slug(name: str) -> str:
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
        slug = slug[:48].strip("-")
        return slug or "profile"

    def _unique_id(self, name: str, existing: set[str]) -> str:
        base = self._slug(name)
        if base not in existing:
            return base
        while True:
            candidate = f"{base[:54]}-{secrets.token_hex(3)}"
            if candidate not in existing:
                return candidate

    def save_profile(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise StoreError("invalid_payload", "Profile payload must be a JSON object")
        with self._lock(exclusive=True) as directory_fd:
            state = self._read_unlocked(directory_fd)
            profiles = list(state["profiles"])
            requested_id = str(payload.get("id") or "").strip().lower()
            existing_index = (
                next((index for index, item in enumerate(profiles) if item["id"] == requested_id), None)
                if requested_id
                else None
            )
            name = str(payload.get("name") or "").strip()
            ids = {item["id"] for item in profiles}
            if existing_index is None:
                profile_id = normalize_profile_id(requested_id) if requested_id else self._unique_id(name, ids)
                if profile_id in ids:
                    raise StoreError("duplicate_profile_id", f"Profile id already exists: {profile_id}")
                created_at = utc_now()
                enabled = payload.get("enabled") is True
            else:
                previous = profiles[existing_index]
                profile_id = previous["id"]
                created_at = previous.get("createdAt") or utc_now()
                enabled = previous["enabled"] if "enabled" not in payload else payload.get("enabled") is True

            entries = parse_entries_text(payload.get("entriesText")) if "entriesText" in payload else payload.get("entries", [])
            profile = normalize_profile(
                {
                    "id": profile_id,
                    "name": payload.get("name"),
                    "description": payload.get("description", ""),
                    "enabled": enabled,
                    "entries": entries,
                }
            )
            profile["createdAt"] = created_at
            profile["updatedAt"] = utc_now()
            if existing_index is None:
                profiles.append(profile)
            else:
                profiles[existing_index] = profile
            state["profiles"] = profiles
            self._write_unlocked(state, directory_fd)
            result = dict(profile)
            result["entriesText"] = entries_to_text(profile["entries"])
            return result

    def toggle_profile(self, profile_id: str, enabled: bool) -> dict[str, Any]:
        key = normalize_profile_id(profile_id)
        with self._lock(exclusive=True) as directory_fd:
            state = self._read_unlocked(directory_fd)
            found: dict[str, Any] | None = None
            for profile in state["profiles"]:
                if profile["id"] == key:
                    profile["enabled"] = bool(enabled)
                    profile["updatedAt"] = utc_now()
                    found = profile
                    break
            if found is None:
                raise StoreError("profile_not_found", f"Unknown profile: {key}", {"profileId": key})
            self._write_unlocked(state, directory_fd)
            return found

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        key = normalize_profile_id(profile_id)
        with self._lock(exclusive=True) as directory_fd:
            state = self._read_unlocked(directory_fd)
            removed = next((profile for profile in state["profiles"] if profile["id"] == key), None)
            if removed is None:
                raise StoreError("profile_not_found", f"Unknown profile: {key}", {"profileId": key})
            state["profiles"] = [profile for profile in state["profiles"] if profile["id"] != key]
            self._write_unlocked(state, directory_fd)
            return removed

    def update_last_apply(self, metadata: Mapping[str, Any] | None) -> None:
        with self._lock(exclusive=True) as directory_fd:
            state = self._read_unlocked(directory_fd)
            state["lastApply"] = dict(metadata) if metadata is not None else None
            self._write_unlocked(state, directory_fd)

    def profiles_for_ui(self, state: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        source = state or self.load()
        profiles: list[dict[str, Any]] = []
        for profile in source["profiles"]:
            item = dict(profile)
            item["entriesText"] = entries_to_text(profile["entries"])
            item["entryCount"] = len(profile["entries"])
            item["nameCount"] = sum(len(entry["names"]) for entry in profile["entries"])
            profiles.append(item)
        return profiles
