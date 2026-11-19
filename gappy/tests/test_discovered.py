import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.compare.configurations import compare_configurations
from gappy.compare.discovered import compare
from gappy.contracts import Candidate, Scan
from gappy.io import GappyError, dump
from gappy.report.html import render
from gappy.report.traffic import traffic_lights
from gappy.scan.dependabot import Job
from gappy.scan.native import dependabot, renovate
from tests.test_compare import scan
from tests.test_scan import FIXTURES


def test_report_explains_direct_dependency_scope() -> None:
    output = render(compare(scan("dependabot", [None]), scan("renovate", [None])))
    assert "direct dependencies" in output
    assert "transitive" in output
    assert "No 100-dependency cap" in output


@pytest.mark.parametrize(
    "names",
    [
        ("org/workflows/.github/workflows/build.yml", "org/workflows/.github/workflows/test.yml"),
        ("org/workflows/restore", "org/workflows/save"),
    ],
)
def test_actions_repository_identity_matches_subactions_and_reusable_workflows(
    names: tuple[str, str],
) -> None:
    left = scan("dependabot", [None])
    right = scan("renovate", [None])
    manifest = ".github/workflows/pipeline.yml"
    original = left.observations[0]
    dependencies = [
        original.dependency.model_copy(
            update={
                "ecosystem": "github-actions",
                "package": name,
                "manifestPath": manifest,
            }
        )
        for name in names
    ]
    left = left.model_copy(
        update={
            "observations": [
                original.model_copy(update={"dependency": dep}) for dep in dependencies
            ]
        }
    )
    right = right.model_copy(
        update={
            "observations": [
                right.observations[0].model_copy(
                    update={
                        "dependency": dependencies[0].model_copy(
                            update={"package": "org/workflows"}
                        )
                    }
                )
            ]
        }
    )
    report = compare(left, right)
    assert len(report["dependencies"]) == 1
    row = report["dependencies"][0]
    assert row["package"] == "org/workflows"
    assert row["result"] == "no-recommendations"
    assert row["dependabot"]["discovered"] and row["renovate"]["discovered"]
    assert len(report["scans"][0]["observations"]) == 2
    assert report["scans"][0]["observations"][0]["dependency"]["package"] == dependencies[0].package
    assert "repository and manifest" in " ".join(report["warnings"])
    combined = compare_configurations((left, right), (left, right))
    assert len(combined["dependencies"]) == 1
    assert all(v["discovered"] for v in combined["dependencies"][0]["variants"].values())
    assert "repository and manifest" in render(combined)


@pytest.mark.parametrize("difference", ["repository", "manifest", "version", "policy"])
def test_actions_repository_grouping_preserves_real_differences(difference: str) -> None:
    left = scan("dependabot", [None])
    right = scan("renovate", [None])
    dep = left.observations[0].dependency.model_copy(
        update={
            "ecosystem": "github-actions",
            "package": "org/actions/restore",
            "manifestPath": ".github/workflows/build.yml",
        }
    )
    other = dep.model_copy(update={"package": "org/actions"})
    if difference == "repository":
        other = other.model_copy(update={"package": "org/other"})
    elif difference == "manifest":
        other = other.model_copy(update={"manifestPath": ".github/workflows/test.yml"})
    elif difference == "version":
        other = other.model_copy(update={"installedVersion": "2.0.0"})
    left = left.model_copy(
        update={"observations": [left.observations[0].model_copy(update={"dependency": dep})]}
    )
    right = right.model_copy(
        update={
            "observations": [
                right.observations[0].model_copy(
                    update={
                        "dependency": other,
                        "policy": "unknown" if difference == "policy" else "eligible",
                    }
                )
            ]
        }
    )
    report = compare(left, right)
    if difference in {"repository", "manifest"}:
        assert len(report["dependencies"]) == 2
        assert all(r["result"].endswith("-only-discovery") for r in report["dependencies"])
    else:
        assert len(report["dependencies"]) == 1
        assert report["dependencies"][0]["result"] == (
            "installed-version-mismatch" if difference == "version" else "not-assessable"
        )


def test_comparison_excludes_transitives_and_preserves_raw_scan_evidence() -> None:
    dependabot_events = [
        {
            "type": "update_dependency_list",
            "data": {
                "dependencies": [
                    {
                        "name": "parent",
                        "version": "1.0.0",
                        "requirements": [{"file": "package.json"}],
                    },
                    {"name": "gappy-test-indirect-leaf", "version": "1.0.0", "requirements": []},
                    {
                        "name": "gappy-test-unknown-package",
                        "version": "1.0.0",
                    },
                ]
            },
        },
        {
            "type": "create_pull_request",
            "data": {
                "dependencies": [
                    {"name": "parent", "version": "2.0.0"},
                    {"name": "gappy-test-indirect-leaf", "version": "3.0.0"},
                    {"name": "gappy-test-unknown-package", "version": "4.0.0"},
                ]
            },
        },
        {"type": "mark_as_processed", "data": {}},
    ]
    renovate_events = [
        {
            "msg": "packageFiles with updates",
            "config": {
                "npm": [
                    {
                        "packageFile": "package.json",
                        "deps": [
                            {
                                "depName": "parent",
                                "depType": "dependencies",
                                "currentVersion": "1.0.0",
                                "updates": [{"newVersion": "2.0.0"}],
                            },
                            {
                                "depName": "gappy-test-indirect-leaf",
                                "depType": "transitive",
                                "currentVersion": "1.0.0",
                                "updates": [{"newVersion": "3.0.0"}],
                            },
                        ],
                    }
                ]
            },
        },
        {"msg": "Repository finished"},
    ]
    left_observations, _ = dependabot("\n".join(map(json.dumps, dependabot_events)), "/", None)
    right_observations, _ = renovate("\n".join(map(json.dumps, renovate_events)))
    left = scan("dependabot", []).model_copy(update={"observations": left_observations})
    right = scan("renovate", []).model_copy(update={"observations": right_observations})
    report = compare(left, right)

    assert [row["package"] for row in report["dependencies"]] == ["parent"]
    assert report["dependencies"][0]["result"] == "same-recommendations"
    assert report["metrics"]["dependencies"] == 1
    for provider in ("dependabot", "renovate"):
        assert report["metrics"]["providers"][provider] == {
            "discovered": 1,
            "withRecommendations": 1,
            "recommendedUpdates": 1,
        }
    assert report["metrics"]["excludedObservations"] == {
        "dependabot": {"transitive": 1, "unknown": 1},
        "renovate": {"transitive": 1, "unknown": 0},
    }
    assert {item["dependency"]["package"] for item in report["scans"][0]["observations"]} == {
        "parent",
        "gappy-test-indirect-leaf",
        "gappy-test-unknown-package",
    }
    output = render(report)
    assert "parent" in output
    assert "gappy-test-indirect-leaf" not in output
    assert "gappy-test-unknown-package" not in output
    assert "unclassified observations" in output


def test_old_scan_without_directness_is_not_silently_counted_as_direct() -> None:
    old = scan("dependabot", [None]).model_dump()
    old["schemaVersion"] = "1.0.0"
    old["observations"][0].pop("directness")
    report = compare(Scan.model_validate(old), scan("renovate", []))
    assert report["dependencies"] == []
    assert report["metrics"]["excludedObservations"]["dependabot"]["unknown"] == 1
    assert any("rescan" in warning.lower() for warning in report["warnings"])
    assert "unclassified" in render(report)


def test_same_context_direct_and_transitive_candidates_do_not_merge() -> None:
    events = [
        {
            "msg": "packageFiles with updates",
            "config": {
                "gomod": [
                    {
                        "packageFile": "go.mod",
                        "deps": [
                            {
                                "depName": "example.com/shared",
                                "depType": "require",
                                "currentVersion": "1.0.0",
                                "updates": [{"newVersion": "2.0.0"}],
                            },
                            {
                                "depName": "example.com/shared",
                                "depType": "indirect",
                                "currentVersion": "1.0.0",
                                "updates": [{"newVersion": "3.0.0"}],
                            },
                        ],
                    }
                ]
            },
        },
        {"msg": "Repository finished"},
    ]
    observations, complete = renovate("\n".join(map(json.dumps, events)))
    assert complete
    assert {item.directness for item in observations} == {"direct", "transitive"}
    right = scan("renovate", []).model_copy(update={"observations": observations})
    right = Scan.model_validate(right.model_dump())
    report = compare(scan("dependabot", []), right)
    assert report["dependencies"][0]["renovate"]["recommendations"] == ["2.0.0"]
    assert report["metrics"]["providers"]["renovate"]["recommendedUpdates"] == 1


@pytest.mark.parametrize("dep_type", ["overrides", "resolutions", "pnpm.overrides"])
def test_npm_override_entries_are_unclassified_without_direct_declaration(dep_type: str) -> None:
    events = [
        {
            "msg": "packageFiles with updates",
            "config": {
                "npm": [
                    {
                        "packageFile": "package.json",
                        "deps": [
                            {
                                "depName": "possibly-transitive",
                                "depType": dep_type,
                                "currentVersion": "1.0.0",
                                "updates": [{"newVersion": "2.0.0"}],
                            }
                        ],
                    }
                ]
            },
        },
        {"msg": "Repository finished"},
    ]
    observations, _ = renovate("\n".join(map(json.dumps, events)))
    assert observations[0].directness == "unknown"
    report = compare(
        scan("dependabot", []),
        scan("renovate", []).model_copy(update={"observations": observations}),
    )
    assert report["dependencies"] == []
    assert report["metrics"]["excludedObservations"]["renovate"]["unknown"] == 1


def test_update_summary_counts_targets_separately_and_deduplicates() -> None:
    left = scan("dependabot", ["1.2.1", None, "1.2.1"])
    first, second, third = left.observations
    first = first.model_copy(
        update={
            "candidates": [
                Candidate(targetVersion=target, evidenceId=f"evidence-{index}")
                for index, target in enumerate(["1.2.1", "1.2.1", "2.0.0"])
            ],
        }
    )
    third = third.model_copy(
        update={
            "dependency": third.dependency.model_copy(update={"ecosystem": "docker"}),
        }
    )
    left = left.model_copy(update={"observations": [first, second, third, first]})
    report = compare(left, scan("renovate", ["1.2.1", "1.2.1", None]))
    counts = report["metrics"]
    assert counts["providers"]["dependabot"] == {
        "discovered": 3,
        "withRecommendations": 2,
        "recommendedUpdates": 3,
    }
    assert counts["providers"]["renovate"] == {
        "discovered": 3,
        "withRecommendations": 2,
        "recommendedUpdates": 2,
    }
    assert counts["byEcosystem"]["docker"]["dependabot"] == {
        "discovered": 1,
        "withRecommendations": 1,
        "recommendedUpdates": 1,
    }
    assert counts["byEcosystem"]["docker"]["renovate"] == {
        "discovered": 0,
        "withRecommendations": 0,
        "recommendedUpdates": 0,
    }
    for provider in ("dependabot", "renovate"):
        for metric, total in counts["providers"][provider].items():
            assert (
                sum(values[provider][metric] for values in counts["byEcosystem"].values()) == total
            )
    output = render(report)
    assert "3 recommended target versions" in output
    assert "2 recommended target versions" in output
    assert "Dependabot update summary" in output
    assert "Renovate update summary" in output


@pytest.mark.parametrize("status", ["complete", "partial", "failed", "cancelled"])
def test_update_summary_does_not_claim_incomplete_counts_are_exhaustive(status: str) -> None:
    left = scan("dependabot", ["1.2.1"]).model_copy(update={"status": status})
    report = compare(left, scan("renovate", []))
    assert report["metrics"]["providers"]["renovate"]["recommendedUpdates"] == 0
    assert ("Incomplete scan: these are observed recommendations only" in render(report)) == (
        status != "complete"
    )


@pytest.mark.parametrize(
    "path",
    sorted((FIXTURES / "policy").glob("*/*.jsonl")),
    ids=lambda path: path.parent.name + "/" + path.stem,
)
def test_real_sanitized_policy_and_non_npm_captures(path: Path) -> None:
    scenario = path.parent.name
    expected_update = scenario not in {
        "no-update",
        "ignore-patch",
        "disable-patch",
        "cooldown-recent",
    }
    if path.name.startswith("renovate"):
        if scenario == "cooldown-recent":
            expected_update = True
        observations, complete = renovate(
            path.read_text(),
            require_branches=True,
            policy_filtered=True,
        )
    else:
        observations, complete = dependabot(
            path.read_text(),
            "/",
            None,
            ecosystem="helm" if scenario == "helm" else "npm",
        )
    assert complete
    assert observations
    assert all(
        item.dependency.ecosystem == ("helm" if scenario == "helm" else "npm")
        for item in observations
    )
    assert all(bool(item.candidates) == expected_update for item in observations)
    if path.name.startswith("renovate") and scenario == "cooldown-recent":
        assert all(item.policy == "deferred" for item in observations)


def test_offline_cli_compares_non_npm_without_inventory_or_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline comparison attempted a process or network operation.")

    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    for provider, target in (("dependabot", "23-alpine"), ("renovate", "24-alpine")):
        model = scan(provider, [target])
        item = model.observations[0]
        model = model.model_copy(
            update={
                "observations": [
                    item.model_copy(
                        update={
                            "dependency": item.dependency.model_copy(
                                update={
                                    "ecosystem": "docker",
                                    "package": "node",
                                    "manifestPath": "Dockerfile",
                                    "installedVersion": "22-alpine",
                                    "versioning": "native",
                                    "registry": None,
                                }
                            )
                        }
                    )
                ]
            }
        )
        (tmp_path / f"{provider}.json").write_text(dump(model.model_dump()))
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(tmp_path / "dependabot.json"),
            "--renovate-json",
            str(tmp_path / "renovate.json"),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    report = json.loads((tmp_path / "comparison.json").read_text())
    assert report["metrics"]["ecosystems"] == ["docker"]
    assert report["dependencies"][0]["result"] == "different-recommendations"
    assert "23-alpine" in (tmp_path / "comparison.html").read_text()
    assert not (tmp_path / "inventory.json").exists()


def test_comparison_has_no_inventory_and_compares_all_recommendations() -> None:
    report = compare(scan("dependabot", ["1.2.1", None]), scan("renovate", ["2.0.0"]))
    assert "inventory" not in report
    assert "coverage" not in report["metrics"]
    assert "winner" not in report["metrics"]
    assert [row["result"] for row in report["dependencies"]] == [
        "different-recommendations",
        "dependabot-only-discovery",
    ]
    assert report["dependencies"][0]["dependabot"]["recommendations"] == ["1.2.1"]
    assert report["dependencies"][0]["renovate"]["recommendations"] == ["2.0.0"]
    assert dump(report) == dump(
        compare(scan("dependabot", ["1.2.1", None]), scan("renovate", ["2.0.0"]))
    )
    output = render(report)
    assert "No Gappy-generated inventory" in output or "no Gappy-generated inventory" in output
    assert "different-recommendations" in output
    assert "Confirmed miss" not in output
    assert "<script" not in output


def test_no_recommendations_preserves_extracted_dependencies_and_failures() -> None:
    left = scan("dependabot", [None])
    right = scan("renovate", []).model_copy(
        update={"status": "failed", "warnings": ["Native lookup failed."]}
    )
    report = compare(left, right)
    row = report["dependencies"][0]
    assert row["result"] == "dependabot-only-discovery"
    assert "failed" in row["renovate"]["reason"]
    assert "Native lookup failed." in render(report)
    assert report["metrics"]["providers"]["dependabot"]["discovered"] == 1


def test_completed_empty_lookups_show_no_newer_update_label() -> None:
    report = compare(scan("dependabot", [None]), scan("renovate", [None]))
    output = render(report)
    badge = '<span class="badge text-bg-success">No newer update recommended</span>'
    assert output.count(badge) == 2
    assert "already be up to date, or no newer eligible version may be available" in output
    assert "not proof that the installed version is the latest published release" in output
    assert "Both completed eligible lookups" in output


@pytest.mark.parametrize(
    ("policy", "lookup_complete", "status", "label", "tone"),
    [
        ("excluded", True, "complete", "Excluded by policy", "secondary"),
        ("deferred", True, "complete", "Update deferred", "secondary"),
        ("unknown", True, "complete", "Update availability unknown", "warning"),
        ("eligible", False, "complete", "Update availability unknown", "warning"),
        ("eligible", True, "partial", "Update availability unknown", "warning"),
        ("eligible", True, "failed", "Update availability unknown", "warning"),
        ("eligible", True, "cancelled", "Update availability unknown", "warning"),
        ("eligible", True, "unsupported", "Update availability unknown", "warning"),
    ],
)
def test_empty_recommendations_do_not_claim_up_to_date_without_evidence(
    policy: str, lookup_complete: bool, status: str, label: str, tone: str
) -> None:
    left = scan("dependabot", [None])
    left = left.model_copy(
        update={
            "status": status,
            "warnings": ["Synthetic diagnostic."],
            "observations": [
                left.observations[0].model_copy(
                    update={"policy": policy, "lookupComplete": lookup_complete}
                )
            ],
        }
    )
    report = compare(left, scan("renovate", [None]))
    assert report["dependencies"][0]["result"] == "not-assessable"
    output = render(report)
    assert f'<span class="badge text-bg-{tone}">{label}</span>' in output
    assert (
        output.count('<span class="badge text-bg-success">No newer update recommended</span>') == 1
    )


def test_recommended_or_undiscovered_dependencies_have_no_empty_update_label() -> None:
    output = render(compare(scan("dependabot", ["1.2.1"]), scan("renovate", [])))
    assert '<span class="badge text-bg-success">No newer update recommended</span>' not in output
    assert '<span class="badge text-bg-warning">Update availability unknown</span>' not in output
    assert "Recommended: 1.2.1" in output


def test_context_mismatch_is_not_recommendation_agreement() -> None:
    left = scan("dependabot", ["1.2.2"])
    right = scan("renovate", ["1.2.2"])
    obs = right.observations[0]
    right = right.model_copy(
        update={
            "observations": [
                obs.model_copy(
                    update={
                        "dependency": obs.dependency.model_copy(
                            update={"installedVersion": "1.1.0"}
                        )
                    }
                )
            ]
        }
    )
    assert compare(left, right)["dependencies"][0]["result"] == "installed-version-mismatch"
    with pytest.raises(GappyError):
        compare(left, right.model_copy(update={"sessionId": "different"}))


@pytest.mark.parametrize(
    ("ecosystem", "installed", "other", "expected"),
    [
        ("github-actions", "8.0.2", "v8.0.2", "same-recommendations"),
        ("npm", "v1.2.0", "1.2.0", "same-recommendations"),
        ("helm", "1.2.0-rc.1+build.2", "v1.2.0-rc.1+build.2", "same-recommendations"),
        ("github-actions", "8.0.2", "v8.0.3", "installed-version-mismatch"),
        ("npm", "1.2.0-rc.1", "v1.2.0-rc.2", "installed-version-mismatch"),
        ("github-actions", "8.0.2", "a" * 40, "installed-reference-unresolved"),
        ("docker", "1.2.0", "v1.2.0", "installed-version-mismatch"),
        ("npm", "release", "vrelease", "installed-version-mismatch"),
        ("npm", "1.2.0", "vv1.2.0", "installed-version-mismatch"),
    ],
)
def test_installed_release_prefix_comparison_preserves_native_evidence(
    ecosystem: str, installed: str, other: str, expected: str
) -> None:
    left = scan("dependabot", ["1.3.0"]).model_dump()
    right = scan("renovate", ["1.3.0"]).model_dump()
    for data, version in ((left, installed), (right, other)):
        data["observations"][0]["dependency"].update(ecosystem=ecosystem, installedVersion=version)
    report = compare(Scan.model_validate(left), Scan.model_validate(right))
    row = report["dependencies"][0]
    assert row["result"] == expected
    assert row["dependabot"]["installedVersions"] == [installed]
    assert row["renovate"]["installedVersions"] == [other]
    assert report["scans"][1]["observations"][0]["dependency"]["installedVersion"] == other


@pytest.mark.parametrize(
    ("ecosystem", "manager", "manifest", "pin", "source"),
    [
        (
            "github-actions",
            "github-actions",
            ".github/workflows/test.yml",
            "a" * 40,
            {"ref": "a" * 40},
        ),
        (
            "docker",
            "dockerfile",
            "Dockerfile",
            "sha256:" + "a" * 64,
            {"tag": "1.2.0", "digest": "a" * 64},
        ),
    ],
)
@pytest.mark.parametrize("renovate_resolves_version", [False, True])
@pytest.mark.parametrize("same_pin", [False, True])
def test_native_digest_pin_is_installed_identity(
    ecosystem: str,
    manager: str,
    manifest: str,
    pin: str,
    source: dict[str, str],
    renovate_resolves_version: bool,
    same_pin: bool,
) -> None:
    other_pin = pin if same_pin else pin[:-1] + "b"
    left_events = [
        {
            "type": "update_dependency_list",
            "data": {
                "dependencies": [
                    {
                        "name": "fixture",
                        "version": "1.2.0",
                        "requirements": [{"file": manifest, "source": source}],
                    }
                ]
            },
        },
        {"type": "mark_as_processed", "data": {}},
    ]
    right_events = [
        {
            "msg": "packageFiles with updates",
            "config": {
                manager: [
                    {
                        "packageFile": manifest,
                        "deps": [
                            {
                                "depName": "fixture",
                                "currentDigest": other_pin,
                                "currentValue": other_pin,
                                "currentVersion": "1.2.0"
                                if renovate_resolves_version
                                else other_pin,
                                "updates": [],
                            }
                        ],
                    }
                ]
            },
        },
        {"msg": "Repository finished"},
    ]
    a, _ = dependabot("\n".join(map(json.dumps, left_events)), "/", None, ecosystem=ecosystem)
    b, _ = renovate("\n".join(map(json.dumps, right_events)))
    assert a[0].dependency.installedVersion == pin
    assert b[0].dependency.installedVersion == other_pin
    assert "Native reported version: 1.2.0" in a[0].reason
    filtered = Job("/", ecosystem, {}, [], True).policy(a)
    assert "Native reported version: 1.2.0" in filtered[0].reason
    left = scan("dependabot", []).model_copy(update={"observations": a})
    right = scan("renovate", []).model_copy(update={"observations": b})
    row = compare(left, right)["dependencies"][0]
    assert row["result"] == ("no-recommendations" if same_pin else "installed-version-mismatch")


def test_dependabot_pins_are_scoped_to_each_manifest() -> None:
    events = [
        {
            "type": "update_dependency_list",
            "data": {
                "dependencies": [
                    {
                        "name": "actions/checkout",
                        "version": "7.0.1",
                        "requirements": [
                            {"file": ".github/workflows/a.yml", "source": {"ref": "a" * 40}},
                            {"file": ".github/workflows/b.yml", "source": {"ref": "b" * 40}},
                            {"file": ".github/workflows/c.yml", "source": {"ref": "v7.0.1"}},
                        ],
                    }
                ]
            },
        },
        {"type": "mark_as_processed", "data": {}},
    ]
    observations, complete = dependabot(
        "\n".join(map(json.dumps, events)), "/", None, ecosystem="github-actions"
    )
    assert complete
    assert {o.dependency.manifestPath: o.dependency.installedVersion for o in observations} == {
        ".github/workflows/a.yml": "a" * 40,
        ".github/workflows/b.yml": "b" * 40,
        ".github/workflows/c.yml": "7.0.1",
    }


def test_old_digest_vs_release_exports_are_unresolved_not_equivalent() -> None:
    left = scan("dependabot", [None])
    right = scan("renovate", [None])
    for model, version in ((left, "7.0.1"), (right, "a" * 40)):
        obs = model.observations[0]
        model.observations[0] = obs.model_copy(
            update={
                "dependency": obs.dependency.model_copy(
                    update={"ecosystem": "github-actions", "installedVersion": version}
                )
            }
        )
    report = compare(left, right)
    assert report["dependencies"][0]["result"] == "installed-reference-unresolved"
    assert report["metrics"]["outcomes"]["installed-version-mismatch"] == 0
    assert "cannot be linked" in render(report)


def test_registry_mismatch_does_not_imply_same_recommendations() -> None:
    left = scan("dependabot", ["1.2.2"])
    right = scan("renovate", ["1.2.2"])
    observations = []
    for item in right.observations:
        observations.append(
            item.model_copy(
                update={
                    "dependency": item.dependency.model_copy(
                        update={"registry": "https://other.example"}
                    )
                }
            )
        )
    assert (
        compare(left, right.model_copy(update={"observations": observations}))["dependencies"][0][
            "result"
        ]
        == "registry-mismatch"
    )


@pytest.mark.parametrize(
    ("manager", "datasource", "ecosystem", "package", "version"),
    [
        ("github-actions", "github-tags", "github-actions", "actions/checkout", "v4"),
        ("github-actions", "docker", "docker", "redis", "7.4"),
        ("dockerfile", "docker", "docker", "docker.io/library/node", "22-alpine"),
        ("helmv3", "helm", "helm", "test-chart", "1.2.0"),
        ("pip_requirements", "pypi", "pip", "Some_Package", "1.0"),
        ("pre-commit", "github-tags", "pre-commit", "https://github.com/test/hook", "v1.0"),
        ("custom.regex", "custom", "custom.regex", "fixture", "arbitrary"),
    ],
)
def test_renovate_extracts_non_npm_managers(
    manager: str,
    datasource: str,
    ecosystem: str,
    package: str,
    version: str,
) -> None:
    events = [
        {
            "msg": "packageFiles with updates",
            "config": {
                manager: [
                    {
                        "packageFile": "manifest.yml",
                        "deps": [
                            {
                                "depName": package,
                                "datasource": datasource,
                                "currentValue": version,
                                "updates": [],
                            }
                        ],
                    }
                ]
            },
        },
        {"msg": "Repository finished", "level": 30},
    ]
    observations, complete = renovate("\n".join(json.dumps(event) for event in events))
    assert complete
    assert len(observations) == 1
    assert observations[0].dependency.ecosystem == ecosystem
    assert observations[0].dependency.installedVersion == version


def test_post_policy_branches_remove_disabled_lookup_candidate() -> None:
    data = (FIXTURES / "renovate-patch.jsonl").read_text()
    data += '\n{"msg":"branches","branches":[]}\n'
    observations, _ = renovate(data, policy_filtered=True, require_branches=True)
    assert not observations[0].candidates
    assert observations[0].policy == "unknown"
    assert not observations[0].lookupComplete
    with pytest.raises(GappyError, match="branch evidence"):
        renovate((FIXTURES / "renovate-patch.jsonl").read_text(), require_branches=True)


def test_deferred_native_version_is_recorded_as_recommended_and_remains_blue() -> None:
    data = (FIXTURES / "policy/cooldown-recent/renovate-cooldown-recent-1.jsonl").read_text()
    observations, complete = renovate(data, policy_filtered=True, require_branches=True)
    assert complete
    observation = observations[0]
    assert observation.policy == "deferred"
    assert observation.lookupComplete
    assert [candidate.targetVersion for candidate in observation.candidates] == ["1.2.2"]
    right = scan("renovate", [None]).model_copy(update={"observations": observations})
    left = scan("dependabot", [None]).model_copy(
        update={
            "observations": [
                scan("dependabot", [None])
                .observations[0]
                .model_copy(update={"dependency": observation.dependency})
            ]
        }
    )
    report = compare_configurations((left, right), (left, right))
    row = report["dependencies"][0]
    assert row["variants"]["renovate.baseline"]["recommendations"] == ["1.2.2"]
    assert row["hasChanges"]
    assert traffic_lights(row, report["variants"])["renovate.baseline"] == (
        "primary",
        "Deferred — waiting for policy eligibility",
    )
    output = render(report)
    assert 'class="table-primary"' in output
    assert "<strong>Recommended: 1.2.2</strong>" in output
    assert 'data-has-updates="true"' in output
    paired = render(compare(left, right))
    assert "Recommended: 1.2.2" in paired
    assert "Update deferred" in paired


def test_dependabot_preserves_multiple_manifests_and_unknown_versions() -> None:
    events = [
        {
            "type": "update_dependency_list",
            "data": {
                "dependencies": [
                    {
                        "name": "actions/checkout",
                        "version": None,
                        "requirements": [
                            {"file": ".github/workflows/test.yml"},
                            {"file": ".github/workflows/build.yml"},
                        ],
                    }
                ]
            },
        },
        {"type": "mark_as_processed", "data": {}},
    ]
    observations, complete = dependabot(
        "\n".join(json.dumps(event) for event in events),
        "/",
        None,
        ecosystem="github-actions",
    )
    assert complete
    assert len(observations) == 2
    assert {obs.dependency.manifestPath for obs in observations} == {
        ".github/workflows/test.yml",
        ".github/workflows/build.yml",
    }
    assert all(obs.dependency.ecosystem == "github-actions" for obs in observations)
    assert all(obs.dependency.installedVersion == "(not reported)" for obs in observations)
