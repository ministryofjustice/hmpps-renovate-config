"""Configure public Nexus proxies and disposable-snapshot registry overrides."""

import argparse
import base64
import getpass
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from gappy.contracts import Snapshot
from gappy.io import GappyError
from gappy.scan.snapshot import content_hash


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise GappyError("Nexus redirected an authenticated request; refusing to follow it.")


DEFAULT_HELM_UPSTREAM = "https://ministryofjustice.github.io/hmpps-helm-charts"
DEFAULT_NPM_MIRROR = "https://host.docker.internal:8443/repository/npm-all/"
DEFAULT_PYPI_MIRROR = "https://host.docker.internal:8443/repository/pypi/simple/"
DEFAULT_DOCKER_MIRROR = "https://host.docker.internal:8444"
DEFAULT_HELM_MIRROR = (
    DEFAULT_HELM_UPSTREAM + "=https://host.docker.internal:8443/repository/helm-group/"
)


def configure(url: str, password: str, *, helm_upstream: str = DEFAULT_HELM_UPSTREAM) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise GappyError("Nexus setup requires an HTTP loopback URL without credentials or a path.")
    if not password.strip():
        raise GappyError("Nexus administrator password is empty.")
    token = base64.b64encode(f"admin:{password.strip()}".encode()).decode()
    opener = urllib.request.build_opener(NoRedirect())

    def request(method: str, path: str, body: object | None = None) -> Any:
        req = urllib.request.Request(
            url.rstrip("/") + "/service/rest/v1/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with opener.open(req, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            raise GappyError(f"Nexus {method} {path} failed with HTTP {exc.code}.") from None
        except (OSError, ValueError):
            raise GappyError(
                f"Nexus {method} {path} failed; check readiness and connectivity."
            ) from None

    helm_upstream = mirror_url(helm_upstream, "Helm upstream")
    repositories = request("GET", "repositories")
    if not isinstance(repositories, list):
        raise GappyError("Nexus returned an invalid repository list.")
    for name, format_, upstream in (
        ("npmjs", "npm", "https://registry.npmjs.org"),
        ("pypi", "pypi", "https://pypi.org"),
        ("dockerhub", "docker", "https://registry-1.docker.io"),
        ("helm", "helm", helm_upstream.rstrip("/")),
    ):
        existing = next(
            (repo for repo in repositories if isinstance(repo, dict) and repo.get("name") == name),
            None,
        )
        if existing is not None:
            attributes = existing.get("attributes")
            proxy = attributes.get("proxy") if isinstance(attributes, dict) else None
            remote = proxy.get("remoteUrl") if isinstance(proxy, dict) else None
            if (
                existing.get("format") != format_
                or existing.get("type") != "proxy"
                or not isinstance(remote, str)
                or remote.rstrip("/") != upstream
            ):
                raise GappyError(f"Existing {name} repository is not the expected public proxy.")
            if format_ == "docker":
                docker_attributes = (
                    attributes.get("docker") if isinstance(attributes, dict) else None
                )
                if (
                    not isinstance(docker_attributes, dict)
                    or docker_attributes.get("httpPort") != 8082
                    or docker_attributes.get("forceBasicAuth") is not False
                ):
                    raise GappyError(
                        "Existing dockerhub proxy needs anonymous HTTP connector 8082."
                    )
            continue
        body: dict[str, Any] = {
            "name": name,
            "online": True,
            "storage": {"blobStoreName": "default", "strictContentTypeValidation": True},
            "proxy": {
                "remoteUrl": upstream,
                "contentMaxAge": 1440,
                "metadataMaxAge": 60,
            },
            "negativeCache": {"enabled": True, "timeToLive": 5},
            "httpClient": {"blocked": False, "autoBlock": True},
        }
        if format_ == "docker":
            body["docker"] = {"v1Enabled": False, "forceBasicAuth": False, "httpPort": 8082}
            body["dockerProxy"] = {"indexType": "HUB", "cacheForeignLayers": False}
        request("POST", f"repositories/{format_}/proxy", body)
    realms = request("GET", "security/realms/active")
    if not isinstance(realms, list) or not all(isinstance(realm, str) for realm in realms):
        raise GappyError("Nexus returned an invalid active realm list.")
    if "DockerToken" not in realms:
        request("PUT", "security/realms/active", [*realms, "DockerToken"])
    request(
        "PUT",
        "security/anonymous",
        {"enabled": True, "userId": "anonymous", "realmName": "NexusAuthorizingRealm"},
    )


def mirror_url(url: str, label: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in url)
    ):
        raise GappyError(
            f"{label} must be a container-reachable HTTP(S) URL without credentials; "
            "use host.docker.internal rather than localhost."
        )
    return url.rstrip("/") + "/"


def apply_npm_mirror(root: Path, snapshot: Snapshot, url: str) -> Snapshot:
    """Apply an explicit public-npm transport override to the disposable snapshot only."""
    url = mirror_url(url, "npm mirror")
    config_path = root / "renovate.json"
    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, UnicodeError):
        raise GappyError("Invalid Renovate configuration for npm mirror routing.") from None
    if not isinstance(config, dict):
        raise GappyError("Renovate configuration must be an object.")
    if "npmrc" in config:
        raise GappyError("npm mirror cannot override an explicit Renovate npmrc configuration.")
    rules = config.setdefault("packageRules", [])
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        raise GappyError("Renovate packageRules must be an array of objects.")
    if any("registryUrls" in rule for rule in rules):
        raise GappyError("npm mirror cannot override explicit Renovate registryUrls.")
    paths = {root / ".npmrc"} | {path for path in root.rglob(".npmrc")}
    paths |= {path.parent / ".npmrc" for path in root.rglob("package.json")}
    rewritten: dict[Path, str] = {}
    for path in paths:
        lines = path.read_text().splitlines() if path.exists() else []
        for line in lines:
            key, separator, value = line.strip().partition("=")
            key = key.strip()
            if separator and (key == "registry" or key.endswith(":registry")):
                if key != "registry" or value.strip().rstrip("/") != "https://registry.npmjs.org":
                    raise GappyError("npm mirror cannot override a private or scoped npm registry.")
        rewritten[path] = (
            "\n".join(
                line for line in lines if line.strip().partition("=")[0].strip() != "registry"
            )
            + f"\nregistry={url}\n"
        )
    for path, content in rewritten.items():
        path.write_text(content)
    rules.append({"matchDatasources": ["npm"], "registryUrls": [url]})
    config_path.write_text(json.dumps(config))
    return snapshot.model_copy(
        update={
            "contentHash": content_hash(root),
            "configHashes": {
                **snapshot.configHashes,
                "renovate.json": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                "gappy:npm-mirror": hashlib.sha256(url.encode()).hexdigest(),
            },
        }
    )


def registry_credentials(
    ecosystem: str, *, pypi: str | None = None, docker: str | None = None
) -> list[dict[str, object]]:
    if ecosystem == "pip" and pypi is not None:
        return [{"type": "python_index", "index-url": pypi, "replaces-base": True}]
    if ecosystem in {"docker", "docker-compose"} and docker is not None:
        return [
            {"type": "docker_registry", "registry": urlsplit(docker).netloc, "replaces-base": True}
        ]
    return []


def apply_registry_mirrors(
    root: Path,
    snapshot: Snapshot,
    *,
    pypi: str | None = None,
    docker: str | None = None,
    helm: tuple[str, ...] = (),
) -> Snapshot:
    """Route public base registries and explicitly mapped Helm HTTP repositories only."""
    pypi = mirror_url(pypi, "PyPI mirror") if pypi is not None else None
    docker = mirror_url(docker, "Docker mirror") if docker is not None else None
    if docker and (urlsplit(docker).scheme != "https" or urlsplit(docker).path != "/"):
        raise GappyError("Docker mirror must be an HTTPS registry origin without a path.")
    mappings = {}
    for entry in helm:
        upstream, separator, proxy = entry.partition("=")
        if not separator:
            raise GappyError("Helm mirror must use UPSTREAM_URL=PROXY_URL format.")
        upstream = mirror_url(upstream, "Helm upstream").rstrip("/")
        if upstream in mappings:
            raise GappyError("Helm upstream is mapped more than once.")
        mappings[upstream] = mirror_url(proxy, "Helm mirror").rstrip("/")
    config_path = root / "renovate.json"
    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, UnicodeError):
        raise GappyError("Invalid Renovate configuration for registry mirror routing.") from None
    if not isinstance(config, dict):
        raise GappyError("Renovate configuration must be an object.")
    rules = config.setdefault("packageRules", [])
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        raise GappyError("Renovate packageRules must be an array of objects.")
    if any("registryUrls" in rule for rule in rules) or "registryUrls" in config:
        # npm's explicit override is the only rule generated before this function.
        if (
            any(
                rule != {"matchDatasources": ["npm"], "registryUrls": rule.get("registryUrls")}
                for rule in rules
                if "registryUrls" in rule
            )
            or "registryUrls" in config
        ):
            raise GappyError("Registry mirrors cannot override explicit Renovate registryUrls.")
    if pypi:
        for path in root.rglob("*"):
            if not path.is_file() or not (
                path.name in {"pyproject.toml", "pip.conf", "Pipfile", "uv.toml"}
                or path.suffix in {".txt", ".in"}
            ):
                continue
            content = path.read_text()
            if re.search(
                r"(?:--(?:extra-)?index-url|(?:extra-)?index-url\s*=|PIP_INDEX_URL|"
                r"(?m:^\s*-i(?:\s|=)))",
                content,
            ) or re.search(
                r"(?m)^\s*(?:\[\[?(?:tool\.(?:poetry\.source|uv\.index)|source|index)|"
                r"(?:index|index_url|index-url)\s*=)",
                content,
            ):
                raise GappyError(
                    "PyPI mirror cannot override explicit Python index/source configuration."
                )
        rules.append({"matchDatasources": ["pypi"], "registryUrls": [pypi]})
    if docker:
        rules.append(
            {
                "matchDatasources": ["docker"],
                "matchPackageNames": [
                    "/^(?:(?:docker\\.io|index\\.docker\\.io|registry\\.hub\\.docker\\.com)/)?"
                    "[a-z0-9_-]+(?:/[a-z0-9_.-]+)?$/"
                ],
                "registryUrls": [docker.rstrip("/")],
            }
        )
    rewritten: dict[Path, str] = {}
    patterns = (
        ("Chart.yaml", "Chart.lock", "requirements.yaml", "requirements.lock") if mappings else ()
    )
    for pattern in patterns:
        for path in root.rglob(pattern):
            try:
                data = yaml.safe_load(path.read_text())
            except (yaml.YAMLError, UnicodeError):
                raise GappyError(
                    "Cannot parse a Helm dependency file for mirror routing."
                ) from None
            if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
                continue
            changed = False
            for dependency in data["dependencies"]:
                repository = dependency.get("repository") if isinstance(dependency, dict) else None
                if isinstance(repository, str) and repository.rstrip("/") in mappings:
                    dependency["repository"] = mappings[repository.rstrip("/")]
                    changed = True
            if changed:
                rewritten[path] = yaml.safe_dump(data, sort_keys=False)
    for path, content in rewritten.items():
        path.write_text(content)
    config_path.write_text(json.dumps(config))
    fingerprints = {**snapshot.configHashes}
    for name, value in (("pypi", pypi), ("docker", docker), ("helm", mappings)):
        if value:
            fingerprints[f"gappy:{name}-mirror"] = hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest()
    fingerprints["renovate.json"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    return snapshot.model_copy(
        update={
            "contentHash": content_hash(root),
            "configHashes": fingerprints,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8081")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--helm-upstream", default=DEFAULT_HELM_UPSTREAM)
    args = parser.parse_args()
    password = (
        sys.stdin.read() if args.password_stdin else getpass.getpass("Nexus admin password: ")
    )
    try:
        configure(args.url, password, helm_upstream=args.helm_upstream)
    except GappyError as exc:
        parser.exit(1, f"{exc}\n")
    print(
        "Nexus npm, PyPI, Docker Hub and Helm proxies ready. With the bundled TLS gateway, use "
        "--npm-mirror https://host.docker.internal:8443/repository/npm-all/ "
        "--pypi-mirror https://host.docker.internal:8443/repository/pypi/simple/ "
        "--docker-mirror https://host.docker.internal:8444 "
        f"--helm-mirror {args.helm_upstream.rstrip('/')}="
        "https://host.docker.internal:8443/repository/helm-group/ "
        "--registry-ca .gappy-mirror/ca.crt (export the gateway CA first)."
    )


if __name__ == "__main__":
    main()
