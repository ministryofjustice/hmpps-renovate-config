"""Freeze a tracked revision without touching the user's worktree."""

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from gappy.contracts import Snapshot
from gappy.io import GappyError
from gappy.scan.process import run


@contextmanager
def checkout(source: Path | None, repository: str) -> Iterator[Path]:
    """Use an explicit local source or acquire an isolated GitHub checkout."""
    if source is not None:
        yield source
        return
    with tempfile.TemporaryDirectory(prefix="gappy-checkout-") as directory:
        destination = Path(directory) / "repository"
        # Disable interactive authentication; never persist clone output or credentials.
        import os

        env = {
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "HOME", "TMPDIR", "SSH_AUTH_SOCK"}
        }
        env["GIT_TERMINAL_PROMPT"] = "0"
        code, _ = run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--",
                f"https://github.com/{repository}.git",
                str(destination),
            ],
            timeout=180,
            env=env,
        )
        if code:
            raise GappyError(
                "Cannot clone GitHub repository; check its name, network access, and Git "
                "authentication, or supply --source with an existing checkout."
            )
        yield destination


def content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(
            path.relative_to(root).as_posix().encode()
            + b"\0"
            + hashlib.sha256(path.read_bytes()).digest()
        )
    return digest.hexdigest()


def _default_private_packages(root: Path) -> None:
    for path in sorted(root.rglob("package.json")):
        if not path.is_file():
            continue
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            # Leave invalid manifests to the native scanner's diagnostics.
            continue
        if isinstance(package, dict) and "private" not in package:
            package["private"] = True
            path.write_text(
                json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


def freeze(
    source: Path,
    ref: str,
    destination: Path,
    *,
    config_dir: Path | None = None,
    require_configs: bool = True,
    replace_alternate_configs: bool = False,
) -> Snapshot:
    if not source.is_dir() or ref.startswith("-"):
        raise GappyError("Source must be a Git checkout and ref must identify a revision.")
    try:
        revision = (
            subprocess.run(
                ["git", "-C", str(source), "rev-parse", "--verify", f"{ref}^{{commit}}"],
                capture_output=True,
                check=True,
                timeout=30,
            )
            .stdout.decode()
            .strip()
        )
        archive = subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", revision],
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise GappyError("Cannot materialize the tracked Git revision.") from None
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in sorted(tar.getmembers(), key=lambda item: item.name):
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise GappyError("Snapshot contains unsupported paths or symbolic links.")
            if member.isdir():
                continue
            if not member.isfile() or member.size > 32 * 1024 * 1024:
                raise GappyError("Snapshot contains unsupported objects or oversized files.")
            stream = tar.extractfile(member)
            assert stream is not None
            content = stream.read()
            if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise GappyError("Unresolved Git LFS content is unsupported.")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    if config_dir is not None:
        supplied = {
            ".github/dependabot.yml": config_dir / "dependabot.yml",
            "renovate.json": config_dir / "renovate.json",
        }
        try:
            contents = {name: path.read_bytes() for name, path in supplied.items()}
        except OSError:
            raise GappyError(
                "Config directory must contain readable dependabot.yml and renovate.json."
            ) from None
        for name, content in contents.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if replace_alternate_configs:
            from gappy.forecast.config import DEPENDABOT_PATHS, RENOVATE_PATHS

            for name in (*DEPENDABOT_PATHS, *RENOVATE_PATHS):
                if name not in supplied:
                    (destination / name).unlink(missing_ok=True)
            package_path = destination / "package.json"
            if package_path.is_file():
                try:
                    package = json.loads(package_path.read_text())
                except (OSError, ValueError):
                    raise GappyError("Cannot normalize embedded Renovate configuration.") from None
                if isinstance(package, dict) and "renovate" in package:
                    del package["renovate"]
                    package_path.write_text(json.dumps(package))
    if (destination / ".gitmodules").exists():
        raise GappyError(
            "Submodules are unsupported; supply a fully materialized tracked snapshot."
        )
    configs = [".github/dependabot.yml", "renovate.json"]
    if require_configs and not all((destination / path).is_file() for path in configs):
        raise GappyError("Revision must contain .github/dependabot.yml and renovate.json.")
    _default_private_packages(destination)
    return Snapshot(
        revision=revision,
        contentHash=content_hash(destination),
        configHashes={
            name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
            for name in configs
            if (destination / name).is_file()
        },
    )
