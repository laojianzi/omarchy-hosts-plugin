"""Linux descriptor-relative filesystem helpers.

The helpers in this module deliberately avoid check-then-reopen pathname
flows. Managed directories are opened one component at a time with
``O_DIRECTORY | O_NOFOLLOW`` and child operations are performed relative to
the held directory descriptor.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
import secrets
import stat
from typing import Final


class SecurePathError(RuntimeError):
    """A pathname or filesystem object violated a security invariant."""


_DIRECTORY_FLAGS: Final[int] = (
    os.O_RDONLY
    | os.O_DIRECTORY
    | os.O_CLOEXEC
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_NOFOLLOW: Final[int] = getattr(os, "O_NOFOLLOW", 0)


def path_component(name: str) -> str:
    """Validate and return one basename-style path component."""

    if not name or name in {".", ".."} or "/" in name or "\x00" in name:
        raise SecurePathError(f"unsafe path component: {name!r}")
    return name


def open_directory_path(
    path: Path,
    *,
    create: bool = False,
    create_mode: int = 0o700,
) -> int:
    """Open an absolute directory without following any component symlink.

    Missing components may be created one at a time beneath a directory that
    is already held open. The caller owns the returned descriptor.
    """

    target = Path(path)
    if not target.is_absolute():
        raise SecurePathError(f"directory path must be absolute: {target}")

    current = os.open("/", _DIRECTORY_FLAGS)
    try:
        for raw_part in target.parts[1:]:
            part = path_component(raw_part)
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, create_mode, dir_fd=current)
                except FileExistsError:
                    # A concurrent creator won. Opening with O_NOFOLLOW and
                    # validating the resulting object remains authoritative.
                    pass
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def open_directory_at(
    parent_fd: int,
    name: str,
    *,
    create: bool = False,
    mode: int = 0o700,
) -> int:
    """Open one child directory relative to a held parent descriptor."""

    component = path_component(name)
    try:
        return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(component, mode, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_fd)


def validate_directory_fd(
    fd: int,
    *,
    owner_uid: int | None = None,
    forbidden_mode_bits: int = 0o022,
    force_mode: int | None = None,
) -> os.stat_result:
    """Validate a held directory and optionally tighten its mode by fd."""

    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise SecurePathError("opened object is not a directory")
    if owner_uid is not None and info.st_uid != owner_uid:
        raise SecurePathError(
            f"directory owner {info.st_uid} does not match uid {owner_uid}"
        )
    if forbidden_mode_bits and stat.S_IMODE(info.st_mode) & forbidden_mode_bits:
        raise SecurePathError("directory has unsafe permissions")
    if force_mode is not None and stat.S_IMODE(info.st_mode) != force_mode:
        os.fchmod(fd, force_mode)
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) != force_mode:
            raise SecurePathError("directory mode could not be secured")
    return info


def open_regular_file_at(
    directory_fd: int,
    name: str,
    *,
    flags: int = os.O_RDONLY,
    mode: int = 0o600,
    create: bool = False,
    exclusive: bool = False,
    owner_uid: int | None = None,
    require_single_link: bool = True,
    forbidden_mode_bits: int = 0,
    force_mode: int | None = None,
) -> tuple[int, os.stat_result]:
    """Open and validate one regular file relative to a held directory."""

    component = path_component(name)
    open_flags = flags | os.O_CLOEXEC | _FILE_NOFOLLOW
    if create:
        open_flags |= os.O_CREAT
    if exclusive:
        open_flags |= os.O_EXCL
    fd = os.open(component, open_flags, mode, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SecurePathError(f"{component} is not a regular file")
        if owner_uid is not None and info.st_uid != owner_uid:
            raise SecurePathError(
                f"{component} owner {info.st_uid} does not match uid {owner_uid}"
            )
        if require_single_link and info.st_nlink != 1:
            raise SecurePathError(f"{component} must have exactly one hard link")
        if forbidden_mode_bits and stat.S_IMODE(info.st_mode) & forbidden_mode_bits:
            raise SecurePathError(f"{component} has unsafe permissions")
        if force_mode is not None and stat.S_IMODE(info.st_mode) != force_mode:
            os.fchmod(fd, force_mode)
            info = os.fstat(fd)
            if stat.S_IMODE(info.st_mode) != force_mode:
                raise SecurePathError(f"{component} mode could not be secured")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def read_fd_bounded(fd: int, maximum: int) -> bytes:
    """Read from the descriptor once, rejecting content above ``maximum``."""

    if maximum < 0:
        raise ValueError("maximum must be non-negative")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(131072, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise SecurePathError(f"input exceeds the {maximum} byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def read_regular_file_at(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
    owner_uid: int | None = None,
    require_single_link: bool = True,
    forbidden_mode_bits: int = 0,
) -> tuple[bytes, os.stat_result]:
    """Open and read one regular file through one bounded descriptor."""

    fd, info = open_regular_file_at(
        directory_fd,
        name,
        owner_uid=owner_uid,
        require_single_link=require_single_link,
        forbidden_mode_bits=forbidden_mode_bits,
    )
    try:
        if info.st_size > maximum:
            raise SecurePathError(f"{name} exceeds the {maximum} byte limit")
        return read_fd_bounded(fd, maximum), info
    finally:
        os.close(fd)


def create_private_file_at(
    directory_fd: int,
    *,
    prefix: str,
    suffix: str,
    mode: int = 0o600,
    attempts: int = 64,
) -> tuple[int, str]:
    """Create an unpredictable single-link regular file in a held directory."""

    if (
        not prefix
        or "/" in prefix
        or "\x00" in prefix
        or "/" in suffix
        or "\x00" in suffix
    ):
        raise SecurePathError("unsafe temporary filename template")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _ in range(attempts):
        name = f"{prefix}{secrets.token_hex(16)}{suffix}"
        try:
            fd, _ = open_regular_file_at(
                directory_fd,
                name,
                flags=flags,
                mode=mode,
                owner_uid=os.geteuid(),
                force_mode=mode,
            )
        except FileExistsError:
            continue
        except BaseException:
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError:
                pass
            raise
        return fd, name
    raise OSError(errno.EEXIST, "could not allocate a unique temporary filename")


def write_all(fd: int, payload: bytes) -> None:
    """Write every byte or raise."""

    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]


def atomic_write_bytes_at(
    directory_fd: int,
    destination: str,
    payload: bytes,
    *,
    mode: int = 0o600,
    prefix: str = ".write-",
) -> None:
    """Atomically replace a child name without reopening the directory path."""

    target = path_component(destination)
    temp_fd, temp_name = create_private_file_at(
        directory_fd,
        prefix=prefix,
        suffix=".tmp",
        mode=mode,
    )
    temp_exists = True
    try:
        try:
            write_all(temp_fd, payload)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        os.replace(
            temp_name,
            target,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temp_exists = False
        os.fsync(directory_fd)
    finally:
        if temp_exists:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
