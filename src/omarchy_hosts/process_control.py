"""Bounded subprocess execution with deadlines and process-group teardown."""

from __future__ import annotations

from dataclasses import dataclass
import os
import selectors
import signal
import subprocess
import time
from typing import Mapping, Sequence


class ProcessControlError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def _signal_group(process: subprocess.Popen[bytes], signum: int) -> None:
    """Signal the child's dedicated process group, with a direct fallback."""

    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return
    except OSError:
        try:
            process.send_signal(signum)
        except OSError:
            pass


def _terminate_group(
    process: subprocess.Popen[bytes],
    *,
    grace: float = 0.75,
) -> None:
    """Terminate the complete child session, escalating to SIGKILL."""

    _signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + max(0.0, grace)
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.025)
    _signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _reap_remaining_group(process: subprocess.Popen[bytes]) -> None:
    """Do not leave detached descendants after the leader exits normally."""

    _signal_group(process, signal.SIGTERM)
    time.sleep(0.025)
    _signal_group(process, signal.SIGKILL)


def run_bounded_process(
    command: Sequence[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    env: Mapping[str, str] | None = None,
) -> BoundedProcessResult:
    """Run argv without a shell, bounding time, output, and descendants."""

    if timeout <= 0 or stdout_limit < 0 or stderr_limit < 0:
        raise ValueError("invalid process limits")
    argv = [str(part) for part in command]
    if not argv or any("\x00" in part for part in argv):
        raise ValueError("invalid command")

    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        close_fds=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    selector = selectors.DefaultSelector()
    stdout = bytearray()
    stderr = bytearray()
    streams = {
        process.stdout.fileno(): ("stdout", stdout, stdout_limit),
        process.stderr.fileno(): ("stderr", stderr, stderr_limit),
    }
    for fd, metadata in streams.items():
        os.set_blocking(fd, False)
        selector.register(fd, selectors.EVENT_READ, metadata)

    deadline = time.monotonic() + timeout
    completed = False
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessControlError(
                    "timeout",
                    f"process exceeded {timeout:g} seconds",
                )

            events = selector.select(min(0.1, remaining))
            if not events and process.poll() is not None:
                # EOF delivery may lag leader exit. Probe every remaining pipe.
                events = [
                    (key, selectors.EVENT_READ)
                    for key in list(selector.get_map().values())
                ]

            for key, _ in events:
                stream_name, output, limit = key.data
                read_size = min(65536, max(1, limit + 1 - len(output)))
                try:
                    chunk = os.read(key.fd, read_size)
                except BlockingIOError:
                    continue
                if not chunk:
                    try:
                        selector.unregister(key.fd)
                    except KeyError:
                        pass
                    continue
                output.extend(chunk)
                if len(output) > limit:
                    raise ProcessControlError(
                        "output_limit",
                        f"{stream_name} exceeded {limit} bytes",
                    )

        try:
            returncode = process.wait(
                timeout=max(0.0, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessControlError(
                "timeout",
                f"process exceeded {timeout:g} seconds",
            ) from exc

        completed = True
        _reap_remaining_group(process)
        return BoundedProcessResult(
            returncode=returncode,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )
    except BaseException:
        _terminate_group(process)
        raise
    finally:
        if not completed and process.poll() is None:
            _terminate_group(process)
        selector.close()
        process.stdout.close()
        process.stderr.close()
