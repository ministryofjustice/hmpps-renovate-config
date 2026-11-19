"""Safe serialization and no-clobber publication."""

import json
import os
import tempfile
from pathlib import Path


class GappyError(Exception):
    """Sanitized, actionable application error."""


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GappyError(
            "Cannot read valid UTF-8 JSON input; check file format and permissions."
        ) from None


def preflight(outputs: list[Path], inputs: list[Path]) -> None:
    resolved = [item.resolve() for item in outputs]
    if len(set(resolved)) != len(resolved):
        raise GappyError("Output paths must be distinct.")
    for path in outputs:
        if path.exists() or path.is_symlink():
            raise GappyError("An output already exists; choose new output paths.")
        if not path.parent.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            # raise GappyError("Output parent directory does not exist.")
        if path.resolve() in {item.resolve() for item in inputs}:
            raise GappyError("An output path aliases an input.")


def publish(contents: dict[Path, str]) -> None:
    preflight(list(contents), [])
    staged: list[tuple[Path, Path]] = []
    published = 0
    try:
        for destination, text in contents.items():
            fd, name = tempfile.mkstemp(prefix=".gappy-", dir=destination.parent)
            temporary = Path(name)
            staged.append((temporary, destination))
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        for temporary, destination in staged:
            os.link(temporary, destination)
            published += 1
    except OSError:
        raise GappyError(
            f"Output publication failed after {published} file(s); "
            "check permissions and whether destinations appeared during execution."
        ) from None
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"
