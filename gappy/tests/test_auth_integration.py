"""Opt-in real scanner authentication proof; uses no credentials unless explicitly supplied."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from gappy.io import dump
from gappy.scan.auth import github_token
from gappy.scan.session import scan
from tests.test_scan import fixture_repo

pytestmark = pytest.mark.integration


def test_real_github_authentication(tmp_path: Path) -> None:
    if "GAPPY_TEST_GITHUB_TOKEN" not in os.environ:
        pytest.skip("Set GAPPY_TEST_GITHUB_TOKEN explicitly to test real read-only GitHub access.")
    token = github_token("GAPPY_TEST_GITHUB_TOKEN")
    assert token is not None
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "package.json").unlink()
    workflow = source / ".github/workflows/test.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "name: Fixture\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v3\n"
    )
    (source / ".github/dependabot.yml").write_text(
        "version: 2\nupdates:\n  - package-ecosystem: github-actions\n"
        "    directory: /\n    schedule:\n      interval: weekly\n"
    )
    (source / "renovate.json").write_text(json.dumps({"enabledManagers": ["github-actions"]}))
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
            "Synthetic authenticated GitHub Actions fixture",
        ],
        check=True,
        capture_output=True,
    )
    for result in scan(
        source, "HEAD", "gappy/auth-fixture", 180, cache=False, github_token=token, mirror=False
    ):
        # Avoid rendering a failing export/exception with a credential-bearing repr.
        if token in dump(result.model_dump()):
            pytest.fail("Authentication secret appeared in the scan export.", pytrace=False)
        assert result.status == "complete", result.warnings
        assert any(
            item.dependency.package == "actions/checkout" and item.candidates
            for item in result.observations
        )
    assert workflow.read_text().endswith("      - uses: actions/checkout@v3\n")
