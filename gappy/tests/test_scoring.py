import json
from copy import deepcopy

import pytest

from gappy.compare.configurations import compare_configurations
from gappy.compare.scoring import score_configurations
from gappy.io import dump
from gappy.report.html import render
from tests.test_compare import scan
from tests.test_traffic import report_for


def test_score_rewards_visible_updates_not_green_rows() -> None:
    report = report_for(["1.3.0", None, "1.3.0", "1.3.0"])
    scoring = report["scoring"]
    assert scoring["winner"] is None
    assert len(scoring["leaders"]) == 3
    assert [item["score"] for item in scoring["variants"]] == [100, 100, 100, 80]
    assert [item["rank"] for item in scoring["variants"]] == [1, 1, 1, 4]
    output = render(report)
    assert "Overall optimisation score" in output
    assert "Joint highest observed-evidence score:" in output
    assert "80.00 / 100" in output
    assert render(json.loads(dump(report))) == output


def test_unique_winner_and_missing_discovery_use_shared_denominator() -> None:
    report = compare_configurations(
        (scan("dependabot", ["1.3.0", None]), scan("renovate", [None])),
        (scan("dependabot", [None]), scan("renovate", [None])),
    )
    scoring = report["scoring"]
    assert scoring["winner"] == "dependabot.baseline"
    assert scoring["variants"][0]["score"] == 100
    assert scoring["variants"][1]["score"] == 40
    assert scoring["variants"][1]["components"]["discovery"]["total"] == 2
    assert "Highest observed-evidence score: Dependabot / baseline" in render(report)


def test_no_targets_reweights_and_empty_evidence_is_not_perfect() -> None:
    scoring = report_for([None] * 4)["scoring"]
    assert scoring["weights"] == {"discovery": 50, "resolution": 50, "recommendations": 0}
    assert all(item["score"] == 100 for item in scoring["variants"])
    report = compare_configurations(
        (scan("dependabot", []), scan("renovate", [])),
        (scan("dependabot", []), scan("renovate", [])),
    )
    assert report["scoring"]["winner"] is None
    assert all(item["score"] is None for item in report["scoring"]["variants"])
    assert "Not assessable: no direct dependency evidence." in render(report)


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled", "unsupported"])
def test_incomplete_scans_prevent_winner_and_resolution_credit(status: str) -> None:
    report = report_for(["1.3.0", None, None, None])
    report["variants"][0]["scan"]["status"] = status
    report.pop("scoring")
    scoring = score_configurations(report["dependencies"], report["variants"])
    assert scoring["provisional"]
    assert scoring["winner"] is None
    item = next(item for item in scoring["variants"] if item["id"] == "dependabot.baseline")
    assert item["components"]["resolution"]["points"] == 0
    assert "Provisional ranking" in render(report)


@pytest.mark.parametrize("policy", [["unknown"], ["excluded"], ["eligible", "deferred"], []])
def test_unresolved_policies_do_not_earn_resolution_credit(policy: list[str]) -> None:
    report = report_for([None] * 4)
    report["dependencies"][0]["variants"]["dependabot.baseline"]["policy"] = policy
    scoring = score_configurations(report["dependencies"], report["variants"])
    item = next(item for item in scoring["variants"] if item["id"] == "dependabot.baseline")
    assert item["score"] == 50


def test_deferred_targets_receive_equal_credit_without_mutating_inputs() -> None:
    report = report_for(["1.3.0"] * 4)
    side = report["dependencies"][0]["variants"]["dependabot.baseline"]
    side.update(policy=["deferred"], lookupComplete=False)
    before = deepcopy(report)
    scoring = score_configurations(report["dependencies"], report["variants"])
    assert all(item["score"] == 100 for item in scoring["variants"])
    assert report == before


@pytest.mark.parametrize("field", ["installedVersions", "lookupComplete"])
def test_missing_current_version_or_lookup_prevents_resolution(field: str) -> None:
    report = report_for([None] * 4)
    report["dependencies"][0]["variants"]["dependabot.baseline"][field] = (
        [] if field == "installedVersions" else False
    )
    scoring = score_configurations(report["dependencies"], report["variants"])
    item = next(item for item in scoring["variants"] if item["id"] == "dependabot.baseline")
    assert item["score"] == 50
