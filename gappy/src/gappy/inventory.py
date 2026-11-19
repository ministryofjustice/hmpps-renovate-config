"""Independent direct npm patch inventory from frozen files and registry metadata."""

import gzip
import json
import re
import ssl
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from pydantic import ValidationError

from gappy.compare.assess import semver
from gappy.contracts import Dependency, Inventory, Opportunity, Provenance, Snapshot, timestamp
from gappy.io import GappyError, read_json

MAX_REGISTRY_BYTES = 64 * 1024 * 1024


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise GappyError("Registry redirected across origins; configure its canonical URL.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_registry(registry: str, package: str, timeout: int, ca: Path | None) -> dict[str, Any]:
    context = ssl.create_default_context()
    if ca is not None:
        context.load_verify_locations(cafile=str(ca))
    opener = build_opener(HTTPSHandler(context=context), SameOriginRedirect())
    request = Request(
        registry.rstrip("/") + "/" + quote(package, safe="@"),
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "gappy/0.1.0",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=response) as decoded:
                    payload = decoded.read(MAX_REGISTRY_BYTES + 1)
            elif encoding == "identity":
                payload = response.read(MAX_REGISTRY_BYTES + 1)
            else:
                raise GappyError("Registry returned an unsupported content encoding.")
        if len(payload) > MAX_REGISTRY_BYTES:
            raise GappyError(
                f"Registry metadata for {package} exceeds the "
                f"{MAX_REGISTRY_BYTES // (1024 * 1024)} MiB decoded inventory limit; "
                "supply --inventory from a separately prepared snapshot."
            )
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise GappyError("Registry metadata must be a JSON object.")
        return result
    except (OSError, URLError, ValueError, EOFError):
        raise GappyError(
            "Inventory registry lookup failed; check connectivity, TLS trust, registry "
            "availability, or supply --inventory. Private registry authentication is unsupported."
        ) from None


def registries(root: Path, directory: Path) -> dict[str, str]:
    result = {"registry": "https://registry.npmjs.org"}
    paths = [root]
    paths.extend(
        root / parent
        for parent in reversed(directory.relative_to(root).parents)
        if parent != Path(".")
    )
    if directory != root:
        paths.append(directory)
    for folder in dict.fromkeys(paths):
        npmrc = folder / ".npmrc"
        if not npmrc.is_file():
            continue
        try:
            lines = npmrc.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise GappyError("Cannot read snapshot .npmrc for inventory generation.") from None
        for line in lines:
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if "${" in line or re.search(r"auth|token|password|username", line, re.I):
                raise GappyError(
                    "Inventory does not support credential-bearing or variable-based .npmrc; "
                    "supply a separately prepared --inventory."
                )
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if separator and (key == "registry" or re.fullmatch(r"@[^/:\s]+:registry", key)):
                try:
                    Dependency(
                        ecosystem="npm",
                        package="validation",
                        installedVersion="0.0.0",
                        manifestPath="package.json",
                        registry=value,
                    )
                except ValidationError:
                    raise GappyError(
                        "Inventory registry must be a credential-free HTTP(S) URL."
                    ) from None
                result[key] = value.rstrip("/")
    return result


def installed_version(root: Path, manifest: Path, package: str, declared: str) -> str | None:
    directory = manifest.parent
    for folder in [directory, *directory.parents]:
        if not folder.is_relative_to(root):
            break
        lock_path = folder / "package-lock.json"
        if not lock_path.is_file():
            continue
        lock = read_json(lock_path)
        if not isinstance(lock, dict):
            raise GappyError("npm lockfile must be a JSON object.")
        packages = lock.get("packages")
        if isinstance(packages, dict):
            relative = directory.relative_to(folder).as_posix()
            context = "" if relative == "." else relative + "/"
            candidates = [context + "node_modules/" + package]
            # Node resolves hoisted direct dependencies through ancestor node_modules.
            parent = directory
            while parent != folder:
                parent = parent.parent
                prefix = parent.relative_to(folder).as_posix()
                candidates.append(
                    ("" if prefix == "." else prefix + "/") + "node_modules/" + package
                )
            for key in dict.fromkeys(candidates):
                entry = packages.get(key)
                if isinstance(entry, dict) and not entry.get("link"):
                    version = entry.get("version")
                    if isinstance(version, str):
                        return version
        elif folder == directory and isinstance(lock.get("dependencies"), dict):
            entry = lock["dependencies"].get(package)
            if isinstance(entry, dict):
                version = entry.get("version")
                if isinstance(version, str):
                    return version
    return declared if semver(declared) is not None else None


def generate(
    root: Path, snapshot: Snapshot, repository: str, timeout: int, ca: Path | None = None
) -> Inventory:
    started = datetime.now(UTC)
    warnings = [
        "Generated inventory covers direct npm manifest dependencies only, not a complete "
        "repository inventory; other ecosystems and transitive dependencies are excluded.",
        "Registry lookups precede scanner observations; live registry changes remain possible.",
    ]
    opportunities: list[Opportunity] = []
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    manifests = sorted(
        path
        for path in root.rglob("package.json")
        if "node_modules" not in path.relative_to(root).parts
    )
    if not manifests:
        raise GappyError("No npm manifests found for automatic inventory; supply --inventory.")
    for manifest in manifests:
        data = read_json(manifest)
        if not isinstance(data, dict):
            raise GappyError("npm manifest must be a JSON object.")
        registry_config = registries(root, manifest.parent)
        dependencies: dict[str, str] = {}
        for group in ("dependencies", "devDependencies", "optionalDependencies"):
            values = data.get(group, {})
            if not isinstance(values, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in values.items()
            ):
                raise GappyError("npm dependency sections must map package names to versions.")
            dependencies.update(values)
        if data.get("peerDependencies"):
            warnings.append(
                f"Peer-only dependency declarations excluded: {manifest.relative_to(root)}."
            )
        for package, declared in sorted(dependencies.items()):
            if not re.fullmatch(r"(?:@[a-zA-Z0-9._-]+/)?[a-zA-Z0-9._-]+", package):
                raise GappyError("npm package identity has an unsupported format.")
            version = installed_version(root, manifest, package, declared)
            installed = semver(version) if version is not None else None
            if installed is None or version is None:
                warnings.append(
                    f"Unresolved/non-stable installed version excluded: "
                    f"{manifest.relative_to(root)}: {package}."
                )
                continue
            registry = (
                registry_config.get(
                    package.split("/", 1)[0] + ":registry", registry_config["registry"]
                )
                if package.startswith("@")
                else registry_config["registry"]
            )
            key = registry, package
            if key not in cache:
                cache[key] = fetch_registry(registry, package, timeout, ca)
            metadata = cache[key]
            versions, times = metadata.get("versions"), metadata.get("time")
            if not isinstance(versions, dict) or not isinstance(times, dict):
                raise GappyError("Registry metadata is missing versions or release timestamps.")
            for target in sorted(versions):
                candidate = semver(target)
                if candidate is None or candidate[:2] != installed[:2] or candidate <= installed:
                    continue
                release = times.get(target)
                if not isinstance(release, str):
                    raise GappyError("Registry patch metadata is missing a release timestamp.")
                try:
                    available = timestamp(release)
                except ValueError:
                    raise GappyError("Registry patch release timestamp is invalid.") from None
                if available > started:
                    warnings.append("Patch releases after inventory start were excluded.")
                    continue
                opportunities.append(
                    Opportunity(
                        dependency=Dependency(
                            ecosystem="npm",
                            package=package,
                            manifestPath=manifest.relative_to(root).as_posix(),
                            installedVersion=version,
                            registry=registry,
                        ),
                        directness="direct",
                        candidateVersion=target,
                        availableAt=release,
                    )
                )
    return Inventory(
        schemaVersion="1.1.0",
        repository=repository,
        snapshotHash=snapshot.contentHash,
        asOf=started.isoformat(),
        provenance=Provenance(
            source="Independent frozen npm manifests/lockfiles and registry packuments",
            complete=False,
            warnings=sorted(set(warnings)),
        ),
        opportunities=opportunities,
    )
