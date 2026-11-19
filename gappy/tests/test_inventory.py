"""Independent inventory tests using synthetic files and controlled HTTP responses."""

import gzip
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request

import pytest

from gappy.contracts import Snapshot
from gappy.inventory import SameOriginRedirect, fetch_registry, generate
from gappy.io import GappyError, dump


def snapshot() -> Snapshot:
    return Snapshot(revision="a" * 40, contentHash="b" * 64, configHashes={})


def manifest(root: Path, dependencies: dict[str, str], path: str = "package.json") -> None:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(dump({"dependencies": dependencies}))


def metadata() -> dict[str, Any]:
    versions = ["1.2.0", "1.2.1", "1.2.2", "1.2.3-beta.1", "1.3.0", "2.0.0"]
    return {
        "versions": {version: {"version": version} for version in versions},
        "time": {version: "2024-01-01T00:00:00Z" for version in versions},
    }


def test_independent_inventory_includes_all_patch_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest(tmp_path, {"fixture": "^1.2.0"})
    (tmp_path / "package-lock.json").write_text(
        dump(
            {
                "lockfileVersion": 3,
                "packages": {"node_modules/fixture": {"version": "1.2.0"}},
            }
        )
    )
    monkeypatch.setattr("gappy.inventory.fetch_registry", lambda *args: metadata())
    inventory = generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert inventory.snapshotHash == snapshot().contentHash
    assert [item.candidateVersion for item in inventory.opportunities] == ["1.2.1", "1.2.2"]
    assert inventory.opportunities[0].dependency.installedVersion == "1.2.0"
    assert not inventory.provenance.complete
    assert inventory.provenance.warnings


@pytest.mark.parametrize("lock_version", [1, 2, 3])
def test_lockfile_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lock_version: int
) -> None:
    manifest(tmp_path, {"fixture": "~1.2.0"})
    lock: dict[str, Any] = {"lockfileVersion": lock_version}
    if lock_version == 1:
        lock["dependencies"] = {"fixture": {"version": "1.2.1"}}
    else:
        lock["packages"] = {"node_modules/fixture": {"version": "1.2.1"}}
    (tmp_path / "package-lock.json").write_text(dump(lock))
    monkeypatch.setattr("gappy.inventory.fetch_registry", lambda *args: metadata())
    result = generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert [item.candidateVersion for item in result.opportunities] == ["1.2.2"]


def test_workspace_hoisting_context_scopes_and_request_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest(tmp_path, {"@scope/fixture": "1.2.0"})
    manifest(tmp_path, {"@scope/fixture": "^1.2.0"}, "packages/app/package.json")
    (tmp_path / ".npmrc").write_text(
        "registry=https://default.example\n@scope:registry=https://scoped.example/\n"
    )
    (tmp_path / "package-lock.json").write_text(
        dump(
            {
                "lockfileVersion": 3,
                "packages": {"node_modules/@scope/fixture": {"version": "1.2.0"}},
            }
        )
    )
    calls = []

    def fetch(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return metadata()

    monkeypatch.setattr("gappy.inventory.fetch_registry", fetch)
    result = generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert len(calls) == 1
    assert calls[0][:2] == ("https://scoped.example", "@scope/fixture")
    assert {item.dependency.manifestPath for item in result.opportunities} == {
        "package.json",
        "packages/app/package.json",
    }


def test_unsupported_dependencies_warn_without_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest(tmp_path, {"fixture": "^1.2.0", "local": "workspace:*"})
    monkeypatch.setattr(
        "gappy.inventory.fetch_registry",
        lambda *args: pytest.fail("No unresolved dependency may be queried."),
    )
    result = generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert not result.opportunities
    assert any("Unresolved" in warning for warning in result.provenance.warnings)


@pytest.mark.parametrize(
    "npmrc",
    [
        "//registry.example/:_authToken=canary",
        "registry=https://user:canary@example.test",
        "registry=${PRIVATE_REGISTRY}",
        "registry=javascript:unsafe",
    ],
)
def test_credentials_and_dynamic_registries_fail_safely(tmp_path: Path, npmrc: str) -> None:
    manifest(tmp_path, {"fixture": "1.2.0"})
    (tmp_path / ".npmrc").write_text(npmrc)
    with pytest.raises(GappyError) as error:
        generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert "canary" not in str(error.value)


@pytest.mark.parametrize("release", [None, "invalid", "2024-01-01T00:00:00"])
def test_invalid_release_metadata_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, release: str | None
) -> None:
    manifest(tmp_path, {"fixture": "1.2.0"})
    data = metadata()
    data["time"]["1.2.1"] = release
    monkeypatch.setattr("gappy.inventory.fetch_registry", lambda *args: data)
    with pytest.raises(GappyError, match="timestamp"):
        generate(tmp_path, snapshot(), "gappy/fixture", 10)


def test_future_releases_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest(tmp_path, {"fixture": "1.2.0"})
    data = metadata()
    data["time"]["1.2.2"] = "9999-01-01T00:00:00Z"
    monkeypatch.setattr("gappy.inventory.fetch_registry", lambda *args: data)
    result = generate(tmp_path, snapshot(), "gappy/fixture", 10)
    assert [item.candidateVersion for item in result.opportunities] == ["1.2.1"]
    assert any("after inventory start" in warning for warning in result.provenance.warnings)


@pytest.mark.parametrize("filename", ["package.json", "package-lock.json"])
def test_malformed_files_are_errors(tmp_path: Path, filename: str) -> None:
    manifest(tmp_path, {"fixture": "^1.2.0"})
    (tmp_path / filename).write_text("{invalid")
    with pytest.raises(GappyError):
        generate(tmp_path, snapshot(), "gappy/fixture", 10)


def test_no_manifests_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GappyError, match="--inventory"):
        generate(tmp_path, snapshot(), "gappy/fixture", 10)


def test_real_local_http_metadata_and_errors() -> None:
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(metadata()).encode() if self.path != "/broken" else b"invalid"
            )

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        registry = f"http://127.0.0.1:{server.server_port}"
        assert fetch_registry(registry, "@scope/fixture", 2, None) == metadata()
        assert paths == ["/@scope%2Ffixture"]
        with pytest.raises(GappyError, match="lookup failed"):
            fetch_registry(registry, "broken", 2, None)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_cross_origin_registry_redirect_rejected() -> None:
    with pytest.raises(GappyError, match="across origins"):
        SameOriginRedirect().redirect_request(
            Request("https://registry.example/package"),
            None,
            302,
            "Found",
            {},
            "http://other.example/package",
        )


@pytest.mark.parametrize("compressed", [False, True])
def test_registry_metadata_larger_than_eight_mib(
    monkeypatch: pytest.MonkeyPatch, compressed: bool
) -> None:
    payload = json.dumps({**metadata(), "readme": "x" * (9 * 1024 * 1024)}).encode()
    requests = []

    class Response(io.BytesIO):
        headers = {"Content-Encoding": "gzip" if compressed else "identity"}

    class Opener:
        def open(self, request: Request, **kwargs: Any) -> Response:
            requests.append(request)
            return Response(gzip.compress(payload) if compressed else payload)

    monkeypatch.setattr("gappy.inventory.build_opener", lambda *args: Opener())
    result = fetch_registry("https://registry.example", "fixture", 2, None)
    assert len(result["readme"]) == 9 * 1024 * 1024
    assert requests[0].get_header("Accept-encoding") == "gzip"


@pytest.mark.parametrize("compressed", [False, True])
def test_registry_limit_applies_to_decoded_body(
    monkeypatch: pytest.MonkeyPatch, compressed: bool
) -> None:
    payload = json.dumps({"padding": "x" * 2048}).encode()

    class Response(io.BytesIO):
        headers = {"Content-Encoding": "gzip" if compressed else "identity"}

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> Response:
            return Response(gzip.compress(payload) if compressed else payload)

    monkeypatch.setattr("gappy.inventory.build_opener", lambda *args: Opener())
    monkeypatch.setattr("gappy.inventory.MAX_REGISTRY_BYTES", 1024)
    with pytest.raises(GappyError, match="fixture.*decoded inventory limit"):
        fetch_registry("https://registry.example", "fixture", 2, None)


def test_corrupt_gzip_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response(io.BytesIO):
        headers = {"Content-Encoding": "gzip"}

    class Opener:
        def open(self, *args: Any, **kwargs: Any) -> Response:
            return Response(b"not-gzip")

    monkeypatch.setattr("gappy.inventory.build_opener", lambda *args: Opener())
    with pytest.raises(GappyError, match="lookup failed"):
        fetch_registry("https://registry.example", "fixture", 2, None)
