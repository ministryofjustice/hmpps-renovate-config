"""Verify the pinned Renovate binary and report with more than 100 direct dependencies."""

import json
import shutil
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.request import urlopen

import pytest

from gappy.compare.discovered import compare
from gappy.report.html import render
from gappy.scan.native import json_events
from gappy.scan.process import run
from gappy.scan.session import execute, toolchain
from tests.test_dependabot import snapshot
from tests.test_scan import fixture_repo

pytestmark = pytest.mark.integration


def test_real_renovate_and_report_retain_150_direct_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker must be available.")
    names = {f"gappy-scale-{index}" for index in range(150)}
    requests: set[str] = set()

    class Registry(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            name = self.path.lstrip("/")
            if name not in names:
                self.send_error(404)
                return
            requests.add(name)
            payload = json.dumps(
                {
                    "name": name,
                    "dist-tags": {"latest": "1.0.0"},
                    "time": {"1.0.0": "2024-01-01T00:00:00Z"},
                    "versions": {"1.0.0": {"name": name, "version": "1.0.0"}},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    cert, key = tmp_path / "ca.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=host.docker.internal",
            "-addext",
            "subjectAltName=DNS:host.docker.internal,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    with ThreadingHTTPServer(("127.0.0.1", 0), Registry) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            port = server.server_port
            url = f"https://host.docker.internal:{port}/"
            with urlopen(
                f"https://127.0.0.1:{port}/gappy-scale-0",
                context=ssl.create_default_context(cafile=str(cert)),
                timeout=5,
            ) as response:
                assert response.status == 200
            requests.clear()
            source = tmp_path / "source"
            fixture_repo(source)
            (source / "package.json").write_text(
                json.dumps(
                    {
                        "name": "gappy-scale-fixture",
                        "version": "1.0.0",
                        "dependencies": dict.fromkeys(sorted(names), "1.0.0"),
                    }
                )
            )
            (source / ".npmrc").write_text(f"registry={url}\n")
            (source / "renovate.json").write_text(
                json.dumps(
                    {
                        "enabledManagers": ["npm"],
                        "packageRules": [{"matchDatasources": ["npm"], "registryUrls": [url]}],
                    }
                )
            )
            captured = []

            def capture(args: list[str], **kwargs: Any) -> tuple[int, str]:
                code, output = run(args, **kwargs)
                if args[:2] == ["docker", "run"]:
                    captured.extend(json_events(output))
                return code, output

            monkeypatch.setattr("gappy.scan.session.run", capture)
            result = execute(
                "renovate",
                source,
                snapshot(),
                "scale",
                "gappy/scale-fixture",
                120,
                toolchain(),
                cert,
            )
            assert result.status == "complete", result.warnings
            native_names = {
                dep["depName"]
                for event in captured
                if event.get("msg") == "packageFiles with updates"
                for manifest in event["config"]["npm"]
                for dep in manifest["deps"]
            }
            assert native_names == names
            assert requests == names
            assert len(result.observations) == 150
            assert {item.dependency.package for item in result.observations} == names
            left = result.model_copy(update={"provider": "dependabot", "observations": []})
            report = compare(left, result)
            assert report["metrics"]["providers"]["renovate"]["discovered"] == 150
            html = render(report)
            assert "150 direct dependencies discovered" in html
            assert all(name in html for name in names)
        finally:
            server.shutdown()
            thread.join(timeout=10)
            assert not thread.is_alive()
