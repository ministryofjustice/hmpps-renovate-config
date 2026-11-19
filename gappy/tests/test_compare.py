import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from gappy.cli import main
from gappy.compare.assess import compare
from gappy.contracts import Inventory, Scan
from gappy.io import GappyError, dump, preflight, publish
from gappy.report.html import render

HASH = "a" * 64


def dependency(name: str = "fixture") -> dict[str, Any]:
    return {
        "ecosystem": "npm",
        "package": name,
        "manifestPath": "package.json",
        "installedVersion": "1.2.0",
        "versioning": "semver",
        "registry": "https://registry.npmjs.org",
    }


def scan(provider: str, targets: list[str | None]) -> Scan:
    return Scan.model_validate(
        {
            "schemaVersion": "1.1.0",
            "provider": provider,
            "sessionId": "same-session",
            "repository": "gappy/fixture",
            "snapshot": {"revision": "b" * 40, "contentHash": HASH, "configHashes": {}},
            "toolchain": {"version": "fixture"},
            "startedAt": "2026-01-02T00:00:00Z",
            "finishedAt": "2026-01-02T00:01:00Z",
            "cacheMode": "fresh",
            "status": "complete",
            "warnings": [],
            "observations": [
                {
                    "dependency": dependency(str(i)),
                    "directness": "direct",
                    "policy": "eligible",
                    "reason": "fixture",
                    "lookupComplete": True,
                    "candidates": (
                        [{"targetVersion": target, "evidenceId": f"{provider}-{i}"}]
                        if target
                        else []
                    ),
                }
                for i, target in enumerate(targets)
            ],
        }
    )


def inventory(count: int) -> Inventory:
    return Inventory.model_validate(
        {
            "schemaVersion": "1.1.0",
            "repository": "gappy/fixture",
            "snapshotHash": HASH,
            "asOf": "2026-01-02T00:00:00Z",
            "provenance": {"source": "synthetic", "complete": False},
            "opportunities": [
                {
                    "dependency": dependency(str(i)),
                    "directness": "direct",
                    "candidateVersion": "1.2.1",
                    "availableAt": "2026-01-01T00:00:00Z",
                }
                for i in range(count)
            ],
        }
    )


def test_required_denominator_and_winner() -> None:
    left = scan("dependabot", ["1.2.1", "1.2.2", "1.2.1", None])
    right = scan("renovate", ["1.2.1", "1.2.1", None, None])
    report = compare(left, right, inventory(4))
    metrics = report["metrics"]
    assert metrics["detections"] == {"dependabot": 3, "renovate": 2}
    assert metrics["denominator"] == 4
    assert metrics["coverage"] == {"dependabot": 0.75, "renovate": 0.5}
    assert metrics["winner"] == "dependabot"
    assert {row["result"] for row in report["assessments"]} == {
        "both",
        "dependabot-only",
        "neither",
    }
    assert dump(report) == dump(compare(left, right, inventory(4)))
    assert render(report) == render(compare(left, right, inventory(4)))
    assert "3 / 4" in render(report)


def test_legacy_inventory_reports_only_proven_direct_opportunities() -> None:
    data = inventory(4).model_dump()
    data["opportunities"][1]["directness"] = "transitive"
    data["opportunities"][2]["directness"] = "unknown"
    data["opportunities"][3]["directness"] = "unknown"
    left = scan("dependabot", ["1.2.1", "1.2.1", "1.2.1"])
    report = compare(left, scan("renovate", []), Inventory.model_validate(data))

    assert [row["dependency"]["package"] for row in report["assessments"]] == ["0", "2"]
    assert report["metrics"]["opportunities"] == 2
    assert report["metrics"]["excludedOpportunities"] == {"transitive": 1, "unknown": 1}
    assert any("unclassified" in warning for warning in report["warnings"])
    assert len(report["inventory"]["opportunities"]) == 4


def test_legacy_inventory_does_not_count_transitive_scan_evidence_as_direct() -> None:
    left = scan("dependabot", ["1.2.1"])
    left = left.model_copy(
        update={
            "observations": [left.observations[0].model_copy(update={"directness": "transitive"})]
        }
    )
    report = compare(left, scan("renovate", []), inventory(1))
    assert report["assessments"][0]["dependabot"]["detection"] == "unknown"


@pytest.mark.parametrize(
    ("left_target", "right_target", "result", "winner"),
    [
        ("1.2.2", None, "dependabot-only", "dependabot"),
        (None, "1.2.1", "renovate-only", "renovate"),
        ("1.2.1", "1.2.1", "both", "tie"),
        (None, None, "neither", "tie"),
        ("1.3.0", None, "not-assessable", "insufficient-evidence"),
    ],
)
def test_outcomes(
    left_target: str | None, right_target: str | None, result: str, winner: str
) -> None:
    report = compare(
        scan("dependabot", [left_target]), scan("renovate", [right_target]), inventory(1)
    )
    assert report["assessments"][0]["result"] == result
    assert report["metrics"]["winner"] == winner


def test_unknown_does_not_inflate_denominator() -> None:
    left = scan("dependabot", ["1.2.2"] * 3)
    right = scan("renovate", ["1.2.2", None, None])
    observations = list(right.observations)
    observations[1] = observations[1].model_copy(update={"policy": "excluded"})
    observations[2] = observations[2].model_copy(update={"lookupComplete": False})
    right = right.model_copy(update={"observations": observations})
    report = compare(left, right, inventory(3))
    assert report["metrics"]["jointlyEligible"] == 2
    assert report["metrics"]["denominator"] == 1
    assert report["metrics"]["excludedFromPaired"] == 1
    assert report["assessments"][1]["winner"] == "dependabot"
    assert report["assessments"][2]["result"] == "not-assessable"


def test_future_availability_is_not_a_miss() -> None:
    baseline = inventory(1).model_dump()
    baseline["asOf"] = "2026-01-03T00:00:00Z"
    baseline["opportunities"][0]["availableAt"] = "2026-01-02T00:00:30Z"
    report = compare(
        scan("dependabot", [None]), scan("renovate", [None]), Inventory.model_validate(baseline)
    )
    assert report["metrics"]["winner"] == "insufficient-evidence"


@pytest.mark.parametrize("status", ["failed", "partial", "cancelled", "unsupported"])
def test_non_success_is_not_negative_evidence(status: str) -> None:
    left = scan("dependabot", [None]).model_copy(update={"status": status, "warnings": ["fixture"]})
    report = compare(left, scan("renovate", [None]), inventory(1))
    assert report["assessments"][0]["dependabot"]["detection"] == "unknown"


def test_empty_is_not_zero_percent_tie() -> None:
    report = compare(scan("dependabot", []), scan("renovate", []), inventory(0))
    assert report["metrics"]["coverage"] == {"dependabot": None, "renovate": None}
    assert report["metrics"]["winner"] == "insufficient-evidence"


def test_html_bootstrap_and_confirmed_gap_map() -> None:
    report = compare(
        scan("dependabot", ["1.2.1", None]),
        scan("renovate", [None, None]),
        inventory(2),
    )
    output = render(report)
    assert "Bootstrap  v5.3.8" in output
    assert "--bs-primary" in output
    assert 'class="table-responsive"' in output
    assert "Patch-by-patch gap map" in output
    assert "Confirmed miss" in output
    assert "Investigate missed patch: Renovate." in output
    assert "Investigate missed patch: Dependabot, Renovate." in output
    assert "Dependabot leads on the assessed subset" in output
    assert "100.0%" not in output
    assert "50.0%" in output
    assert "<script" not in output
    assert "<link " not in output


def test_html_preserves_identification_when_paired_coverage_is_blocked() -> None:
    left = scan("dependabot", []).model_copy(
        update={"status": "unsupported", "warnings": ["Groups are not supported."]}
    )
    right = scan("renovate", ["1.2.2"])
    right = right.model_copy(
        update={
            "status": "partial",
            "observations": [
                item.model_copy(update={"policy": "unknown", "lookupComplete": False})
                for item in right.observations
            ],
        }
    )
    output = render(compare(left, right, inventory(1)))
    assert "No fair winner can be determined" in output
    assert "Unavailable" in output
    assert "Not assessable" in output
    assert "Unknown — evidence gap" in output
    assert "Patch identified" in output
    assert "Groups are not supported." in output
    assert "Confirmed miss</span>" not in output
    assert "Investigate missed patch:" not in output
    assert "0 / 0" not in output


def test_html_distinguishes_policy_from_detection_failure() -> None:
    left = scan("dependabot", [None])
    left = left.model_copy(
        update={
            "observations": [left.observations[0].model_copy(update={"policy": "deferred"})],
        }
    )
    output = render(compare(left, scan("renovate", ["1.2.1"]), inventory(1)))
    assert "Policy: deferred" in output
    assert "not a detection failure" in output
    assert "Confirmed miss</span>" not in output


def test_identity_mismatch() -> None:
    right = scan("renovate", []).model_copy(update={"sessionId": "other"})
    with pytest.raises(GappyError, match="identities"):
        compare(scan("dependabot", []), right, inventory(0))


def test_deduplicate_inventory_and_isolate_context() -> None:
    baseline = inventory(1).model_dump()
    baseline["opportunities"].append(baseline["opportunities"][0])
    assert (
        len(
            compare(
                scan("dependabot", [None]),
                scan("renovate", [None]),
                Inventory.model_validate(baseline),
            )["assessments"]
        )
        == 1
    )
    baseline["opportunities"][0]["dependency"]["manifestPath"] = "other/package.json"
    report = compare(
        scan("dependabot", [None]), scan("renovate", [None]), Inventory.model_validate(baseline)
    )
    assert report["assessments"][0]["result"] == "not-assessable"


@pytest.mark.parametrize("value", ["not-a-date", "2026-01-01T00:00:00"])
def test_bad_dates(value: str) -> None:
    baseline = inventory(1).model_dump()
    baseline["opportunities"][0]["availableAt"] = value
    with pytest.raises(ValidationError):
        Inventory.model_validate(baseline)


def test_strict_schema_and_paths() -> None:
    baseline = inventory(1).model_dump()
    baseline["opportunities"][0]["dependency"]["manifestPath"] = "../outside"
    with pytest.raises(ValidationError):
        Inventory.model_validate(baseline)
    baseline = inventory(0).model_dump()
    baseline["provenance"]["complete"] = "true"
    with pytest.raises(ValidationError):
        Inventory.model_validate(baseline)


def test_html_escaping() -> None:
    report = compare(scan("dependabot", []), scan("renovate", []), inventory(0))
    report["repository"] = '<script>alert("unsafe")</script>'
    output = render(report)
    assert "<script>" not in output
    assert "&lt;script&gt;" in output
    assert "<script src" not in output


def test_publication_refuses_aliases_and_existing_outputs(tmp_path: Path) -> None:
    existing = tmp_path / "input"
    existing.write_text("unchanged")
    alias = tmp_path / "alias"
    alias.symlink_to(existing)
    with pytest.raises(GappyError):
        preflight([alias], [existing])
    with pytest.raises(GappyError):
        preflight([tmp_path / "out", tmp_path / "out"], [])
    with pytest.raises(GappyError):
        publish({existing: "changed"})
    assert existing.read_text() == "unchanged"


def test_offline_cli_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/no-docker-or-scanners")
    paths = [tmp_path / name for name in ("left.json", "right.json", "inventory.json")]
    for path, model in zip(
        paths, (scan("dependabot", ["1.2.2"]), scan("renovate", [None]), inventory(1)), strict=True
    ):
        path.write_text(dump(model.model_dump()))
    args = [
        "compare",
        "--dependabot-json",
        str(paths[0]),
        "--renovate-json",
        str(paths[1]),
        "--inventory",
        str(paths[2]),
        "--json",
        str(tmp_path / "result.json"),
        "--html",
        str(tmp_path / "result.html"),
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads((tmp_path / "result.json").read_text())["metrics"]["winner"] == "dependabot"
    assert "dependabot-only" in (tmp_path / "result.html").read_text()
    assert CliRunner().invoke(main, args).exit_code == 1


@pytest.mark.parametrize("version", ["1.3.0", "2.0.0", "1.2.1-beta.1", "sha256:abc", "latest"])
def test_unsupported_candidate_is_not_a_patch(version: str) -> None:
    baseline = inventory(1).model_dump()
    baseline["opportunities"][0]["candidateVersion"] = version
    report = compare(
        scan("dependabot", ["1.2.2"]),
        scan("renovate", ["1.2.2"]),
        Inventory.model_validate(baseline),
    )
    assert report["assessments"][0]["result"] == "not-assessable"


def test_registry_identity_is_part_of_context() -> None:
    baseline = inventory(1).model_dump()
    baseline["opportunities"][0]["dependency"]["registry"] = "https://other.example"
    report = compare(
        scan("dependabot", ["1.2.2"]),
        scan("renovate", ["1.2.2"]),
        Inventory.model_validate(baseline),
    )
    assert report["assessments"][0]["result"] == "not-assessable"


def test_equivalent_timezone_duplicates_count_once() -> None:
    baseline = inventory(1).model_dump()
    duplicate = {**baseline["opportunities"][0], "availableAt": "2026-01-01T01:00:00+01:00"}
    baseline["opportunities"].append(duplicate)
    report = compare(
        scan("dependabot", ["1.2.2"]),
        scan("renovate", ["1.2.2"]),
        Inventory.model_validate(baseline),
    )
    assert report["metrics"]["denominator"] == 1
