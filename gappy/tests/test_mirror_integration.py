"""Opt-in proof against the local persistent Nexus service; never removes its data."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from gappy.scan.dependabot import jobs
from gappy.scan.mirror import DEFAULT_HELM_UPSTREAM, apply_npm_mirror
from gappy.scan.session import execute, scan, toolchain
from gappy.scan.snapshot import freeze
from tests.test_scan import fixture_repo

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("aliases_enabled", "installed"), [(False, "7.0.0"), (True, "7.0.0"), (True, "6.0.0")]
)
def test_native_npm_aliases_do_not_become_mirror_authentication_errors(
    tmp_path: Path, aliases_enabled: bool, installed: str
) -> None:
    url = os.environ.get("GAPPY_TEST_NEXUS_URL")
    certificate = os.environ.get("GAPPY_TEST_NEXUS_CA")
    if not url or not certificate:
        pytest.skip("Set GAPPY_TEST_NEXUS_URL and GAPPY_TEST_NEXUS_CA for the local Nexus service.")
    source = tmp_path / "source"
    fixture_repo(source)
    manifest = {
        "name": "gappy-alias-fixture",
        "version": "1.0.0",
        "dependencies": {"gappy-fixture-alias": f"npm:is-number@{installed}"},
    }
    (source / "package.json").write_text(json.dumps(manifest))
    (source / "package-lock.json").write_text(
        json.dumps(
            {
                "name": manifest["name"],
                "version": "1.0.0",
                "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "": manifest,
                    "node_modules/gappy-fixture-alias": {
                        "name": "is-number",
                        "version": installed,
                        "resolved": f"https://registry.npmjs.org/is-number/-/is-number-{installed}.tgz",
                    },
                },
            }
        )
    )
    (source / ".npmrc").write_text("registry=https://registry.npmjs.org/\n")
    (source / "renovate.json").write_text('{"enabledManagers":["npm"]}')
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
            "Synthetic npm alias fixture",
        ],
        check=True,
        capture_output=True,
    )
    original_lock = (source / "package-lock.json").read_bytes()
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    snapshot = apply_npm_mirror(frozen, freeze(source, "HEAD", frozen), url)
    if not aliases_enabled:
        from unittest.mock import patch

        def legacy_jobs(*args: Any, **kwargs: Any) -> Any:
            planned, warnings = jobs(*args, **kwargs)
            for job in planned:
                job.definition.pop("experiments", None)
            return planned, warnings

        with patch("gappy.scan.session.jobs", legacy_jobs):
            result = execute(
                "dependabot",
                frozen,
                snapshot,
                "alias-fixture",
                "gappy/fixture",
                120,
                toolchain(),
                Path(certificate),
            )
        assert result.status == "partial", result.warnings
        assert any("private_source_authentication_failure" in w for w in result.warnings)
        assert any(o.dependency.package == "gappy-fixture-alias" for o in result.observations)
    else:
        result = execute(
            "dependabot",
            frozen,
            snapshot,
            "alias-fixture",
            "gappy/fixture",
            120,
            toolchain(),
            Path(certificate),
        )
        assert result.status == "complete", result.warnings
        assert {o.dependency.package for o in result.observations} == {"is-number"}
        assert all(o.lookupComplete for o in result.observations)
        assert any("native alias resolution enabled" in w for w in result.warnings)
        if installed == "6.0.0":
            assert [
                candidate.targetVersion
                for observation in result.observations
                for candidate in observation.candidates
            ] == ["7.0.0"]
    assert (source / "package-lock.json").read_bytes() == original_lock


@pytest.mark.parametrize("default_mirrors", [False, True])
def test_both_scanners_use_nexus_without_changing_source(
    tmp_path: Path, default_mirrors: bool
) -> None:
    url = os.environ.get("GAPPY_TEST_NEXUS_URL")
    certificate = os.environ.get("GAPPY_TEST_NEXUS_CA")
    if not url or not certificate:
        pytest.skip("Set GAPPY_TEST_NEXUS_URL and GAPPY_TEST_NEXUS_CA for the local Nexus service.")
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "gappy-nexus-fixture",
                "version": "1.0.0",
                "dependencies": {"is-number": "6.0.0"},
            }
        )
    )
    (source / ".npmrc").write_text("registry=https://registry.npmjs.org/\n")
    (source / "renovate.json").write_text('{"enabledManagers":["npm"]}')
    (source / ".github/dependabot.yml").write_text(
        "version: 2\nupdates:\n  - package-ecosystem: npm\n"
        "    directory: /\n    schedule:\n      interval: weekly\n"
    )
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
            "Synthetic Nexus fixture",
        ],
        check=True,
        capture_output=True,
    )
    options: dict[str, Any] = {} if default_mirrors else {"mirror": False, "npm_mirror": url}
    for result in scan(
        source,
        "HEAD",
        "gappy/nexus-fixture",
        180,
        Path(certificate),
        cache=False,
        **options,
    ):
        assert result.status == "complete", result.warnings
        dependency = next(o for o in result.observations if o.dependency.package == "is-number")
        assert dependency.dependency.registry == url.rstrip("/")
        assert [candidate.targetVersion for candidate in dependency.candidates] == ["7.0.0"]
        assert "gappy:npm-mirror" in result.snapshot.configHashes
    assert (source / ".npmrc").read_text() == "registry=https://registry.npmjs.org/\n"


@pytest.mark.parametrize("ecosystem", ["pip", "docker", "helm"])
def test_additional_nexus_mirrors(tmp_path: Path, ecosystem: str) -> None:
    variable = {"pip": "PYPI", "docker": "DOCKER", "helm": "HELM"}[ecosystem]
    url = os.environ.get(f"GAPPY_TEST_NEXUS_{variable}_URL")
    certificate = os.environ.get("GAPPY_TEST_NEXUS_CA")
    if not url or not certificate:
        pytest.skip(
            f"Set GAPPY_TEST_NEXUS_{variable}_URL and GAPPY_TEST_NEXUS_CA "
            "after provisioning the Nexus repository."
        )
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "package.json").unlink()
    options: dict[str, Any] = {}
    if ecosystem == "pip":
        (source / "requirements.txt").write_text("idna==3.10\n")
        manager, package = "pip_requirements", "idna"
        options["pypi_mirror"] = url
    elif ecosystem == "docker":
        (source / "Dockerfile").write_text("FROM library/alpine:3.20.0\n")
        manager, package = "dockerfile", "alpine"
        options["docker_mirror"] = url
    else:
        upstream = os.environ.get("GAPPY_TEST_NEXUS_HELM_UPSTREAM", DEFAULT_HELM_UPSTREAM)
        (source / "Chart.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "v2",
                    "name": "application",
                    "version": "0.1.0",
                    "dependencies": [
                        {"name": "generic-service", "version": "3.17.0", "repository": upstream}
                    ],
                }
            )
        )
        manager, package = "helmv3", "generic-service"
        options["helm_mirror"] = (f"{upstream}={url}",)
    (source / "renovate.json").write_text(json.dumps({"enabledManagers": [manager]}))
    (source / ".github/dependabot.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "updates": [
                    {
                        "package-ecosystem": ecosystem,
                        "directory": "/",
                        "schedule": {"interval": "weekly"},
                    }
                ],
            }
        )
    )
    originals = {
        path.relative_to(source): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }
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
            "Synthetic multi-registry Nexus fixture",
        ],
        check=True,
        capture_output=True,
    )
    left, right = scan(
        source,
        "HEAD",
        "gappy/nexus-fixture",
        300,
        Path(certificate),
        cache=False,
        mirror=False,
        **options,
    )
    assert left.snapshot == right.snapshot
    for result in (left, right):
        if ecosystem == "pip" and result.provider == "dependabot":
            assert result.status == "partial"
            assert any("unexpected_external_code" in warning for warning in result.warnings)
        else:
            assert result.status == "complete", result.warnings
            assert any(
                package in observation.dependency.package and observation.candidates
                for observation in result.observations
            ), result.observations
    assert all((source / path).read_bytes() == content for path, content in originals.items())
