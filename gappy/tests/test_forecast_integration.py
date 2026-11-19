"""Opt-in real four-scanner evaluation using only synthetic dependencies."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from gappy.forecast.cli import write_outputs
from gappy.forecast.collect import collect
from gappy.forecast.engine import forecast
from gappy.forecast.models import PlatformState, Window
from tests.test_scan import fixture_repo

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("limit", [1, 5])
def test_native_four_way_grouping_forecast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    url = os.environ.get("GAPPY_TEST_NEXUS_URL")
    certificate = os.environ.get("GAPPY_TEST_NEXUS_CA")
    if not url or not certificate:
        pytest.skip("Set GAPPY_TEST_NEXUS_URL and GAPPY_TEST_NEXUS_CA for local Nexus.")
    source = tmp_path / "source"
    fallback = tmp_path / "fallback"
    optimised = tmp_path / "optimised"
    fallback.mkdir()
    optimised.mkdir()
    fixture_repo(source)
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "gappy-forecast-fixture",
                "version": "1.0.0",
                "dependencies": {"is-number": "6.0.0", "is-odd": "2.0.0"},
            }
        )
    )
    (source / ".npmrc").write_text("registry=https://registry.npmjs.org/\n")
    dependabot = (
        "version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n"
        "    schedule:\n      interval: daily\n      time: '09:00'\n"
        f"    open-pull-requests-limit: {limit}\n"
    )
    (source / ".github/dependabot.yml").write_text(dependabot)
    (source / "renovate.json").write_text(
        json.dumps(
            {
                "enabledManagers": ["npm"],
                "prConcurrentLimit": limit,
            }
        )
    )
    # Repository configurations must win over these different fallback contents.
    (fallback / "dependabot.yml").write_text("version: 2\nupdates: []")
    (fallback / "renovate.json").write_text('{"enabled":false}')
    (optimised / "dependabot.yml").write_text(
        dependabot + "    groups:\n      all:\n        patterns: ['*']\n"
    )
    (optimised / "renovate.json").write_text(
        json.dumps(
            {
                "enabledManagers": ["npm"],
                "prConcurrentLimit": limit,
                "packageRules": [{"matchPackageNames": ["*"], "groupName": "all"}],
            }
        )
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
            "Synthetic four-way grouping",
        ],
        check=True,
        capture_output=True,
    )
    original = (source / "package.json").read_bytes()
    monkeypatch.setattr(
        "gappy.forecast.collect.capture",
        lambda *args: PlatformState(
            repository="gappy/fixture",
            capturedAt="2026-11-12T10:00:00Z",
            baseBranch="main",
            complete=True,
            pullRequests=[],
            warnings=[],
        ),
    )
    data = collect(
        source,
        "HEAD",
        "gappy/fixture",
        fallback,
        optimised,
        Window(start="2030-01-07T09:00:00Z", end="2030-01-08T09:00:00Z"),
        None,
        timeout=180,
        cache=False,
        mirror=False,
        registry_ca=Path(certificate),
        npm_mirror=url,
    )
    results = forecast(data)
    assert len(data.variants) == 4
    counts = {(v.provider, v.label): len(v.proposals) for v in data.variants}
    assert counts == {
        ("dependabot", "baseline"): 2,
        ("dependabot", "optimised"): 1,
        ("renovate", "baseline"): 2,
        ("renovate", "optimised"): 1,
    }
    assert all(v.scan.status == "complete" and v.proposalEvidenceComplete for v in data.variants)
    assert results["deltas"] == {
        "dependabot": {"potentialPRs": -1, "predictedNew": 0 if limit == 1 else -1},
        "renovate": {"potentialPRs": -1, "predictedNew": 0 if limit == 1 else -1},
    }
    for result in results["results"]:
        assert result["queued"] == (1 if limit == 1 and result["label"] == "baseline" else 0)
    assert all(v.configOrigin.startswith("repository:") for v in data.variants[:2])
    assert len({v.scan.snapshot.revision for v in data.variants}) == 1
    assert (source / "package.json").read_bytes() == original
    write_outputs(data, tmp_path)
    assert len(list(tmp_path.glob("*.scan.json"))) == 4
    assert json.loads((tmp_path / "forecast.json").read_text()) == results
    assert (tmp_path / "forecast.html").is_file()
