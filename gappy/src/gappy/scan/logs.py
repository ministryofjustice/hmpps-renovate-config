"""Persist redacted, unprocessed native scanner output alongside scan results."""

import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import TextIO

from gappy.scan.auth import GhcrCredentials, redact


class RawLog:
    """Line sink that redacts credentials before anything reaches disk."""

    def __init__(self, stream: TextIO, redactors: list[Callable[[str], str]]) -> None:
        self.stream = stream
        self.redactors = redactors
        self.lock = Lock()

    def line(self, text: str) -> None:
        for redactor in self.redactors:
            text = redactor(text)
        with self.lock:
            self.stream.write(text)
            self.stream.flush()

    def note(self, text: str) -> None:
        self.line(f"# gappy: {text}\n")


@contextmanager
def raw_log(
    directory: Path | None,
    name: str,
    github_token: str | None = None,
    ghcr: GhcrCredentials | None = None,
) -> Iterator[RawLog | None]:
    if directory is None:
        yield None
        return
    directory.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "scanner"
    path = directory / f"{safe}.log"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    redactors: list[Callable[[str], str]] = [lambda text: redact(text, github_token)]
    if ghcr is not None:
        redactors.append(ghcr.redact)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        yield RawLog(stream, redactors)
