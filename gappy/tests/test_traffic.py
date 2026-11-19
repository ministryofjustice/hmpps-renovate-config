from copy import deepcopy
from typing import Any

import pytest

from gappy.compare.configurations import compare_configurations
from gappy.report.html import render
from gappy.report.traffic import traffic_lights
from tests.test_compare import scan


def report_for(targets: list[str | None]) -> dict[str, Any]:
    models = [
        scan(provider, [target])
        for provider, target in zip(
            ("dependabot", "renovate", "dependabot", "renovate"), targets, strict=True
        )
    ]
    return compare_configurations((models[0], models[1]), (models[2], models[3]))


def tones(report: dict[str, Any]) -> list[str]:
    return [v[0] for v in traffic_lights(report["dependencies"][0], report["variants"]).values()]


def test_updates_are_red_and_no_update_is_green_regardless_of_peer_targets() -> None:
    report = report_for(["1.9.0", "1.10.0", "v1.10.0", None])
    assert tones(report) == ["danger", "danger", "danger", "success"]
    output = render(report)
    assert output.count('class="table-success"') == 1
    assert output.count('class="table-danger"') == 3
    assert "Out of date — update recommended" in output
    assert "Green: up to date" in output
    assert "Yellow: no applicable policy or unknown result" in output
    assert "Red: out of date; update recommended" in output
    assert "Highest comparable recommendation" not in output


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled", "unsupported"])
def test_incomplete_results_never_get_green_or_red(status: str) -> None:
    report = report_for(["1.9.0", None, "99.0.0", None])
    report["variants"][2]["scan"]["status"] = status
    report["variants"][3]["scan"]["status"] = status
    assert tones(report) == ["danger", "success", "warning", "warning"]


@pytest.mark.parametrize("policy", [[], ["excluded"], ["unknown"], ["eligible", "excluded"]])
def test_no_eligible_policy_is_yellow(policy: list[str]) -> None:
    report = report_for(["1.9.0", None, "99.0.0", None])
    row = report["dependencies"][0]
    for key in ("dependabot.optimised", "renovate.optimised"):
        row["variants"][key]["policy"] = policy
    assert tones(report) == ["danger", "success", "warning", "warning"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installedVersions", ["2.0.0"]),
        ("installedVersions", ["1.2.0", "2.0.0"]),
        ("registries", ["https://different.example"]),
    ],
)
def test_peer_context_does_not_change_scanner_status(field: str, value: list[str]) -> None:
    report = report_for(["1.9.0", None, "1.10.0", None])
    report["dependencies"][0]["variants"]["renovate.optimised"][field] = value
    assert tones(report) == ["danger", "success", "danger", "success"]


@pytest.mark.parametrize("version", ["sha256:" + "a" * 64, "a" * 40, "1.3", "next", "1.3.0-rc.1"])
def test_native_recommendations_are_red_even_without_orderable_versions(version: str) -> None:
    report = report_for(["1.9.0", version, None, None])
    assert tones(report) == ["danger", "danger", "success", "success"]


def test_docker_updates_are_red() -> None:
    report = report_for(["1.9.0", "1.10.0", None, None])
    report["dependencies"][0]["ecosystem"] = "docker"
    assert tones(report) == ["danger", "danger", "success", "success"]


def test_all_completed_eligible_no_updates_are_green_and_input_unchanged() -> None:
    report = report_for([None] * 4)
    before = deepcopy(report)
    assert tones(report) == ["success"] * 4
    render(report)
    assert report == before


def test_missing_discovery_and_incomplete_lookup_are_amber() -> None:
    report = report_for(["1.9.0", None, "1.10.0", None])
    del report["dependencies"][0]["variants"]["dependabot.optimised"]
    report["dependencies"][0]["variants"]["renovate.optimised"]["lookupComplete"] = False
    assert tones(report) == ["danger", "success", "warning", "warning"]


def test_release_prefix_installed_contexts_are_equivalent() -> None:
    report = report_for(["1.9.0", "1.10.0", None, None])
    report["dependencies"][0]["variants"]["renovate.optimised"]["installedVersions"] = ["v1.2.0"]
    assert tones(report) == ["danger", "danger", "success", "success"]


@pytest.mark.parametrize(
    ("current", "recommended"),
    [("1.2.0", "1.2.0"), ("v1.2.0", "1.2.0"), ("1.2.0", "v1.2.0")],
)
def test_current_matches_own_recommendation_is_green(current: str, recommended: str) -> None:
    report = report_for([recommended, "1.10.0", None, None])
    side = report["dependencies"][0]["variants"]["dependabot.baseline"]
    side["installedVersions"] = [current]
    light = traffic_lights(report["dependencies"][0], report["variants"])["dependabot.baseline"]
    assert light == ("success", "Current matches this scanner's recommendation")
    assert "Current matches this scanner&#x27;s recommendation" in render(report)


def test_own_match_is_independent_of_other_scanners_context() -> None:
    report = report_for(["v1.2.0", "1.10.0", None, None])
    sides = report["dependencies"][0]["variants"]
    sides["renovate.baseline"]["installedVersions"] = ["2.0.0"]
    sides["renovate.baseline"]["registries"] = ["https://other.example"]
    assert tones(report) == ["success", "danger", "success", "success"]


@pytest.mark.parametrize("condition", ["partial", "failed", "lookup", "excluded", "deferred"])
def test_own_match_does_not_override_incomplete_or_filtered_evidence(condition: str) -> None:
    report = report_for(["v1.2.0", None, None, None])
    side = report["dependencies"][0]["variants"]["dependabot.baseline"]
    if condition in {"partial", "failed"}:
        report["variants"][0]["scan"]["status"] = condition
    elif condition == "lookup":
        side["lookupComplete"] = False
    else:
        side["policy"] = [condition]
    assert tones(report)[0] == ("primary" if condition == "deferred" else "warning")


def test_matching_one_target_does_not_hide_an_additional_recommendation() -> None:
    report = report_for(["1.2.0", None, None, None])
    report["dependencies"][0]["variants"]["dependabot.baseline"]["recommendations"].append("2.0.0")
    assert tones(report)[0] == "danger"


def test_deferred_completed_lookup_is_blue_in_html() -> None:
    report = report_for([None] * 4)
    report["dependencies"][0]["variants"]["dependabot.baseline"]["policy"] = ["deferred"]
    assert tones(report)[0] == "primary"
    output = render(report)
    assert 'class="table-primary"' in output
    assert "Deferred — waiting for policy eligibility" in output
    assert "Blue: deferred by policy" in output


@pytest.mark.parametrize("status", ["complete", "partial", "failed", "cancelled", "unsupported"])
@pytest.mark.parametrize("lookup_complete", [False, True])
def test_known_deferred_target_is_blue_when_policy_eligibility_is_unknown(
    lookup_complete: bool,
    status: str,
) -> None:
    report = report_for([None, "1.3.0", None, None])
    side = report["dependencies"][0]["variants"]["renovate.baseline"]
    side["policy"] = ["deferred"]
    side["lookupComplete"] = lookup_complete
    side["reason"] = "Native update is pending minimum-release-age checks."
    report["variants"][1]["scan"]["status"] = status
    assert tones(report)[1] == "primary"
    output = render(report)
    assert "<strong>Recommended: 1.3.0</strong>" in output
    assert "Deferred — waiting for policy eligibility" in output
    if not lookup_complete:
        assert "Update availability unknown" in output
    if status != "complete":
        assert "Incomplete scan — additional updates may be missing" in output


@pytest.mark.parametrize("condition", ["unknown", "mixed", "excluded"])
def test_known_target_does_not_hide_unrelated_uncertainty(condition: str) -> None:
    report = report_for([None, "1.3.0", None, None])
    side = report["dependencies"][0]["variants"]["renovate.baseline"]
    side["policy"] = ["deferred"]
    side["lookupComplete"] = False
    if condition == "mixed":
        side["policy"] = ["deferred", "unknown"]
    else:
        side["policy"] = [condition]
    assert tones(report)[1] == "warning"


@pytest.mark.parametrize("condition", ["partial", "lookup", "mixed"])
def test_uncertain_deferred_results_remain_yellow(condition: str) -> None:
    report = report_for([None] * 4)
    side = report["dependencies"][0]["variants"]["dependabot.baseline"]
    side["policy"] = ["deferred"]
    if condition == "partial":
        report["variants"][0]["scan"]["status"] = "partial"
    elif condition == "lookup":
        side["lookupComplete"] = False
    else:
        side["policy"] = ["deferred", "unknown"]
    assert tones(report)[0] == "warning"


@pytest.mark.parametrize("targets", [[None] * 4, ["2.0.0"] * 4])
def test_missing_current_version_is_unknown(targets: list[str | None]) -> None:
    report = report_for(targets)
    report["dependencies"][0]["variants"]["dependabot.baseline"]["installedVersions"] = []
    assert tones(report)[0] == "warning"


@pytest.mark.parametrize(
    ("ecosystem", "current", "recommended", "tone"),
    [
        ("docker", "v1.2.0", "1.2.0", "danger"),
        ("docker", "sha256:" + "a" * 64, "sha256:" + "a" * 64, "success"),
        ("docker", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "danger"),
        ("github-actions", "a" * 40, "a" * 40, "success"),
        ("npm", "1.3.0-rc.1", "1.3.0-rc.1", "success"),
    ],
)
def test_reference_equality_is_conservative(
    ecosystem: str, current: str, recommended: str, tone: str
) -> None:
    report = report_for([recommended, None, None, None])
    row = report["dependencies"][0]
    row["ecosystem"] = ecosystem
    row["variants"]["dependabot.baseline"]["installedVersions"] = [current]
    assert tones(report)[0] == tone
