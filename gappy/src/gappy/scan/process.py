"""Subprocess execution without a shell; raw output is persisted only via ``raw_line``."""

import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from threading import Event

from gappy.io import GappyError

OUTPUT_LIMIT = 16 * 1024 * 1024


class ProcessTimeout(GappyError):
    """A subprocess exceeded its execution deadline."""


def run(
    args: Sequence[str],
    *,
    timeout: float = 120,
    env: Mapping[str, str] | None = None,
    capture_line: Callable[[str], bool] | None = None,
    cancel: Event | None = None,
    output_limit: int | None = OUTPUT_LIMIT,
    raw_line: Callable[[str], None] | None = None,
) -> tuple[int, str]:
    """``raw_line`` receives every output line before capture filtering or limits apply."""
    if output_limit is not None and not 1 <= output_limit <= 256 * 1024 * 1024:
        raise GappyError("Scanner output budget must be between 1 byte and 256 MiB.")

    def check_limit(size: int) -> None:
        if output_limit is not None and size > output_limit:
            raise GappyError(
                f"Scanner output exceeded the {output_limit / (1024 * 1024):g} MiB safety limit."
            )

    timeout_error = f"Scanner exceeded its execution timeout ({timeout:g}s)."
    if cancel is not None and cancel.is_set():
        raise KeyboardInterrupt
    try:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            shell=False,
        )
    except OSError:
        raise GappyError("Cannot start required executable; check installation and PATH.") from None
    assert process.stdout is not None
    data = bytearray()
    pending = bytearray()
    raw_pending = bytearray()

    def tap(block: bytes) -> None:
        if raw_line is None:
            return
        raw_pending.extend(block)
        while (end := raw_pending.find(b"\n")) != -1:
            raw_line(raw_pending[: end + 1].decode("utf-8", errors="replace"))
            del raw_pending[: end + 1]

    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if cancel is not None and cancel.is_set():
                    raise KeyboardInterrupt
                if time.monotonic() >= deadline:
                    raise ProcessTimeout(timeout_error)
                for key, _ in selector.select(timeout=0.1):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        break
                    tap(block)
                    if capture_line is None:
                        data.extend(block)
                    else:
                        start = len(pending)
                        pending.extend(block)
                        while (end := pending.find(b"\n", start)) != -1:
                            line = bytes(pending[: end + 1])
                            del pending[: end + 1]
                            start = 0
                            check_limit(len(line))
                            if capture_line(line.decode("utf-8", errors="replace")):
                                data.extend(line)
                            check_limit(len(data))
                    check_limit(max(len(data), len(pending)))
            if pending and capture_line is not None:
                if capture_line(pending.decode("utf-8", errors="replace")):
                    data.extend(pending)
                check_limit(len(data))
            return process.wait(timeout=max(0.1, deadline - time.monotonic())), data.decode(
                "utf-8", errors="replace"
            )
    except subprocess.TimeoutExpired:
        raise ProcessTimeout(timeout_error) from None
    except MemoryError:
        raise GappyError(
            "Insufficient memory while collecting scanner output; reduce scan scope or add memory."
        ) from None
    finally:
        try:
            if raw_line is not None and raw_pending:
                raw_line(raw_pending.decode("utf-8", errors="replace"))
                raw_pending.clear()
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
