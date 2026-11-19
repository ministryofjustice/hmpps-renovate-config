"""Controlled npm registry for scanner feasibility; serves only synthetic package data."""

import argparse
import hashlib
import io
import json
import ssl
import tarfile
import zipfile
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml


def archive(version: str, package: str = "gappy-fixture") -> bytes:
    data = json.dumps({"name": package, "version": version}).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return stream.getvalue()


ARCHIVES = {version: archive(version) for version in ("1.2.0", "1.2.1", "1.2.2")}
LEAF_ARCHIVE = archive("1.0.0", "gappy-test-indirect-leaf")
METADATA = {
    "name": "gappy-fixture",
    "dist-tags": {"latest": "1.2.2"},
    "time": {
        "created": "2024-01-01T00:00:00Z",
        "modified": "2024-01-03T00:00:00Z",
        "1.2.0": "2024-01-01T00:00:00Z",
        "1.2.1": "2024-01-02T00:00:00Z",
        "1.2.2": "2024-01-03T00:00:00Z",
    },
    "versions": {
        version: {
            "name": "gappy-fixture",
            "version": version,
            "dist": {
                "tarball": f"https://host.docker.internal:18947/gappy-fixture/-/{version}.tgz",
                "shasum": hashlib.sha1(data).hexdigest(),
            },
        }
        for version, data in ARCHIVES.items()
    },
}


def wheel(version: str) -> bytes:
    stream = io.BytesIO()
    directory = f"gappy_fixture-{version}.dist-info"
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            f"{directory}/METADATA",
            f"Metadata-Version: 2.1\nName: gappy-fixture\nVersion: {version}\n\n",
        )
        archive.writestr(
            f"{directory}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        )
        archive.writestr(f"{directory}/RECORD", "")
    return stream.getvalue()


WHEELS = {version: wheel(version) for version in ARCHIVES}


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        extra_headers = {}
        if self.path.rstrip("/") == "/simple/gappy-fixture":
            payload = (
                "<html><body>"
                + "".join(
                    f'<a href="/packages/gappy_fixture-{version}-py3-none-any.whl">'
                    f"gappy_fixture-{version}-py3-none-any.whl</a>"
                    for version in WHEELS
                )
                + "</body></html>"
            ).encode()
            content_type = "text/html"
        elif self.path.startswith("/packages/gappy_fixture-"):
            version = self.path.split("gappy_fixture-", 1)[1].split("-", 1)[0]
            if version not in WHEELS:
                self.send_error(404)
                return
            payload = WHEELS[version]
            content_type = "application/zip"
        elif self.path == "/pypi/gappy-fixture/json":
            payload = json.dumps(
                {
                    "info": {"name": "gappy-fixture", "version": "1.2.2"},
                    "releases": {
                        version: [
                            {
                                "filename": f"gappy_fixture-{version}-py3-none-any.whl",
                                "url": f"https://host.docker.internal:18947/packages/"
                                f"gappy_fixture-{version}-py3-none-any.whl",
                                "upload_time": "2024-01-03T00:00:00",
                                "upload_time_iso_8601": "2024-01-03T00:00:00Z",
                                "yanked": False,
                                "packagetype": "bdist_wheel",
                            }
                        ]
                        for version in WHEELS
                    },
                }
            ).encode()
            content_type = "application/json"
        elif self.path.rstrip("/") == "/v2":
            payload = b"{}"
            content_type = "application/json"
        elif self.path.startswith("/v2/") and "/tags/list" in self.path:
            payload = json.dumps(
                {
                    "name": "gappy-fixture",
                    "tags": list(ARCHIVES),
                }
            ).encode()
            content_type = "application/json"
        elif self.path.startswith("/v2/") and "/manifests/" in self.path:
            tag = self.path.rsplit("/", 1)[-1]
            config = json.dumps(
                {
                    "architecture": "amd64",
                    "os": "linux",
                    "created": "2024-01-03T00:00:00Z",
                }
            ).encode()
            payload = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                    "config": {
                        "mediaType": "application/vnd.docker.container.image.v1+json",
                        "size": len(config),
                        "digest": "sha256:" + hashlib.sha256(config).hexdigest(),
                    },
                    "layers": [],
                    "annotations": {"org.opencontainers.image.version": tag},
                }
            ).encode()
            content_type = "application/vnd.docker.distribution.manifest.v2+json"
            extra_headers["Docker-Content-Digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
        elif self.path.startswith("/v2/") and "/blobs/" in self.path:
            payload = json.dumps(
                {
                    "architecture": "amd64",
                    "os": "linux",
                    "created": "2024-01-03T00:00:00Z",
                }
            ).encode()
            content_type = "application/json"
        elif self.path == "/charts/index.yaml":
            payload = yaml.safe_dump(
                {
                    "apiVersion": "v1",
                    "entries": {
                        "gappy-fixture": [
                            {
                                "apiVersion": "v2",
                                "name": "gappy-fixture",
                                "version": version,
                                "created": METADATA["time"][version],
                                "urls": [
                                    f"https://host.docker.internal:18947/charts/{version}.tgz"
                                ],
                            }
                            for version in ARCHIVES
                        ]
                    },
                }
            ).encode()
            content_type = "application/yaml"
        elif self.path.startswith("/charts/") and self.path.endswith(".tgz"):
            version = self.path.rsplit("/", 1)[-1].removesuffix(".tgz")
            if version not in ARCHIVES:
                self.send_error(404)
                return
            stream = io.BytesIO()
            data = yaml.safe_dump(
                {
                    "apiVersion": "v2",
                    "name": "gappy-fixture",
                    "version": version,
                }
            ).encode()
            with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                info = tarfile.TarInfo("gappy-fixture/Chart.yaml")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            payload = stream.getvalue()
            content_type = "application/gzip"
        elif self.path.rstrip("/") == "/gappy-test-indirect-leaf":
            payload = json.dumps(
                {
                    "name": "gappy-test-indirect-leaf",
                    "dist-tags": {"latest": "1.0.0"},
                    "time": {"1.0.0": "2024-01-01T00:00:00Z"},
                    "versions": {
                        "1.0.0": {
                            "name": "gappy-test-indirect-leaf",
                            "version": "1.0.0",
                            "dist": {
                                "tarball": "https://host.docker.internal:18947/"
                                "gappy-test-indirect-leaf/-/1.0.0.tgz",
                                "shasum": hashlib.sha1(LEAF_ARCHIVE).hexdigest(),
                            },
                        }
                    },
                }
            ).encode()
            content_type = "application/json"
        elif self.path == "/gappy-test-indirect-leaf/-/1.0.0.tgz":
            payload = LEAF_ARCHIVE
            content_type = "application/octet-stream"
        elif self.path.rstrip("/") == "/gappy-fixture":
            payload = json.dumps(METADATA).encode()
            content_type = "application/json"
        elif self.path.startswith("/gappy-fixture/-/"):
            version = self.path.rsplit("/", 1)[-1].removesuffix(".tgz")
            if version not in ARCHIVES:
                self.send_error(404)
                return
            payload = ARCHIVES[version]
            content_type = "application/octet-stream"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=300")
        for key, value in extra_headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--recent", action="store_true")
    args = parser.parse_args()
    if args.recent:
        for version in ("1.2.1", "1.2.2"):
            METADATA["time"][version] = datetime.now(UTC).isoformat()
    server = HTTPServer(("127.0.0.1", 18947), Handler)
    if args.cert and args.key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
