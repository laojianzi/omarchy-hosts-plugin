"""User-owned Omarchy config persistence with locking and atomic writes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
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

MAX_STATE_BYTES = 4 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class StoreError(HostsError):
    pass


class StateStore:
    def __init__(self, config_home: Path | None = None):
        if config_home is None:
            config_home = Path.home() / ".config"
        self.directory = Path(config_home) / "omarchy" / "hosts"
        self.path = self.directory / "state.json"
        self.lock_path = self.directory / ".state.lock"
        self.uid = os.getuid()

    @staticmethod
    def default_state() -> dict[str, Any]:
        return {"schemaVersion": SCHEMA_VERSION, "profiles": [], "lastApply": None}

    def _ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            info = self.directory.lstat()
        except OSError as exc:
            raise StoreError("state_directory", f"Cannot inspect {self.directory}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid:
            raise StoreError(
                "state_directory_unsafe",
                f"State directory must be a real directory owned by uid {self.uid}: {self.directory}",
            )
        try:
            os.chmod(self.directory, 0o700)
        except OSError as exc:
            raise StoreError("state_directory", f"Cannot secure {self.directory}: {exc}") from exc

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        self._ensure_directory()
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.lock_path, flags, 0o600)
        except OSError as exc:
            raise StoreError("state_lock", f"Cannot open state lock: {exc}") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.uid or info.st_nlink != 1:
                raise StoreError("state_lock_unsafe", f"Unsafe state lock: {self.lock_path}")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _read_fd(fd: int, maximum: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(131072, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise StoreError("state_too_large", f"State file exceeds {maximum // 1024} KiB")
            chunks.append(chunk)
        return b"".join(chunks)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            before = self.path.lstat()
        except FileNotFoundError:
            return self.default_state()
        except OSError as exc:
            raise StoreError("state_read_failed", f"Cannot inspect {self.path}: {exc}") from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_uid != self.uid:
            raise StoreError("state_unsafe", f"State file must be a regular file owned by uid {self.uid}: {self.path}")
        if before.st_nlink != 1:
            raise StoreError("state_unsafe", f"State file must not have hard links: {self.path}")
        if stat.S_IMODE(before.st_mode) & 0o077:
            raise StoreError("state_permissions", f"State file must have mode 0600: chmod 600 {self.path}")
        if before.st_size > MAX_STATE_BYTES:
            raise StoreError("state_too_large", f"State file exceeds {MAX_STATE_BYTES // 1024} KiB")

        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags)
        except OSError as exc:
            raise StoreError("state_read_failed", f"Cannot open {self.path}: {exc}") from exc
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise StoreError("state_race", f"State file changed while being opened: {self.path}")
            payload = self._read_fd(fd, MAX_STATE_BYTES)
        finally:
            os.close(fd)
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

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        normalized = self._normalize_state(state)
        payload = (json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if len(payload) > MAX_STATE_BYTES:
            raise StoreError("state_too_large", f"State file exceeds {MAX_STATE_BYTES // 1024} KiB")
        self._ensure_directory()
        fd, temp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
            dir_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> dict[str, Any]:
        with self._lock(exclusive=False):
            return self._read_unlocked()

    def save(self, state: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock(exclusive=True):
            self._write_unlocked(state)
            return self._read_unlocked()

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
        with self._lock(exclusive=True):
            state = self._read_unlocked()
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
            self._write_unlocked(state)
            result = dict(profile)
            result["entriesText"] = entries_to_text(profile["entries"])
            return result

    def toggle_profile(self, profile_id: str, enabled: bool) -> dict[str, Any]:
        key = normalize_profile_id(profile_id)
        with self._lock(exclusive=True):
            state = self._read_unlocked()
            found: dict[str, Any] | None = None
            for profile in state["profiles"]:
                if profile["id"] == key:
                    profile["enabled"] = bool(enabled)
                    profile["updatedAt"] = utc_now()
                    found = profile
                    break
            if found is None:
                raise StoreError("profile_not_found", f"Unknown profile: {key}", {"profileId": key})
            self._write_unlocked(state)
            return found

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        key = normalize_profile_id(profile_id)
        with self._lock(exclusive=True):
            state = self._read_unlocked()
            removed = next((profile for profile in state["profiles"] if profile["id"] == key), None)
            if removed is None:
                raise StoreError("profile_not_found", f"Unknown profile: {key}", {"profileId": key})
            state["profiles"] = [profile for profile in state["profiles"] if profile["id"] != key]
            self._write_unlocked(state)
            return removed

    def update_last_apply(self, metadata: Mapping[str, Any] | None) -> None:
        with self._lock(exclusive=True):
            state = self._read_unlocked()
            state["lastApply"] = dict(metadata) if metadata is not None else None
            self._write_unlocked(state)

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
