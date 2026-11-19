"""Four-way forecasts retain proposal grouping, uncertainty and offline reproducibility."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from gappy.cli import main
from gappy.contracts import Provider, Scan, timestamp
from gappy.forecast.config import baseline, policies, resolve
from gappy.forecast.engine import due, forecast, forecast_variant, schedule_error
from gappy.forecast.github import capture
from gappy.forecast.models import (
    ForecastInput,
    Member,
    PlatformState,
    Policy,
    Proposal,
    PullRequest,
    Variant,
    Window,
)
from gappy.forecast.proposals import extract
from gappy.forecast.report import render
from gappy.io import GappyError, dump
from gappy.scan.snapshot import freeze
from tests.test_scan import FIXTURES, fixture_repo

START = "2026-11-16T09:00:00+00:00"
END = "2026-11-17T09:00:00+00:00"
CAPTURE = "2026-11-12T10:00:00+00:00"


def proposal(n: int = 0, ecosystem: str = "npm", branch: str | None = None) -> Proposal:
    return Proposal(
        id=hashlib.sha256(str(n).encode()).hexdigest(),
        ecosystem=ecosystem,
        directory="/",
        branch=branch,
        members=[Member(package=f"fixture-{n}", target="2.0.0", manifest="package.json")],
    )


def variant(provider: Provider = "dependabot", count: int = 3) -> Variant:
    scan = Scan(
        schemaVersion="1.1.0",
        provider=provider,
        sessionId="forecast",
        repository="gappy/fixture",
        snapshot={"revision": "a" * 40, "contentHash": "b" * 64, "configHashes": {}},
        toolchain={},
        startedAt=CAPTURE,
        finishedAt=CAPTURE,
        cacheMode="fresh",
        status="complete",
        observations=[],
        warnings=[],
    )
    ecosystem = "npm" if provider == "dependabot" else "all-managers"
    return Variant(
        label="baseline",
        provider=provider,
        configOrigin="repository:config",
        configHash="c" * 64,
        scan=scan,
        proposals=[proposal(n, ecosystem) for n in range(count)],
        policies=[Policy(ecosystem=ecosystem, directory="/", openLimit=2)],
        proposalEvidenceComplete=True,
        warnings=[],
    )


def inputs() -> ForecastInput:
    variants = []
    for provider in ("dependabot", "renovate"):
        v = variant("dependabot" if provider == "dependabot" else "renovate")
        variants.extend([v, v.model_copy(update={"label": "optimised"})])
    return ForecastInput(
        repository="gappy/fixture",
        revision="a" * 40,
        sourceHash="b" * 64,
        window=Window(start=START, end=END),
        platform=PlatformState(
            repository="gappy/fixture",
            capturedAt=CAPTURE,
            baseBranch="main",
            complete=True,
            pullRequests=[],
            warnings=[],
        ),
        variants=variants,
    )


def test_distinct_group_demand_not_dependency_count() -> None:
    event = {
        "type": "create_pull_request",
        "data": {
            "dependencies": [
                {"name": "a", "version": "2"},
                {"name": "b", "version": "3"},
            ]
        },
    }
    output = dump(event).replace("\n", "") + '\n{"type":"mark_as_processed","data":{}}'
    proposals, complete = extract("dependabot", "npm", "/", output)
    assert complete and len(proposals) == 1
    assert len(proposals[0].members) == 2
    assert extract("dependabot", "npm", "/", output + "\n" + json.dumps(event))[0] == proposals


def test_real_sanitized_proposal_captures() -> None:
    for provider, name in (
        ("dependabot", "dependabot-group-0.jsonl"),
        ("renovate", "renovate-group-1.jsonl"),
    ):
        output = (FIXTURES / "policy/group" / name).read_text()
        proposals, complete = extract(provider, "npm", "/", output)
        assert complete and len(proposals) == 1
        assert proposals[0].members[0].package == "gappy-fixture"


def test_missing_evidence_and_native_errors_are_not_zero() -> None:
    assert extract("renovate", "all-managers", "/", "") == ([], False)
    data = inputs()
    v = data.variants[0].model_copy(update={"proposalEvidenceComplete": False})
    result = forecast_variant(data, v)
    assert result.potentialPRs is None and result.predictedNew is None
    assert result.unknown == 3


def test_repeated_invocations_do_not_recount_and_capacity_leaves_queue() -> None:
    result = forecast_variant(inputs(), variant())
    assert (result.potentialPRs, result.predictedNew, result.queued, result.deferred) == (
        3,
        2,
        1,
        0,
    )
    assert sum(d.state == "predicted-new" for d in result.decisions) == 2


def test_renovate_hourly_limit_resets_and_zero_concurrency_is_unlimited() -> None:
    data = inputs()
    v = variant("renovate", 3)
    v = v.model_copy(
        update={
            "policies": [
                Policy(
                    ecosystem="all-managers",
                    directory="/",
                    openLimit=0,
                    hourlyLimit=1,
                )
            ]
        }
    )
    result = forecast_variant(data, v)
    assert result.predictedNew == 3 and result.queued == 0
    assert [d.at for d in result.decisions] == [
        "2026-11-16T09:00:00+00:00",
        "2026-11-16T10:00:00+00:00",
        "2026-11-16T11:00:00+00:00",
    ]


def test_dependabot_zero_limit_queues_instead_of_creating() -> None:
    v = variant().model_copy(
        update={
            "policies": [
                Policy(
                    ecosystem="npm",
                    directory="/",
                    openLimit=0,
                )
            ]
        }
    )
    result = forecast_variant(inputs(), v)
    assert result.predictedNew == 0 and result.queued == 3


def test_schedule_timezone_and_deferred_window() -> None:
    policy = Policy(
        ecosystem="npm",
        directory="/",
        openLimit=5,
        interval="weekly",
        day="tuesday",
        time="09:00",
        timezone="Europe/London",
    )
    v = variant().model_copy(update={"policies": [policy]})
    result = forecast_variant(inputs(), v)
    assert result.predictedNew == 0 and result.deferred == 3
    assert not due(timestamp("2026-03-30T07:59:00Z"), policy.model_copy(update={"day": "monday"}))
    assert due(timestamp("2026-03-30T08:00:00Z"), policy.model_copy(update={"day": "monday"}))


@pytest.mark.parametrize(
    "changes",
    [
        {"interval": "cron"},
        {"timezone": "not/a/timezone"},
        {"time": "25:00"},
        {"day": "someday"},
    ],
)
def test_unsupported_schedules_are_unknown(changes: dict[str, str]) -> None:
    p = variant().policies[0].model_copy(update=changes)
    assert schedule_error(p)
    result = forecast_variant(inputs(), variant().model_copy(update={"policies": [p]}))
    assert result.predictedNew is None


def test_incomplete_platform_does_not_substitute_empty_state() -> None:
    data = inputs()
    data = data.model_copy(
        update={"platform": data.platform.model_copy(update={"complete": False})}
    )
    assert forecast_variant(data, variant()).predictedNew is None


def test_existing_branch_matches_and_ambiguous_dependabot_state_is_unknown() -> None:
    data = inputs()
    pr = PullRequest(
        number=1,
        provider="renovate",
        branch="renovate/fixture",
        base="main",
        state="open",
        createdAt=CAPTURE,
    )
    data = data.model_copy(
        update={"platform": data.platform.model_copy(update={"pullRequests": [pr]})}
    )
    v = variant("renovate", 1).model_copy(
        update={"proposals": [proposal(0, "all-managers", "renovate/fixture")]}
    )
    result = forecast_variant(data, v)
    assert result.existing == 1 and result.predictedNew == 0
    pr = pr.model_copy(update={"provider": "dependabot"})
    data = data.model_copy(
        update={"platform": data.platform.model_copy(update={"pullRequests": [pr]})}
    )
    result = forecast_variant(data, variant())
    assert result.predictedNew is None and result.unknown == 3


def test_closed_history_prevents_unproven_creation_claim() -> None:
    data = inputs()
    pr = PullRequest(
        number=1,
        provider="dependabot",
        branch="dependabot/npm/a",
        base="main",
        state="closed",
        createdAt=CAPTURE,
    )
    data = data.model_copy(
        update={"platform": data.platform.model_copy(update={"pullRequests": [pr]})}
    )
    assert forecast_variant(data, variant()).predictedNew is None


def test_empty_proposals_with_unresolved_policy_are_not_exact_zero() -> None:
    v = variant(count=0)
    p = v.policies[0].model_copy(update={"unknown": ["Unresolved age policy."]})
    result = forecast_variant(inputs(), v.model_copy(update={"policies": [p]}))
    assert result.predictedNew is None


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-11-16", END),
        (END, START),
        (START, START),
        (START, "2036-11-16T09:00:00Z"),
    ],
)
def test_invalid_or_unbounded_windows_are_rejected(start: str, end: str) -> None:
    with pytest.raises(ValidationError):
        Window(start=start, end=end)


def test_incompatible_inputs_rejected() -> None:
    data = inputs().model_dump()
    data["variants"].pop()
    with pytest.raises(ValidationError):
        ForecastInput.model_validate(data)
    data = inputs().model_dump()
    data["variants"][0]["scan"]["snapshot"]["revision"] = "d" * 40
    with pytest.raises(ValidationError):
        ForecastInput.model_validate(data)
    data = inputs().model_dump()
    data["window"]["start"] = "2026-11-01T00:00:00Z"
    with pytest.raises(ValidationError):
        ForecastInput.model_validate(data)


def test_config_precedence_is_independent_and_invalid_never_falls_back(tmp_path: Path) -> None:
    root, fallback, optimised, output = [tmp_path / n for n in ("root", "fallback", "opt", "out")]
    for path in (root, fallback, optimised, output):
        path.mkdir()
    for directory in (fallback, optimised):
        (directory / "dependabot.yml").write_text("version: 2\nupdates: []")
        (directory / "renovate.json").write_text("{}")
    (root / ".github").mkdir()
    (root / ".github/dependabot.yaml").write_text("version: 2\nupdates: []")
    configs = resolve(root, fallback, optimised, output)
    assert configs["baseline", "dependabot"][0] == "repository:.github/dependabot.yaml"
    assert configs["baseline", "renovate"][0].startswith("fallback:")
    assert configs["optimised", "renovate"][0].startswith("optimised:")
    (root / "renovate.json").write_text("{invalid")
    with pytest.raises(GappyError, match="no fallback"):
        baseline(root, fallback, "renovate")


def test_embedded_conflicting_and_unsupported_config_detection(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"renovate":{"enabled":false}}')
    assert baseline(tmp_path, tmp_path, "renovate")[1].endswith("package.json#renovate")
    (tmp_path / "renovate.json5").write_text("{}")
    with pytest.raises(GappyError, match="ambiguous"):
        baseline(tmp_path, tmp_path, "renovate")
    (tmp_path / "package.json").unlink()
    with pytest.raises(GappyError, match="no fallback"):
        baseline(tmp_path, tmp_path, "renovate")


def test_multi_ecosystem_forecast_uses_group_schedule_but_marks_shared_prs_unknown() -> None:
    from tests.test_dependabot import multi_config

    source = multi_config()
    source["updates"][0]["schedule"] = {"interval": "daily"}
    source["updates"][0]["open-pull-requests-limit"] = 10
    source["multi-ecosystem-groups"]["infrastructure"]["schedule"] = {
        "interval": "weekly",
        "day": "friday",
        "time": "10:00",
        "timezone": "Europe/London",
    }
    policy = policies("dependabot", source)[0]
    assert policy.interval == "weekly"
    assert policy.day == "friday"
    assert policy.time == "10:00"
    assert policy.timezone == "Europe/London"
    assert policy.openLimit == 5
    assert any("Cross-ecosystem" in warning for warning in policy.unknown)


def test_unknown_future_policies_remain_explicit() -> None:
    assert policies("renovate", {"extends": ["config:recommended"]})[0].unknown
    assert policies("renovate", {"schedule": ["before 5am"]})[0].unknown
    p = policies(
        "dependabot",
        {
            "updates": [
                {
                    "package-ecosystem": "npm",
                    "directory": "/",
                    "schedule": {"interval": "weekly"},
                    "cooldown": {"default-days": 7},
                }
            ]
        },
    )[0]
    assert len(p.unknown) == 2


def test_capture_is_read_only_paginated_and_never_puts_token_in_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        assert "synthetic-token" not in str(args)
        assert kwargs["env"]["GH_TOKEN"] == "synthetic-token"
        assert "--method" not in args
        return (0, "main\n") if "--paginate" not in args else (0, "[]")

    monkeypatch.setattr("gappy.forecast.github.run", run)
    state = capture("gappy/fixture", "synthetic-token")
    assert state.complete and len(calls) == 2
    assert "--paginate" in calls[1] and "--slurp" in calls[1]
    assert "synthetic-token" not in state.model_dump_json()
    assert not capture("gappy/fixture", None).complete
    assert len(calls) == 2


def test_capture_failure_is_visible_not_success_shaped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gappy.forecast.github.run", lambda *a, **k: (1, "secret-canary"))
    state = capture("gappy/fixture", "synthetic-token")
    assert not state.complete and state.warnings
    assert "secret-canary" not in state.model_dump_json()


def test_offline_replay_exact_results_and_no_external_assets(tmp_path: Path) -> None:
    data = inputs()
    input_path = tmp_path / "input.json"
    input_path.write_text(dump(data.model_dump()))
    output = tmp_path / "output"
    output.mkdir()
    result = CliRunner().invoke(
        main,
        [
            "forecast-replay",
            "--input",
            str(input_path),
            "--output-dir",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads((output / "forecast.json").read_text()) == forecast(data)
    html = (output / "forecast.html").read_text()
    header = html.split("</thead>", 1)[0]
    assert header.count("/ baseline</th>") == 2 and header.count("/ optimised</th>") == 2
    assert 'src="http' not in html and 'href="http' not in html and "<script" not in html
    assert "Predictions, not actual PRs" in html
    assert len(list(output.glob("*.scan.json"))) == 4
    assert (
        CliRunner()
        .invoke(
            main,
            [
                "forecast-replay",
                "--input",
                str(input_path),
                "--output-dir",
                str(output),
            ],
        )
        .exit_code
        != 0
    )


def test_report_escapes_native_members() -> None:
    data = inputs()
    v = data.variants[0]
    member = Member(package="<script>", target="2", manifest=".")
    changed = v.proposals[0].model_copy(update={"members": [member]})
    v = v.model_copy(update={"proposals": [changed]})
    data = data.model_copy(update={"variants": [v, *data.variants[1:]]})
    assert "&lt;script&gt;" in render(data) and "<script>" not in render(data)


def test_snapshot_can_freeze_missing_configs_without_weakening_existing_default(
    tmp_path: Path,
) -> None:
    import subprocess

    root = tmp_path / "repo"
    fixture_repo(root)
    (root / "renovate.json").unlink()
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Missing config",
        ],
        check=True,
        capture_output=True,
    )
    out = tmp_path / "out"
    out.mkdir()
    snapshot = freeze(root, "HEAD", out, require_configs=False)
    assert "renovate.json" not in snapshot.configHashes
    out2 = tmp_path / "out2"
    out2.mkdir()
    with pytest.raises(GappyError, match="Revision must contain"):
        freeze(root, "HEAD", out2)


@pytest.mark.parametrize("dependabot_present", [False, True])
@pytest.mark.parametrize("renovate_present", [False, True])
def test_all_baseline_fallback_combinations(
    tmp_path: Path,
    dependabot_present: bool,
    renovate_present: bool,
) -> None:
    root = tmp_path / "source"
    fallback = tmp_path / "fallback"
    root.mkdir()
    fallback.mkdir()
    (root / ".github").mkdir()
    (fallback / "dependabot.yml").write_text("version: 2\nupdates: []")
    (fallback / "renovate.json").write_text("{}")
    if dependabot_present:
        (root / ".github/dependabot.yml").write_text("version: 2\nupdates: []")
    if renovate_present:
        (root / ".github/renovate.json").write_text("{}")
    for provider, present in (("dependabot", dependabot_present), ("renovate", renovate_present)):
        _, origin = baseline(root, fallback, provider)
        assert origin.startswith("repository:" if present else "fallback:")


def test_normalized_configs_remove_alternates_only_in_disposable_snapshot(tmp_path: Path) -> None:
    import subprocess

    root = tmp_path / "source"
    fixture_repo(root)
    (root / ".github/dependabot.yml").rename(root / ".github/dependabot.yaml")
    (root / "renovate.json").rename(root / ".github/renovate.json")
    package = json.loads((root / "package.json").read_text())
    package["renovate"] = {"enabled": False}
    (root / "package.json").write_text(json.dumps(package))
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Alternate configurations",
        ],
        check=True,
        capture_output=True,
    )
    supplied = tmp_path / "configs"
    supplied.mkdir()
    (supplied / "renovate.json").write_text("{}")
    (supplied / "dependabot.yml").write_text("version: 2\nupdates: []")
    out = tmp_path / "out"
    out.mkdir()
    freeze(root, "HEAD", out, config_dir=supplied, replace_alternate_configs=True)
    assert (out / ".github/dependabot.yml").is_file()
    assert not (out / ".github/dependabot.yaml").exists()
    assert not (out / ".github/renovate.json").exists()
    assert "renovate" not in json.loads((out / "package.json").read_text())
    assert (root / ".github/dependabot.yaml").is_file()
    assert (root / ".github/renovate.json").is_file()
    assert "renovate" in json.loads((root / "package.json").read_text())


@pytest.mark.parametrize("verbose", [False, True])
def test_forecast_cli_forwards_options_and_publishes_all_four(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verbose: bool,
) -> None:
    data = inputs()
    seen = []

    def collect(*args: Any, **kwargs: Any) -> ForecastInput:
        seen.append((args, kwargs))
        return data

    monkeypatch.setattr("gappy.forecast.collect.collect", collect)
    result = CliRunner().invoke(
        main,
        [
            "forecast",
            "--repository",
            "gappy/fixture",
            "--source",
            str(tmp_path),
            "--fallback-config-dir",
            str(tmp_path),
            "--window-start",
            START,
            "--window-end",
            END,
            "--no-mirror",
            "--no-cache",
            "--dependabot-workers",
            "1",
            "--renovate-output-limit-mib",
            "32",
            "--output-dir",
            str(tmp_path),
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert not seen[0][1]["cache"] and not seen[0][1]["mirror"]
    assert seen[0][1]["dependabot_workers"] == 1
    assert seen[0][1]["renovate_output_limit_mib"] == 32
    assert seen[0][1]["log_dir"] == tmp_path / "logs"
    assert len(list(tmp_path.glob("*.scan.json"))) == 4
    assert (tmp_path / "forecast.html").is_file()
    from tests.test_report_filter import FilterMarkup

    parsed = FilterMarkup()
    parsed.feed((tmp_path / "forecast.html").read_text())
    assert parsed.checkbox is not None
    assert ("checked" in parsed.checkbox) is not verbose


@pytest.mark.parametrize("verbose", [False, True])
def test_forecast_replay_filter_preserves_evidence(tmp_path: Path, verbose: bool) -> None:
    from gappy.report.filter import filter_token
    from tests.test_compare import scan
    from tests.test_report_filter import FilterMarkup

    data = inputs()
    for index, variant in enumerate(data.variants):
        data.variants[index] = variant.model_copy(
            update={
                "scan": variant.scan.model_copy(
                    update={
                        "observations": scan(variant.provider, [None, "2.0.0", None]).observations
                    }
                )
            }
        )
    data.variants[-1].scan.observations.pop()
    original = dump(data.model_dump())
    source = tmp_path / "input.json"
    source.write_text(original)
    result = CliRunner().invoke(
        main,
        [
            "forecast-replay",
            "--input",
            str(source),
            "--output-dir",
            str(tmp_path),
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = FilterMarkup()
    output = (tmp_path / "forecast.html").read_text()
    parsed.feed(output)
    assert parsed.rows == ["false", "true", "true"]
    assert [row["data-filter-ecosystem"] for row in parsed.filter_rows] == [filter_token("npm")] * 3
    assert [row["data-filter-status"] for row in parsed.filter_rows] == [
        filter_token("No recommendation observed"),
        filter_token("Recommendation observed"),
        filter_token("No recommendation observed"),
    ]
    assert f"filter-status-{filter_token('Recommendation observed')}" in output
    assert f"filter-status-{filter_token('No recommendation observed')}" in output
    assert "No dependencies match the selected filters." in output
    assert "<script" not in output
    assert parsed.checkbox is not None
    assert ("checked" in parsed.checkbox) is not verbose
    assert (tmp_path / "forecast-input.json").read_text() == original
    assert (tmp_path / "forecast.json").read_text() == dump(forecast(data))


def test_non_success_forecast_cli_keeps_diagnostic_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = inputs()
    data = data.model_copy(
        update={"platform": data.platform.model_copy(update={"complete": False})}
    )
    monkeypatch.setattr("gappy.forecast.collect.collect", lambda *args, **kwargs: data)
    result = CliRunner().invoke(
        main,
        [
            "forecast",
            "--repository",
            "gappy/fixture",
            "--source",
            str(tmp_path),
            "--fallback-config-dir",
            str(tmp_path),
            "--window-start",
            START,
            "--window-end",
            END,
            "--no-mirror",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert (tmp_path / "forecast.html").is_file()


@pytest.mark.parametrize(
    "setting",
    [
        {"prApproval": True},
        {"prNotPendingHours": 24},
        {"lockFileMaintenance": {"enabled": True}},
        {"unknownFuturePolicy": True},
    ],
)
def test_unmodeled_renovate_policy_cannot_claim_exact_creation(
    setting: dict[str, Any],
) -> None:
    selected = policies("renovate", setting)
    assert selected[0].unknown
    result = forecast_variant(
        inputs(),
        variant("renovate", 1).model_copy(update={"policies": selected}),
    )
    assert result.potentialPRs == 1
    assert result.predictedNew is None and result.queued is None


def test_duplicate_policy_scope_is_rejected_on_replay() -> None:
    data = variant().model_dump()
    data["policies"] *= 2
    with pytest.raises(ValidationError, match="Duplicate policy"):
        Variant.model_validate(data)


def test_exact_existing_match_does_not_depend_on_future_schedule_policy() -> None:
    data = inputs()
    pr = PullRequest(
        number=1,
        provider="renovate",
        branch="renovate/fixture",
        base="main",
        state="open",
        createdAt=CAPTURE,
    )
    data = data.model_copy(
        update={
            "platform": data.platform.model_copy(update={"pullRequests": [pr]}),
        }
    )
    v = variant("renovate", 1)
    v = v.model_copy(
        update={
            "proposals": [proposal(0, "all-managers", "renovate/fixture")],
            "policies": [
                v.policies[0].model_copy(update={"unknown": ["Unresolved future schedule."]})
            ],
        }
    )
    result = forecast_variant(data, v)
    assert result.existing == 1
    assert result.predictedNew is None


def test_platform_snapshot_rejects_duplicate_prs() -> None:
    data = inputs().platform.model_dump()
    pr = PullRequest(
        number=1,
        provider="renovate",
        branch="renovate/fixture",
        base="main",
        state="open",
        createdAt=CAPTURE,
    ).model_dump()
    data["pullRequests"] = [pr, pr]
    with pytest.raises(ValidationError, match="Duplicate PR"):
        PlatformState.model_validate(data)


def test_cancelled_forecast_stops_scans_and_keeps_all_four_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gappy.forecast.collect import collect

    source = tmp_path / "source"
    fallback = tmp_path / "fallback"
    optimised = tmp_path / "optimised"
    fallback.mkdir()
    optimised.mkdir()
    fixture_repo(source)
    for directory in (fallback, optimised):
        (directory / "renovate.json").write_text("{}")
        (directory / "dependabot.yml").write_text(
            "version: 2\nupdates:\n  - package-ecosystem: npm\n    directory: /\n"
            "    schedule:\n      interval: daily\n      time: '09:00'\n"
        )
    calls = []

    def run_scans(*args: Any, **kwargs: Any) -> tuple[Scan, Scan]:
        calls.append(kwargs)
        left, right = variant().scan, variant("renovate").scan
        left = left.model_copy(
            update={
                "status": "cancelled",
                "warnings": ["Interrupted."],
                "snapshot": left.snapshot.model_copy(update={"revision": args[1]}),
            }
        )
        right = right.model_copy(
            update={
                "status": "cancelled",
                "warnings": ["Not run."],
                "snapshot": right.snapshot.model_copy(update={"revision": args[1]}),
            }
        )
        return left, right

    monkeypatch.setattr("gappy.forecast.collect.scan", run_scans)
    monkeypatch.setattr("gappy.forecast.collect.capture", lambda *args: inputs().platform)
    result = collect(
        source,
        "HEAD",
        "gappy/fixture",
        fallback,
        optimised,
        inputs().window,
        None,
        mirror=False,
        dependabot_workers=1,
        renovate_output_limit_mib=32,
        log_dir=tmp_path / "logs",
    )
    assert len(calls) == 1
    assert calls[0]["dependabot_workers"] == 1
    assert calls[0]["renovate_output_limit_mib"] == 32
    assert calls[0]["log_dir"] == tmp_path / "logs" / "baseline"
    assert len(result.variants) == 4
    for v in result.variants:
        assert v.scan.status == "cancelled"
        assert not v.proposalEvidenceComplete
        assert forecast_variant(result, v).potentialPRs is None
    for v in result.variants[2:]:
        assert v.scan.warnings[0].startswith("Not run:")


def test_cancelled_forecast_cli_preserves_exit_130_and_diagnostic_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = inputs()
    changed = data.variants[0].model_copy(
        update={
            "scan": data.variants[0].scan.model_copy(
                update={
                    "status": "cancelled",
                    "warnings": ["Interrupted."],
                }
            ),
        }
    )
    data = data.model_copy(update={"variants": [changed, *data.variants[1:]]})
    monkeypatch.setattr("gappy.forecast.collect.collect", lambda *args, **kwargs: data)
    result = CliRunner().invoke(
        main,
        [
            "forecast",
            "--repository",
            "gappy/fixture",
            "--source",
            str(tmp_path),
            "--fallback-config-dir",
            str(tmp_path),
            "--window-start",
            START,
            "--window-end",
            END,
            "--no-mirror",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 130
    assert (tmp_path / "forecast.html").is_file()
    assert len(list(tmp_path.glob("*.scan.json"))) == 4


@pytest.mark.parametrize("difference", ["toolchain", "cacheMode", "transport"])
def test_deltas_require_comparable_execution_settings(difference: str) -> None:
    data = inputs()
    changed = data.variants[1]
    scan = changed.scan
    if difference == "toolchain":
        scan = scan.model_copy(update={"toolchain": {"adapterVersion": "different"}})
    elif difference == "cacheMode":
        scan = scan.model_copy(update={"cacheMode": "persistent"})
    else:
        scan = scan.model_copy(
            update={
                "snapshot": scan.snapshot.model_copy(
                    update={
                        "configHashes": {"gappy:npm-mirror": "f" * 64},
                    }
                ),
            }
        )
    changed = changed.model_copy(update={"scan": scan})
    data = data.model_copy(update={"variants": [data.variants[0], changed, *data.variants[2:]]})
    result = forecast(data)
    assert result["deltas"]["dependabot"] == {"potentialPRs": None, "predictedNew": None}
    assert result["deltas"]["renovate"] == {"potentialPRs": 0, "predictedNew": 0}
    assert result["comparisonWarnings"]
    assert "not comparable" in render(data)
