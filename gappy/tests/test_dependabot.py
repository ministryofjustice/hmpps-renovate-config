"""Local policy translation and safe per-entry orchestration."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from gappy.contracts import Candidate, Snapshot
from gappy.scan.dependabot import jobs
from gappy.scan.native import (
    dependabot,
    dependabot_cooldown_filtered,
    dependabot_docker_no_updates,
    diagnostics,
)
from gappy.scan.session import execute, toolchain
from tests.test_scan import FIXTURES, fixture_repo


def snapshot() -> Snapshot:
    return Snapshot(revision="a" * 40, contentHash="b" * 64, configHashes={})


def config(**settings: Any) -> dict[str, Any]:
    return {
        "version": 2,
        "updates": [
            {
                "package-ecosystem": "npm",
                "directory": "/",
                "schedule": {"interval": "weekly"},
                **settings,
            }
        ],
    }


def test_native_policy_translation_preserves_rules() -> None:
    source = config(
        groups={"non-major": {"update-types": ["minor", "patch"], "patterns": ["*"]}},
        ignore=[
            {"dependency-name": "@types/node", "update-types": ["version-update:semver-major"]},
            {"dependency-name": "fixture", "versions": [">= 2.0", "1.2.3"]},
        ],
        cooldown={"default-days": 7, "exclude": ["typescript"]},
        allow=[{"dependency-type": "direct"}],
        **{"versioning-strategy": "increase"},
    )
    before = json.dumps(source)
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    job = planned[0].definition
    assert job["dependency-groups"] == [
        {
            "name": "non-major",
            "applies-to": "version-updates",
            "rules": {"update-types": ["minor", "patch"], "patterns": ["*"]},
        }
    ]
    assert job["ignore-conditions"] == [
        {"dependency-name": "@types/node", "update-types": ["version-update:semver-major"]},
        {"dependency-name": "fixture", "version-requirement": ">= 2.0"},
        {"dependency-name": "fixture", "version-requirement": "1.2.3"},
    ]
    assert job["cooldown"] == source["updates"][0]["cooldown"]
    assert job["allowed-updates"] == [{"dependency-type": "direct"}]
    assert job["requirements-update-strategy"] == "increase"
    assert json.dumps(source) == before


def test_native_jobs_default_to_direct_dependency_updates() -> None:
    planned, warnings = jobs(config(), snapshot(), "gappy/fixture")
    assert not warnings
    assert planned[0].definition["allowed-updates"] == [
        {"update-type": "all", "dependency-type": "direct"}
    ]


@pytest.mark.parametrize("dependency_type", [None, "all", "indirect", "production", "development"])
def test_allow_rules_default_to_direct_without_overriding_explicit_types(
    dependency_type: str | None,
) -> None:
    rule = {"dependency-name": "fixture-*"}
    if dependency_type is not None:
        rule["dependency-type"] = dependency_type
    source = config(allow=[rule])
    before = json.dumps(source)
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    assert planned[0].definition["allowed-updates"] == [
        {"dependency-name": "fixture-*", "dependency-type": dependency_type or "direct"}
    ]
    assert json.dumps(source) == before


def test_multiple_ecosystems_have_separate_native_jobs() -> None:
    source = config()
    source["updates"].append(
        {
            "package-ecosystem": "docker",
            "directory": "/",
            "schedule": {"interval": "weekly"},
        }
    )
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert len(planned) == 2
    assert not warnings
    assert [job.definition["package-manager"] for job in planned] == ["npm_and_yarn", "docker"]
    assert planned[0].definition["experiments"] == {"dealias-packages": True}
    assert "experiments" not in planned[1].definition


def multi_config(**settings: Any) -> dict[str, Any]:
    source = config(**{"multi-ecosystem-group": "infrastructure", "patterns": ["*"], **settings})
    del source["updates"][0]["schedule"]
    source["multi-ecosystem-groups"] = {
        "infrastructure": {
            "schedule": {"interval": "weekly"},
            "labels": ["infrastructure", "dependencies"],
            "open-pull-requests-limit": 5,
        }
    }
    return source


def test_multi_ecosystem_group_preserves_scoped_native_policy() -> None:
    source = multi_config(
        patterns=["fixture-*", "@scope/*"],
        cooldown={"default-days": 7},
        allow=[{"dependency-type": "production"}],
        groups={"minor": {"update-types": ["minor", "patch"]}},
        ignore=[{"dependency-name": "fixture-old"}],
    )
    source["updates"].append(
        {
            "package-ecosystem": "github-actions",
            "directory": "/",
            "patterns": ["actions/*"],
            "multi-ecosystem-group": "infrastructure",
        }
    )
    before = json.dumps(source)
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    assert [job.ecosystem for job in planned] == ["npm", "github-actions"]
    assert planned[0].definition["allowed-updates"] == [
        {"dependency-type": "production", "dependency-name": "fixture-*"},
        {"dependency-type": "production", "dependency-name": "@scope/*"},
    ]
    assert planned[0].definition["cooldown"] == {"default-days": 7}
    assert planned[0].definition["dependency-groups"][0]["name"] == "minor"
    assert planned[0].definition["ignore-conditions"] == [{"dependency-name": "fixture-old"}]
    assert planned[1].definition["allowed-updates"] == [
        {"dependency-type": "direct", "dependency-name": "actions/*"}
    ]
    assert not planned[1].filtered
    assert json.dumps(source) == before


@pytest.mark.parametrize(
    "settings",
    [
        {"multi-ecosystem-group": "missing"},
        {"multi-ecosystem-group": []},
        {"patterns": []},
        {"patterns": "*"},
        {"patterns": [""]},
        {"directory": None},
        {"allow": [{}]},
        {"allow": [{"unknown": "canary"}]},
        {"patterns": ["actions/*"], "allow": [{"dependency-name": "other-*"}]},
    ],
)
def test_invalid_multi_ecosystem_entry_does_not_block_other_entries(
    settings: dict[str, Any],
) -> None:
    source = multi_config(**settings)
    source["updates"].append(config()["updates"][0])
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert len(planned) == 1
    assert len(warnings) == 1
    assert "entry 1" in warnings[0]


@pytest.mark.parametrize(
    "group",
    [
        None,
        {},
        {"schedule": {"interval": "invalid"}},
        {"schedule": {"interval": "weekly"}, "directory": "/"},
        {"schedule": {"interval": "weekly"}, "cooldown": {"default-days": 7}},
        {"schedule": {"interval": "weekly"}, "target-branch": "other"},
        {"schedule": {"interval": "weekly"}, "labels": "wrong"},
        {"schedule": {"interval": "weekly"}, "milestone": True},
        {"schedule": {"interval": "weekly"}, "open-pull-requests-limit": -1},
    ],
)
def test_invalid_or_unsupported_multi_group_is_explicit(group: object) -> None:
    source = multi_config()
    source["multi-ecosystem-groups"]["infrastructure"] = group
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not planned
    assert len(warnings) == 1


def test_multi_ecosystem_patterns_cannot_be_omitted() -> None:
    source = multi_config()
    del source["updates"][0]["patterns"]
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not planned
    assert "patterns" in warnings[0]


@pytest.mark.parametrize(
    ("patterns", "name", "expected"),
    [(["*"], "fixture-*", "fixture-*"), (["fixture-*"], "fixture-*", "fixture-*")],
)
def test_multi_group_patterns_preserve_compatible_named_allow(
    patterns: list[str], name: str, expected: str
) -> None:
    planned, warnings = jobs(
        multi_config(patterns=patterns, allow=[{"dependency-name": name}]),
        snapshot(),
        "gappy/fixture",
    )
    assert not warnings
    assert planned[0].definition["allowed-updates"] == [
        {"dependency-type": "direct", "dependency-name": expected}
    ]


@pytest.mark.parametrize("output", ["dependabot-no-update.jsonl", "dependabot-patch.jsonl"])
def test_discovery_outside_multi_group_patterns_is_excluded(output: str) -> None:
    planned, warnings = jobs(multi_config(patterns=["other-*"]), snapshot(), "gappy/fixture")
    assert not warnings
    observations, _ = dependabot((FIXTURES / output).read_text(), "/", None)
    result = planned[0].policy(observations)
    assert result
    assert all(o.policy == "excluded" and not o.candidates for o in result)
    assert all("Outside multi-ecosystem" in o.reason for o in result)


def test_multi_group_wildcard_does_not_make_completed_lookup_unknown() -> None:
    planned, warnings = jobs(multi_config(), snapshot(), "gappy/fixture")
    assert not warnings
    observations, _ = dependabot((FIXTURES / "dependabot-no-update.jsonl").read_text(), "/", None)
    result = planned[0].policy(observations)
    assert all(o.policy == "eligible" and o.lookupComplete for o in result)


def test_multi_group_orchestration_runs_without_false_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    (source / ".github/dependabot.yml").write_text(yaml.safe_dump(multi_config()))
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        job_path = Path(args[args.index("--file") + 1])
        definition = yaml.safe_load(job_path.read_text())["job"]
        assert definition["allowed-updates"] == [
            {"dependency-type": "direct", "dependency-name": "*"}
        ]
        return 0, (FIXTURES / "dependabot-patch.jsonl").read_text()

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert len(calls) == 1
    assert result.status == "complete"
    assert result.observations[0].candidates
    assert any("Cross-ecosystem PR" in warning for warning in result.warnings)


def test_optimised_config_plans_configured_ecosystems_and_commit_messages() -> None:
    path = Path(__file__).parents[1] / "optimised/dependabot.yml"
    planned, warnings = jobs(yaml.safe_load(path.read_text()), snapshot(), "gappy/fixture")
    assert not warnings
    assert [job.ecosystem for job in planned] == [
        "npm",
        "github-actions",
        "docker",
        "docker-compose",
        "docker",
    ]
    assert [job.directory for job in planned] == [
        "/",
        "/",
        "/",
        "/",
        "/.github/workflows",
    ]
    assert all(
        job.definition["allowed-updates"] == [{"update-type": "all", "dependency-type": "direct"}]
        for job in planned
    )
    assert [job.definition["commit-message-options"] for job in planned] == [
        {"prefix": "deps"},
        {"prefix": "ci"},
        {"prefix": "docker"},
        {"prefix": "docker"},
        {"prefix": "docker"},
    ]
    assert planned[0].definition["dependency-groups"] == [
        {
            "name": name,
            "applies-to": "version-updates",
            "rules": {"dependency-type": kind},
        }
        for name, kind in (
            ("production-dependencies", "production"),
            ("development-dependencies", "development"),
        )
    ]


def test_commit_message_options_are_translated_without_mutating_config() -> None:
    source = config(
        **{"commit-message": {"prefix": "deps", "prefix-development": "dev", "include": "scope"}}
    )
    before = json.dumps(source)
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    assert planned[0].definition["commit-message-options"] == {
        "prefix": "deps",
        "prefix-development": "dev",
        "include-scope": True,
    }
    assert json.dumps(source) == before


@pytest.mark.parametrize(
    "value",
    [None, "deps", {"prefix": 1}, {"prefix-development": False}, {"include": "invalid"}, {"x": 1}],
)
def test_invalid_commit_message_is_explicitly_rejected(value: Any) -> None:
    planned, warnings = jobs(config(**{"commit-message": value}), snapshot(), "gappy/fixture")
    assert not planned
    assert len(warnings) == 1
    assert "Commit message" in warnings[0] or "commit-message" in warnings[0]


def test_unsupported_entry_setting_is_named_in_diagnostic() -> None:
    planned, warnings = jobs(config(**{"unsupported-option": True}), snapshot(), "gappy/fixture")
    assert not planned
    assert "unsupported-option" in warnings[0]


def test_multiple_explicit_directories() -> None:
    source = config(directories=["/", "/packages/app"])
    del source["updates"][0]["directory"]
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    assert [job.definition["source"]["directory"] for job in planned] == ["/", "/packages/app"]


@pytest.mark.parametrize(
    "settings",
    [
        {"directory": "/../secrets"},
        {"directory": "relative"},
        {"directory": "/packages/*"},
        {"directories": ["/"]},
        {"ignore": [{"dependency-name": "*", "unsupported": "canary"}]},
        {"groups": {"test": {"update-types": ["not-a-type"]}}},
        {"cooldown": {"default-days": -1}},
        {"cooldown": {"default-days": True}},
        {"cooldown": {"exclude": "not-an-array"}},
        {"allow": [{"unknown": "canary"}]},
        {"versioning-strategy": "canary"},
        {"schedule": None},
    ],
)
def test_bad_entry_is_explicit_and_never_relaxes_policy(settings: dict[str, Any]) -> None:
    planned, warnings = jobs(config(**settings), snapshot(), "gappy/fixture")
    assert not planned
    assert len(warnings) == 1
    assert "canary" not in warnings[0]


def test_duplicate_entry_does_not_duplicate_evidence() -> None:
    source = config()
    source["updates"] *= 2
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert len(planned) == 1
    assert len(warnings) == 1


def test_patch_ignore_is_policy_not_a_miss() -> None:
    planned, _ = jobs(
        config(
            ignore=[
                {
                    "dependency-name": "gappy-*",
                    "update-types": ["version-update:semver-patch"],
                }
            ]
        ),
        snapshot(),
        "gappy/fixture",
    )
    observations, _ = dependabot(
        (FIXTURES / "dependabot-no-update.jsonl").read_text(),
        "/",
        None,
    )
    assert planned[0].policy(observations)[0].policy == "excluded"


def test_cooldown_without_proposal_remains_unknown() -> None:
    planned, _ = jobs(config(cooldown={"default-days": 7}), snapshot(), "gappy/fixture")
    observations, _ = dependabot(
        (FIXTURES / "dependabot-no-update.jsonl").read_text(),
        "/",
        None,
    )
    result = planned[0].policy(observations)[0]
    assert result.policy == "unknown"
    assert not result.lookupComplete


COOLDOWN_LOG = "updater | 2026/11/16 21:14:11 INFO {}"


@pytest.mark.parametrize(
    "message",
    [
        "Filtered out 3 versions due to cooldown",
        "Filtered out 1 version due to cooldown",
        "Filtered out 2 version(s) due to cooldown",
        "All versions filtered by cooldown for gappy-fixture, falling back to current version 1.2",
        "Skipping tag 3.14.0 due to cooldown period",
        "Skipping version 7.1.0 due to cooldown period",
    ],
)
def test_dependabot_cooldown_filtering_is_detected(message: str) -> None:
    assert dependabot_cooldown_filtered(COOLDOWN_LOG.format(message))


@pytest.mark.parametrize(
    "line",
    [
        COOLDOWN_LOG.format("Filtering versions in cooldown period from chart"),
        COOLDOWN_LOG.format("Filtered out 0 ignored versions"),
        "proxy | 2026/11/16 21:14:11 INFO Filtered out 3 versions due to cooldown",
        "updater | 2026/11/16 21:14:11 ERROR Filtered out 3 versions due to cooldown",
        '{"msg": "Filtered out 3 versions due to cooldown"}',
    ],
)
def test_unrelated_or_untrusted_cooldown_text_is_ignored(line: str) -> None:
    assert not dependabot_cooldown_filtered(line)


def cooldown_probe() -> dict[tuple[str, str, str], list[Candidate]]:
    observations, complete = dependabot(
        (FIXTURES / "dependabot-patch.jsonl").read_text(), "/", None, evidence_prefix="probe"
    )
    assert complete
    return {
        (
            item.dependency.package,
            item.dependency.manifestPath,
            item.dependency.installedVersion,
        ): item.candidates
        for item in observations
    }


def test_cooldown_withheld_version_is_deferred_recommendation() -> None:
    planned, _ = jobs(config(cooldown={"default-days": 7}), snapshot(), "gappy/fixture")
    probe = cooldown_probe()
    observations, _ = dependabot(
        (FIXTURES / "dependabot-patch.jsonl").read_text().splitlines()[0]
        + '\n{"type": "mark_as_processed", "data": {}}',
        "/",
        None,
    )
    result = planned[0].policy(observations, cooldown_probe=probe)[0]
    assert result.policy == "deferred"
    assert result.lookupComplete
    assert [c.targetVersion for c in result.candidates] == [
        c.targetVersion for c in next(iter(probe.values()))
    ]
    assert "Native cooldown withheld" in result.reason


def test_cooldown_comparison_without_proposal_confirms_no_update() -> None:
    planned, _ = jobs(config(cooldown={"default-days": 7}), snapshot(), "gappy/fixture")
    observations, _ = dependabot((FIXTURES / "dependabot-no-update.jsonl").read_text(), "/", None)
    result = planned[0].policy(observations, cooldown_probe={})[0]
    assert result.policy == "eligible"
    assert result.lookupComplete
    assert "without cooldown also proposes no update" in result.reason


def test_cooldown_comparison_cannot_clear_allow_filter() -> None:
    planned, _ = jobs(
        config(cooldown={"default-days": 7}, allow=[{"dependency-name": "other"}]),
        snapshot(),
        "gappy/fixture",
    )
    observations, _ = dependabot((FIXTURES / "dependabot-no-update.jsonl").read_text(), "/", None)
    result = planned[0].policy(observations, cooldown_probe={})[0]
    assert result.policy == "unknown"
    assert not result.lookupComplete


def docker_evidence(
    *messages: str,
    complete: bool = True,
    manifest: str = "docker-compose-test.yml",
) -> str:
    events = [
        {
            "type": "update_dependency_list",
            "data": {
                "dependencies": [
                    {
                        "name": "wiremock/wiremock",
                        "version": "3.13.2",
                        "requirements": [
                            {"file": manifest, "source": {"tag": "3.13.2"}}
                        ],
                    }
                ]
            },
        }
    ]
    if complete:
        events.append({"type": "mark_as_processed", "data": {}})
    return "\n".join(
        [f"updater | 2026/11/16 21:14:11 INFO {message}" for message in messages]
        + [json.dumps(event) for event in events]
    )


def test_workflow_docker_entry_covers_workflow_image() -> None:
    config_path = Path(__file__).parents[1] / "optimised/dependabot.yml"
    planned, warnings = jobs(yaml.safe_load(config_path.read_text()), snapshot(), "gappy/fixture")
    workflow_job = next(
        job
        for job in planned
        if job.ecosystem == "docker" and job.directory == "/.github/workflows"
    )
    observations, _ = dependabot(
        docker_evidence(
            "Checking if redis 7.4 needs updating",
            "Latest version is 8.10",
            manifest="node_integration_tests_redis.yml",
        ),
        workflow_job.directory,
        None,
        ecosystem="docker",
    )

    assert not warnings
    assert observations[0].dependency.manifestPath == (
        ".github/workflows/node_integration_tests_redis.yml"
    )
    assert len(workflow_job.policy(observations)) == 1


def test_exclude_paths_filter_compose_files_from_docker_observations() -> None:
    source = config(
        **{
            "package-ecosystem": "docker",
            "exclude-paths": ["docker-compose*.yml"],
        }
    )
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    observations, _ = dependabot(
        docker_evidence(
            "Checking if wiremock/wiremock 3.13.2 needs updating",
            "Latest version is 3.13.2",
            "No update needed for wiremock/wiremock 3.13.2",
        ),
        "/",
        None,
        ecosystem="docker",
    )

    assert not warnings
    assert planned[0].policy(observations) == []


@pytest.mark.parametrize("ecosystem", ["docker", "docker-compose"])
def test_explicit_docker_no_update_survives_cooldown_and_allow(ecosystem: str) -> None:
    text = docker_evidence(
        "Checking if wiremock/wiremock 3.13.2 needs updating",
        "Latest version is 3.13.2",
        "No update needed for wiremock/wiremock 3.13.2",
    )
    observations, _ = dependabot(text, "/", None, ecosystem=ecosystem)
    planned, warnings = jobs(
        config(
            **{
                "package-ecosystem": ecosystem,
                "cooldown": {"default-days": 7},
                "allow": [{"dependency-type": "direct"}],
            }
        ),
        snapshot(),
        "gappy/fixture",
    )
    assert not warnings
    result = planned[0].policy(observations, docker_no_updates=dependabot_docker_no_updates(text))[
        0
    ]
    assert result.policy == "eligible"
    assert result.lookupComplete
    assert not result.candidates
    assert "explicitly confirmed no update needed" in result.reason


@pytest.mark.parametrize(
    "messages",
    [
        [],
        ["No update needed for wiremock/wiremock 3.13.2"],
        [
            "Checking if wiremock/wiremock 3.13.2 needs updating",
            "No update needed for wiremock/wiremock 3.13.2",
        ],
        [
            "Checking if wiremock/wiremock 3.13.2 needs updating",
            "Latest version is 4.0.0",
            "No update needed for wiremock/wiremock 3.13.2",
        ],
        [
            "Checking if wiremock/wiremock 3.13.2 needs updating",
            "Skipping tag 4.0.0 due to cooldown period",
            "Latest version is 3.13.2",
            "No update needed for wiremock/wiremock 3.13.2",
        ],
        [
            "Checking if other/image 3.13.2 needs updating",
            "Latest version is 3.13.2",
            "No update needed for wiremock/wiremock 3.13.2",
        ],
        [
            "Checking if wiremock/wiremock 3.13.2 needs updating",
            "Latest version is 3.13.2",
            "All updates for wiremock/wiremock were ignored",
            "No update needed for wiremock/wiremock 3.13.2",
        ],
    ],
)
def test_ambiguous_docker_no_update_evidence_stays_unknown(messages: list[str]) -> None:
    assert not dependabot_docker_no_updates(docker_evidence(*messages))


@pytest.mark.parametrize("condition", ["incomplete", "failed", "ignore", "npm", "pattern"])
def test_docker_confirmation_does_not_override_other_guards(condition: str) -> None:
    text = docker_evidence(
        "Checking if wiremock/wiremock 3.13.2 needs updating",
        "Latest version is 3.13.2",
        "No update needed for wiremock/wiremock 3.13.2",
        complete=condition != "incomplete",
    )
    if condition == "failed":
        text += '\n{"type":"record_update_job_error","data":{"error-type":"unknown_error"}}'
    observations, _ = dependabot(text, "/", None, ecosystem="docker")
    settings: dict[str, Any] = {
        "package-ecosystem": "npm" if condition == "npm" else "docker",
        "cooldown": {"default-days": 7},
    }
    if condition == "ignore":
        settings["ignore"] = [{"dependency-name": "*"}]
    source = (
        multi_config(patterns=["other/*"], **settings)
        if condition == "pattern"
        else config(**settings)
    )
    planned, warnings = jobs(source, snapshot(), "gappy/fixture")
    assert not warnings
    result = planned[0].policy(observations, docker_no_updates=dependabot_docker_no_updates(text))[
        0
    ]
    assert result.policy == ("excluded" if condition in {"ignore", "pattern"} else "unknown")
    if condition not in {"ignore", "pattern"}:
        assert not result.lookupComplete


def test_cooldown_publication_date_warning_is_surfaced_without_raw_details() -> None:
    text = json.dumps(
        {
            "type": "record_update_job_warning",
            "data": {"warn-type": "cooldown_date_unavailable", "warn-description": "secret-canary"},
        }
    )
    messages = diagnostics("dependabot", text)
    assert len(messages) == 1
    assert "cooldown_date_unavailable" in messages[0]
    assert "secret-canary" not in messages[0]


def test_docker_status_logs_cannot_be_spoofed_by_other_output() -> None:
    text = docker_evidence(
        "Checking if wiremock/wiremock 3.13.2 needs updating",
        "Latest version is 3.13.2",
        "No update needed for wiremock/wiremock 3.13.2",
    )
    assert not dependabot_docker_no_updates(text.replace("updater |", "proxy |"))
    assert not dependabot_docker_no_updates(text.replace("INFO", "ERROR"))


def test_later_docker_warning_invalidates_prior_confirmation() -> None:
    text = docker_evidence(
        "Checking if wiremock/wiremock 3.13.2 needs updating",
        "Latest version is 3.13.2",
        "No update needed for wiremock/wiremock 3.13.2",
    )
    text += "\nupdater | 2026/11/16 21:14:11 WARN Registry evidence incomplete"
    assert not dependabot_docker_no_updates(text)


def test_docker_orchestration_preserves_explicit_completed_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    cfg = config(**{"package-ecosystem": "docker", "cooldown": {"default-days": 7}})
    (source / ".github/dependabot.yml").write_text(yaml.safe_dump(cfg))
    output = docker_evidence(
        "Checking if wiremock/wiremock 3.13.2 needs updating",
        "Latest version is 3.13.2",
        "No update needed for wiremock/wiremock 3.13.2",
    )
    monkeypatch.setattr("gappy.scan.session.run", lambda *args, **kwargs: (0, output))
    result = execute(
        "dependabot", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "complete"
    assert result.observations[0].policy == "eligible"
    assert result.observations[0].lookupComplete
    assert not result.observations[0].candidates


def test_unsupported_entry_does_not_prevent_native_npm_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    cfg = config(groups={"all": {"patterns": ["*"]}})
    cfg["updates"].append({"package-ecosystem": "docker"})
    (source / ".github/dependabot.yml").write_text(yaml.safe_dump(cfg))
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        job_path = Path(args[args.index("--file") + 1])
        assert yaml.safe_load(job_path.read_text())["job"]["dependency-groups"]
        return 0, (FIXTURES / "dependabot-patch.jsonl").read_text()

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot",
        source,
        snapshot(),
        "fixture",
        "gappy/fixture",
        10,
        toolchain(),
        None,
    )
    assert len(calls) == 1
    assert result.status == "partial"
    assert result.observations[0].candidates[0].targetVersion == "1.2.2"
    assert any("entry 2" in warning for warning in result.warnings)


def test_authentication_failure_retains_discovery_and_explains_partial_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    source = tmp_path / "source"
    fixture_repo(source)
    output = "\n".join(
        line
        for line in (FIXTURES / "dependabot-no-update.jsonl").read_text().splitlines()
        if json.loads(line).get("type") != "mark_as_processed"
    )
    output += (
        '\n{"type":"record_update_job_error","data":'
        '{"error-type":"private_source_authentication_failure",'
        '"error-details":{"secret":"secret-canary"}}}\n'
    )
    monkeypatch.setattr("gappy.scan.session.run", lambda *args, **kwargs: (1, output))
    result = execute(
        "dependabot", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "partial"
    assert result.observations
    assert all(not item.lookupComplete for item in result.observations)
    assert any("private_source_authentication_failure" in warning for warning in result.warnings)
    assert "secret-canary" not in " ".join(result.warnings)
    assert not any("completion evidence is missing" in warning for warning in result.warnings)
