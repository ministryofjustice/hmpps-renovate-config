"""Common Docker download volume with scanner/toolchain cache namespaces."""

import hashlib
import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from gappy.contracts import Provider
from gappy.io import GappyError
from gappy.scan.process import run

TARGET = "/gappy-cache"
VOLUME = "gappy-download-cache-v2"

# Keep package downloads away from credentials, configuration and source checkouts.
DOWNLOAD_ENV = {
    "XDG_CACHE_HOME": TARGET + "/xdg",
    "npm_config_cache": TARGET + "/npm",
    "YARN_CACHE_FOLDER": TARGET + "/yarn",
    "PIP_CACHE_DIR": TARGET + "/pip",
    "UV_CACHE_DIR": TARGET + "/uv",
    "GOMODCACHE": TARGET + "/go/mod",
    "GOCACHE": TARGET + "/go/build",
    "GRADLE_USER_HOME": TARGET + "/gradle",
    "CARGO_HOME": TARGET + "/cargo",
    "COMPOSER_CACHE_DIR": TARGET + "/composer",
    "NUGET_PACKAGES": TARGET + "/nuget",
    "HELM_CACHE_HOME": TARGET + "/helm",
    "MAVEN_OPTS": "-Dmaven.repo.local=" + TARGET + "/maven",
    "BUNDLE_USER_CACHE": TARGET + "/bundler",
}


def volume_name(repository: str, provider: Provider, ecosystem: str, image: str) -> str:
    return VOLUME


def cache_namespace(provider: Provider, ecosystem: str, image: str) -> str:
    identity = hashlib.sha256((ecosystem + "\0" + image).encode()).hexdigest()[:24]
    return f"{provider}-{identity}"


def scoped_ecosystem(repository: str, ecosystem: str, scope: str | None) -> str:
    if scope is None:
        return ecosystem
    identity = hashlib.sha256((repository.lower() + "\0" + scope).encode()).hexdigest()[:24]
    return f"{ecosystem}-scope-{identity}"


def proxy_cache_scope(repository: str, ecosystem: str, proxy_image: str) -> str:
    identity = hashlib.sha256(
        (repository.lower() + "\0" + ecosystem + "\0" + proxy_image).encode()
    ).hexdigest()[:24]
    return "proxy-" + identity


def prepare(
    repository: str,
    provider: Provider,
    ecosystem: str,
    image: str,
    env: dict[str, str],
    timeout: int,
) -> str:
    name = volume_name(repository, provider, ecosystem, image)
    code, _ = run(
        ["docker", "volume", "create", "--label", "io.gappy.cache=true", name],
        timeout=30,
        env=env,
    )
    if code:
        raise GappyError("Cannot create persistent scanner cache volume.")
    home = "/home/dependabot" if provider == "dependabot" else "/home/ubuntu"
    directory = TARGET + "/" + cache_namespace(provider, ecosystem, image)
    # Each scanner owns only its namespace; never chown the shared root or existing contents.
    code, _ = run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--user",
            "0:0",
            "--mount",
            f"type=volume,src={name},dst={TARGET}",
            "--entrypoint",
            "sh",
            image,
            "-ec",
            'mkdir -p "$1" && chown --reference="$2" "$1"',
            "gappy-cache-init",
            directory,
            home,
        ],
        timeout=timeout,
        env=env,
    )
    if code:
        raise GappyError("Cannot initialize persistent scanner cache permissions.")
    return name


@contextmanager
def staged(
    volume: str,
    image: str,
    env: dict[str, str],
    timeout: int,
    *,
    namespace: str,
) -> Iterator[Path]:
    """CLI 1.94 accepts bind paths only; stage downloads through Docker's copy API."""
    container = "gappy-cache-transfer-" + uuid.uuid4().hex
    code, _ = run(
        [
            "docker",
            "create",
            "--name",
            container,
            "--network=none",
            "--mount",
            f"type=volume,src={volume},dst={TARGET},volume-subpath={namespace}",
            "--entrypoint",
            "true",
            image,
        ],
        timeout=timeout,
        env=env,
    )
    if code:
        raise GappyError("Cannot create Dependabot cache transfer container.")
    try:
        with tempfile.TemporaryDirectory(prefix="gappy-download-cache-") as directory:
            path = Path(directory) / "downloads"
            path.mkdir()
            code, _ = run(
                ["docker", "cp", f"{container}:{TARGET}/.", str(path)],
                timeout=timeout,
                env=env,
            )
            if code:
                raise GappyError("Cannot restore Dependabot downloads from cache volume.")
            # Only the staged copy is shared across host/updater UIDs. Its parent
            # remains private (0700), and no host home/config directory is mounted.
            for folder, directories, filenames in os.walk(path, followlinks=False):
                Path(folder).chmod(0o777)
                for name in directories + filenames:
                    child = Path(folder) / name
                    if child.is_symlink():
                        raise GappyError("Symlinks in staged download caches are unsupported.")
                    child.chmod(0o777 if child.is_dir() else 0o666)
            try:
                yield path
            finally:
                code, _ = run(
                    ["docker", "cp", str(path) + "/.", f"{container}:{TARGET}"],
                    timeout=timeout,
                    env=env,
                )
                if code:
                    raise GappyError("Cannot persist Dependabot downloads to cache volume.")
    finally:
        code, _ = run(["docker", "rm", container], timeout=30, env=env)
        if code:
            raise GappyError("Cannot remove owned Dependabot cache transfer container.")
