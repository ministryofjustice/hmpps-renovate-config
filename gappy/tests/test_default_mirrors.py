import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.mirror import (
    DEFAULT_DOCKER_MIRROR,
    DEFAULT_HELM_MIRROR,
    DEFAULT_NPM_MIRROR,
    DEFAULT_PYPI_MIRROR,
)
from gappy.scan.session import scan
from tests.test_compare import scan as fixture_scan
from tests.test_scan import fixture_repo


def test_default_docker_mirror_uses_tls_gateway_not_plain_http_connector() -> None:
    assert DEFAULT_DOCKER_MIRROR == "https://host.docker.internal:8444"


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("enabled", [False, True])
def test_cli_default_mirror_mode_and_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, enabled: bool
) -> None:
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(tmp_path, command, **({} if enabled else {"mirror": False}))
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
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
            *([] if enabled else ["--no-mirror"]),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_defaults_overrides_ca_and_disposable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, custom: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    fixture_repo(source)
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "renovate.json").write_text("{}")
    (configs / "dependabot.yml").write_text(
        "version: 2\nupdates:\n- package-ecosystem: npm\n"
        "  directory: /\n  schedule:\n    interval: weekly\n"
    )
    ca = configs / ".gappy-mirror" / "ca.crt"
    ca.parent.mkdir()
    ca.write_text("test certificate")
    calls = []

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
        calls.append((provider, frozen))
        mirrored = enabled or custom
        if mirrored:
            config = json.loads((root / "renovate.json").read_text())
            rules = {rule["matchDatasources"][0]: rule for rule in config["packageRules"]}
            npm = "https://custom.test/npm/" if custom else DEFAULT_NPM_MIRROR
            assert rules["npm"]["registryUrls"] == [npm]
            assert rules["pypi"]["registryUrls"] == [DEFAULT_PYPI_MIRROR]
            assert rules["docker"]["registryUrls"] == [DEFAULT_DOCKER_MIRROR]
            assert (root / ".npmrc").read_text().strip() == f"registry={npm}"
            assert all(
                f"gappy:{kind}-mirror" in frozen.configHashes
                for kind in ("npm", "pypi", "docker", "helm")
            )
            if provider == "dependabot":
                assert kwargs["pypi_mirror"] == DEFAULT_PYPI_MIRROR
                assert kwargs["docker_mirror"] == DEFAULT_DOCKER_MIRROR
        else:
            assert not any(key.startswith("gappy:") for key in frozen.configHashes)
        assert certificate == (ca if enabled or custom else None)
        return fixture_scan(provider, []).model_copy(update={"snapshot": frozen})

    (source / ".npmrc").write_text("registry=https://registry.npmjs.org/\n")
    subprocess.run(["git", "-C", str(source), "add", ".npmrc"], check=True, capture_output=True)
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
            "Public registry",
        ],
        check=True,
        capture_output=True,
    )
    original = {
        path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()
    }
    monkeypatch.setattr("gappy.scan.session.shutil.which", lambda name: "/fixture/" + name)
    monkeypatch.setattr("gappy.scan.session.run", native_run)
    monkeypatch.setattr("gappy.scan.session.execute", execute_scan)
    options: dict[str, Any] = {}
    if custom:
        options = {
            "npm_mirror": "https://custom.test/npm/",
            "ca": ca,
        }
        if not enabled:
            options.update(
                pypi_mirror=DEFAULT_PYPI_MIRROR,
                docker_mirror=DEFAULT_DOCKER_MIRROR,
                helm_mirror=(DEFAULT_HELM_MIRROR,),
            )
    results = scan(
        source, "HEAD", "gappy/fixture", 10, config_dir=configs, mirror=enabled, **options
    )
    assert [provider for provider, _ in calls] == ["dependabot", "renovate"]
    assert results[0].snapshot == results[1].snapshot
    assert all((source / path).read_bytes() == data for path, data in original.items())
    assert (configs / "renovate.json").read_text() == "{}"


def test_missing_default_ca_fails_before_scanning_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GappyError, match="--no-mirror"):
        scan(tmp_path, "HEAD", "gappy/fixture", 10)
    assert not list(tmp_path.rglob("*.scan.json"))
