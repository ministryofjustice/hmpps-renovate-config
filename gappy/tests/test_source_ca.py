import os
import ssl
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.session import scan, scanner_ca_bundle
from tests.test_compare import scan as fixture_scan
from tests.test_scan import fixture_repo


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    directory = tmp_path_factory.mktemp("source-ca")
    paths = (directory / "gateway.pem", directory / "source.pem")
    for path in paths:
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
                f"/CN={path.stem}",
                "-keyout",
                os.devnull,
                "-out",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    return paths


@pytest.mark.parametrize("gateway", [False, True])
def test_ca_bundle_adds_trust_without_replacing_gateway_or_modifying_inputs(
    tmp_path: Path, certificates: tuple[Path, Path], gateway: bool
) -> None:
    ca, source_ca = certificates
    before = [path.read_bytes() for path in certificates]
    target = tmp_path / "bundle.pem"
    assert scanner_ca_bundle(ca if gateway else None, source_ca, target) == target
    assert target.read_bytes() == b"\n".join(before if gateway else before[1:])
    ssl.create_default_context(cafile=str(target))
    assert [path.read_bytes() for path in certificates] == before


def test_no_source_ca_preserves_existing_ca_selection(tmp_path: Path) -> None:
    ca, target = tmp_path / "gateway.pem", tmp_path / "bundle.pem"
    assert scanner_ca_bundle(ca, None, target) == ca
    assert scanner_ca_bundle(None, None, target) is None
    assert not target.exists()


@pytest.mark.parametrize(
    "content", [b"", b"invalid", b"\xff", b"PRIVATE KEY", b"x" * (1024 * 1024 + 1)]
)
def test_invalid_or_secret_ca_is_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "source.pem"
    path.write_bytes(content)
    with pytest.raises(GappyError, match="CA"):
        scanner_ca_bundle(None, path, tmp_path / "bundle.pem")
    assert not (tmp_path / "bundle.pem").exists()


def test_missing_source_ca_is_not_silently_ignored(tmp_path: Path) -> None:
    with pytest.raises(GappyError, match="CA"):
        scanner_ca_bundle(None, tmp_path / "missing", tmp_path / "bundle.pem")


@pytest.mark.parametrize("command", ["scan", "full"])
def test_source_ca_cli_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    ca = tmp_path / "source.pem"
    ca.write_text("test certificate")
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(tmp_path, command, source_ca=ca)
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--source-ca",
            str(ca),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("automatic", [False, True])
def test_both_scanners_receive_combined_ca_outside_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    certificates: tuple[Path, Path],
    automatic: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    fixture_repo(source)
    ca, source_ca = certificates
    if automatic:
        local_ca = tmp_path / ".gappy-mirror" / "source-ca.crt"
        local_ca.parent.mkdir()
        local_ca.write_bytes(source_ca.read_bytes())
    seen = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        return (0, "dependabot version 1.94.0") if args[0] == "dependabot" else (0, "linux")

    def execute_scan(
        provider: str,
        root: Path,
        frozen: Any,
        session: str,
        repo: str,
        timeout: int,
        chain: Any,
        certificate: Path | None,
        **kwargs: Any,
    ) -> Any:
        assert certificate is not None
        assert certificate.parent == root.parent
        assert certificate.read_bytes() == ca.read_bytes() + b"\n" + source_ca.read_bytes()
        assert not list(root.rglob("trusted-ca.pem"))
        seen.append(certificate)
        return fixture_scan(provider, []).model_copy(update={"snapshot": frozen})

    monkeypatch.setattr("gappy.scan.session.shutil.which", lambda name: "/fixture/" + name)
    monkeypatch.setattr("gappy.scan.session.run", native_run)
    monkeypatch.setattr("gappy.scan.session.execute", execute_scan)
    results = scan(
        source,
        "HEAD",
        "gappy/fixture",
        10,
        ca,
        mirror=False,
        source_ca=None if automatic else source_ca,
    )
    assert len(seen) == 2 and seen[0] == seen[1]
    assert all(not path.exists() for path in seen)
    assert all(
        any("Additional source CA trust" in w for w in result.warnings) for result in results
    )


@pytest.mark.integration
def test_real_ghcr_uses_approved_source_ca(tmp_path: Path) -> None:
    value = os.environ.get("GAPPY_TEST_SOURCE_CA")
    if not value:
        pytest.skip("Set GAPPY_TEST_SOURCE_CA to verify GHCR behind corporate TLS inspection.")
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "Dockerfile").write_text(
        "FROM ghcr.io/ministryofjustice/hmpps-node:26-alpine-runtime\n"
    )
    (source / ".github/dependabot.yml").write_text(
        "version: 2\nupdates:\n- package-ecosystem: docker\n"
        "  directory: /\n  schedule:\n    interval: weekly\n"
    )
    (source / "renovate.json").write_text('{"enabledManagers":["dockerfile"]}')
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Synthetic public GHCR fixture",
        ],
        check=True,
        capture_output=True,
    )
    results = scan(
        source,
        "HEAD",
        "gappy/ghcr-fixture",
        180,
        cache=False,
        mirror=False,
        source_ca=Path(value),
    )
    for result in results:
        assert result.status == "complete", result.warnings
        assert result.observations
        assert not any("private_source_timed_out" in warning for warning in result.warnings)
