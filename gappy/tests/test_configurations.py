import json
import subprocess
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.compare.configurations import compare_configurations
from gappy.io import GappyError, dump
from gappy.report.html import render
from tests.test_cli import config_pair, mock_collection
from tests.test_compare import scan
from tests.test_report_filter import FilterMarkup


def saved_pair(directory: Path, label: str, **updates: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".optimised" if label == "optimised" else ""
    for provider in ("dependabot", "renovate"):
        model = scan(provider, [None]).model_copy(update=updates)
        (directory / f"{provider}{suffix}.scan.json").write_text(dump(model.model_dump()))


@pytest.mark.parametrize("selected", ["baseline", "optimised"])
@pytest.mark.parametrize("explicit", [False, True])
def test_full_reruns_selected_variant_and_preserves_other_saved_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str, explicit: bool
) -> None:
    other = "baseline" if selected == "optimised" else "optimised"
    prior = (
        tmp_path / "custom-previous"
        if explicit
        else tmp_path / "gappy-results/gappy/fixture/20260102T000000Z-aaaaaaaaaaaa"
    )
    saved_pair(prior, other)
    before = {p.name: p.read_bytes() for p in prior.iterdir()}
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    # A reused variant does not need its current configuration files.
    config_root = tmp_path if other == "baseline" else tmp_path / "optimised"
    for name in ("dependabot.yml", "renovate.json"):
        (config_root / name).unlink()
    monkeypatch.chdir(tmp_path)
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["cache_scope"])
        return scan("dependabot", ["1.3.0"]), scan("renovate", ["1.3.0"])

    mock_collection(monkeypatch, collect)
    args = [
        "full",
        "--repository",
        "gappy/fixture",
        f"--{selected}",
        "--no-mirror",
        *(["--reuse-from", str(prior)] if explicit else []),
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.exception)
    assert calls == [selected]
    assert "Reused " + other in result.output
    runs = list((tmp_path / "gappy-results/gappy/fixture").iterdir())
    output = next(p for p in runs if p != prior)
    report = json.loads((output / "comparison.json").read_text())
    assert len(report["variants"]) == 4
    for variant in report["variants"]:
        evidence = variant["scan"]
        if variant["label"] == other:
            assert evidence["startedAt"] == "2026-01-02T00:00:00Z"
            assert evidence["snapshot"]["configHashes"] == {}
            assert not evidence["observations"][0]["candidates"]
            assert any("Reused " + other in w for w in evidence["warnings"])
        else:
            assert evidence["observations"][0]["candidates"]
    assert before == {p.name: p.read_bytes() for p in prior.iterdir()}
    assert len(list(output.glob("*.scan.json"))) == 4
    assert f"Reused {other} scan evidence" in (output / "comparison.html").read_text()
    # Offline regeneration preserves the reuse warning.
    replay = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(output / "dependabot.scan.json"),
            "--renovate-json",
            str(output / "renovate.scan.json"),
            "--json",
            str(output / "replay.json"),
            "--html",
            str(output / "replay.html"),
        ],
    )
    assert replay.exit_code == 0, replay.output
    assert (output / "replay.json").read_text() == (output / "comparison.json").read_text()


@pytest.mark.parametrize("selected", ["baseline", "optimised"])
@pytest.mark.parametrize(
    "missing", ["no-run", "missing-pair", "one-export", "revision", "repository", "partial"]
)
def test_full_single_variant_creates_other_pair_when_not_reusable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str, missing: str
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    prior = tmp_path / "gappy-results/gappy/fixture/20260102T000000Z-aaaaaaaaaaaa"
    other = "baseline" if selected == "optimised" else "optimised"
    if missing != "no-run":
        prior.mkdir(parents=True)
    if missing not in {"no-run", "missing-pair"}:
        updates: dict[str, Any] = {}
        if missing == "revision":
            updates["snapshot"] = scan("dependabot", [None]).snapshot.model_copy(
                update={"revision": "c" * 40}
            )
        elif missing == "repository":
            updates["repository"] = "other/repo"
        elif missing == "partial":
            updates.update(status="partial", warnings=["Synthetic incomplete lookup"])
        saved_pair(prior, other, **updates)
        if missing == "one-export":
            (
                prior
                / ("renovate.optimised.scan.json" if other == "optimised" else "renovate.scan.json")
            ).unlink()
    monkeypatch.chdir(tmp_path)
    barrier = Barrier(2)
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["cache_scope"])
        barrier.wait(timeout=5)
        return scan("dependabot", [None]), scan("renovate", [None])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            f"--{selected}",
            "--output-dir",
            str(tmp_path / "new"),
            "--no-mirror",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(calls) == ["baseline", "optimised"]
    assert f"scanning {other} configurations too" in result.output
    assert len(list((tmp_path / "new").glob("*.scan.json"))) == 4


@pytest.mark.parametrize("invalid", ["json", "schema", "session", "provider", "snapshot"])
def test_full_rejects_invalid_reused_evidence_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    prior = tmp_path / "prior"
    saved_pair(prior, "baseline")
    path = prior / "renovate.scan.json"
    data = json.loads(path.read_text())
    if invalid == "session":
        data["sessionId"] = "different"
    elif invalid == "provider":
        data["provider"] = "dependabot"
    elif invalid == "snapshot":
        data["snapshot"]["contentHash"] = "d" * 64
    elif invalid == "schema":
        data.pop("snapshot")
    path.write_text("invalid json" if invalid == "json" else dump(data))
    monkeypatch.chdir(tmp_path)
    mock_collection(monkeypatch, lambda *a, **k: pytest.fail("Must not scan invalid reuse"))
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--optimised",
            "--reuse-from",
            str(prior),
            "--output-dir",
            str(tmp_path / "new"),
            "--no-mirror",
        ],
    )
    assert result.exit_code == 1
    assert "invalid or inconsistent" in result.output
    assert not list((tmp_path / "new").glob("*.scan.json"))


@pytest.mark.parametrize(
    "args",
    [
        ["--baseline", "--optimised"],
        ["--reuse-from", "."],
        ["--baseline", "--inventory", "inventory.json"],
        ["--optimised", "--inventory", "inventory.json"],
    ],
)
def test_full_rejects_conflicting_variant_flags(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "inventory.json").write_text("{}")
    result = CliRunner().invoke(main, ["full", "--repository", "gappy/fixture", *args])
    assert result.exit_code == 2, result.output
    assert (
        "cannot be combined" in result.output
        or "requires" in result.output
        or "not both" in result.output
    )


@pytest.mark.parametrize("selected", ["baseline", "optimised"])
def test_full_selected_variant_will_not_overwrite_prior_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    prior = tmp_path / "prior"
    saved_pair(prior, "baseline")
    saved_pair(prior, "optimised")
    before = {p.name: p.read_bytes() for p in prior.iterdir()}
    monkeypatch.chdir(tmp_path)
    mock_collection(monkeypatch, lambda *a, **k: pytest.fail("Must not overwrite prior run"))
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            f"--{selected}",
            "--reuse-from",
            str(prior),
            "--output-dir",
            str(prior),
            "--no-mirror",
        ],
    )
    assert result.exit_code == 1, result.output
    assert before == {p.name: p.read_bytes() for p in prior.iterdir()}


@pytest.mark.parametrize("status", ["partial", "cancelled"])
def test_full_reuse_does_not_hide_selected_variant_failure_or_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    prior = tmp_path / "prior"
    saved_pair(prior, "baseline")
    monkeypatch.chdir(tmp_path)

    def collect(*args: Any, **kwargs: Any) -> Any:
        assert kwargs["cache_scope"] == "optimised"
        return (
            scan("dependabot", [None]).model_copy(
                update={"status": status, "warnings": ["Synthetic incomplete evidence"]}
            ),
            scan("renovate", [None]),
        )

    mock_collection(monkeypatch, collect)
    output = tmp_path / "new"
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--optimised",
            "--reuse-from",
            str(prior),
            "--output-dir",
            str(output),
            "--no-mirror",
        ],
    )
    assert result.exit_code == (130 if status == "cancelled" else 1), result.output
    assert len(list(output.glob("*.scan.json"))) == 4
    assert (output / "comparison.json").exists() == (status == "partial")


@pytest.mark.parametrize("cancelled", [False, True])
def test_full_variants_overlap_at_one_resolved_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    source = tmp_path / "source"
    source.mkdir()
    for args in (
        ["init", "-q"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(source), *args], check=True, capture_output=True)
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    barrier = Barrier(2)
    calls: dict[str, tuple[Any, Any]] = {}

    def collect(*args: Any, **kwargs: Any) -> Any:
        label = kwargs["cache_scope"]
        calls[label] = args, kwargs
        assert args[1] == revision
        assert isinstance(kwargs["cancel"], Event)
        barrier.wait(timeout=5)
        if cancelled:
            if label == "baseline":
                kwargs["cancel"].set()
            else:
                assert kwargs["cancel"].wait(timeout=5)
        pair = scan("dependabot", [None]), scan("renovate", [None])
        return tuple(
            item.model_copy(
                update={
                    "status": "cancelled" if cancelled else "complete",
                    "snapshot": item.snapshot.model_copy(update={"revision": revision}),
                }
            )
            for item in pair
        )

    monkeypatch.setattr("gappy.scan.session.scan", collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--config-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--no-mirror",
        ],
    )
    assert result.exit_code == (130 if cancelled else 0), (result.output, result.exception)
    assert set(calls) == {"baseline", "optimised"}
    assert calls["baseline"][1]["cancel"] is calls["optimised"][1]["cancel"]
    assert len(list((tmp_path / "out").glob("*.scan.json"))) == 4
    assert (tmp_path / "out/comparison.html").exists() is not cancelled


def test_bundled_actions_rule_uses_package_rule_filename_matchers() -> None:
    config = json.loads((Path(__file__).parents[1] / "optimised/renovate.json").read_text())
    rules = [
        rule
        for rule in config["packageRules"]
        if rule.get("matchManagers") == ["github-actions"] and "matchFileNames" in rule
    ]
    assert len(rules) == 1
    assert rules[0]["matchFileNames"] == [
        ".github/workflows/**/*.yml",
        ".github/workflows/**/*.yaml",
    ]
    assert all(
        "fileMatch" not in rule and "managerFilePatterns" not in rule
        for rule in config["packageRules"]
    )


def test_bundled_renovate_config_covers_observed_managers_without_remote_presets() -> None:
    config = json.loads((Path(__file__).parents[1] / "optimised/renovate.json").read_text())
    assert set(config["enabledManagers"]) == {
        "npm",
        "nvm",
        "github-actions",
        "dockerfile",
        "docker-compose",
        "helmv3",
        "helm-values",
    }
    assert all(not preset.startswith(("github>", "local>")) for preset in config["extends"])
    assert config["minimumReleaseAge"] == "7 days"
    assert config["automerge"] is False
    rules = config["packageRules"]
    assert not any(rule.get("automerge") for rule in rules)
    exclusions = [rule for rule in rules if rule.get("enabled") is False]
    assert {
        (tuple(rule["matchManagers"]), tuple(rule["matchPackageNames"])) for rule in exclusions
    } == {
        (("npm",), ("@types/node",)),
        (("github-actions",), ("ministryofjustice/hmpps-github-actions",)),
    }
    assert next(rule for rule in exclusions if rule["matchManagers"] == ["npm"])[
        "matchUpdateTypes"
    ] == ["major"]
    assert any(
        rule.get("matchPackageNames") == ["typescript", "govuk-frontend"]
        and rule.get("minimumReleaseAge") == "0 days"
        for rule in rules
    )


@pytest.mark.parametrize("verbose", [False, True])
def test_full_four_variants_and_offline_regeneration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verbose: bool
) -> None:
    config_pair(tmp_path)
    optimised = tmp_path / "custom"
    config_pair(optimised)
    ca_dir = tmp_path / ".gappy-mirror"
    ca_dir.mkdir()
    (ca_dir / "ca.crt").write_text("gateway test certificate")
    (ca_dir / "source-ca.crt").write_text("source test certificate")
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        optimised_scan = kwargs.get("config_dir") == optimised
        left, right = (
            scan("dependabot", [None]),
            scan("renovate", ["1.3.0"] if optimised_scan else [None]),
        )
        if optimised_scan:
            snapshot = left.snapshot.model_copy(update={"contentHash": "c" * 64})
            left = left.model_copy(update={"sessionId": "optimised", "snapshot": snapshot})
            right = right.model_copy(update={"sessionId": "optimised", "snapshot": snapshot})
        return left, right

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--config-dir",
            str(tmp_path),
            "--optimised-config-dir",
            str(optimised),
            "--output-dir",
            str(output),
            "--timeout-seconds",
            "900",
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(calls) == 2
    calls.sort(key=lambda c: c[1]["cache_scope"])
    assert calls[0][0][1] == "b" * 40
    assert calls[1][0][1] == "b" * 40
    assert all(c[0][3:5] == (900, ca_dir / "ca.crt") for c in calls)
    assert all(c[1]["source_ca"] == ca_dir / "source-ca.crt" for c in calls)
    assert calls[1][1]["replace_alternate_configs"]
    assert calls[0][1]["cache_scope"] == "baseline"
    assert calls[1][1]["cache_scope"] == "optimised"
    report = json.loads((output / "comparison.json").read_text())
    assert len(report["variants"]) == 4
    assert report["dependencies"][0]["variants"]["renovate.optimised"]["recommendations"] == [
        "1.3.0"
    ]
    assert report["dependencies"][0]["hasChanges"]
    assert len(list(output.glob("*.scan.json"))) == 4
    markup = FilterMarkup()
    markup.feed((output / "comparison.html").read_text())
    assert markup.checkbox is not None
    assert ("checked" in markup.checkbox) is not verbose
    assert "Dependabot / baseline" in (output / "comparison.html").read_text()
    assert "Renovate / optimised" in (output / "comparison.html").read_text()
    replay = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(output / "dependabot.scan.json"),
            "--renovate-json",
            str(output / "renovate.scan.json"),
            "--json",
            str(output / "replay.json"),
            "--html",
            str(output / "replay.html"),
            *(["--verbose"] if verbose else []),
        ],
    )
    assert replay.exit_code == 0, replay.output
    assert (output / "replay.json").read_text() == (output / "comparison.json").read_text()
    assert (output / "replay.html").read_text() == (output / "comparison.html").read_text()


@pytest.mark.parametrize("failure", ["missing", "malformed", "conflict"])
def test_full_rejects_bad_optimised_inputs_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    config_pair(tmp_path)
    if failure != "missing":
        config_pair(tmp_path / "optimised")
    if failure == "malformed":
        (tmp_path / "optimised" / "renovate.json").write_text("invalid")
    if failure == "conflict":
        (tmp_path / "dependabot.optimised.scan.json").write_text("keep")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("gappy.scan.session.scan", lambda *a, **k: pytest.fail("No scan allowed"))
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert not (tmp_path / "dependabot.scan.json").exists()
    if failure == "conflict":
        assert (tmp_path / "dependabot.optimised.scan.json").read_text() == "keep"


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled"])
@pytest.mark.parametrize("failed_pair", ["baseline", "optimised"])
def test_four_variant_failure_and_cancellation_remain_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, failed_pair: str
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.chdir(tmp_path)
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        label = "optimised" if kwargs.get("config_dir") == tmp_path / "optimised" else "baseline"
        calls.append(label)
        left, right = scan("dependabot", [None]), scan("renovate", [None])
        if label == failed_pair:
            left = left.model_copy(update={"status": status, "warnings": ["Synthetic failure"]})
        return left, right

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == (130 if status == "cancelled" else 1), result.output
    if status == "cancelled":
        assert not (tmp_path / "comparison.html").exists()
        assert failed_pair in calls
        assert len(calls) <= 2
    else:
        assert len(calls) == 2
        report = json.loads((tmp_path / "comparison.json").read_text())
        assert any(v["scan"]["status"] == status for v in report["variants"])
        assert "Synthetic failure" in (tmp_path / "comparison.html").read_text()


def test_four_variant_comparison_requires_same_revision_and_repository() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    for update in (
        {"repository": "other/repo"},
        {"snapshot": pair[0].snapshot.model_copy(update={"revision": "c" * 40})},
    ):
        other = tuple(s.model_copy(update=update) for s in pair)
        with pytest.raises(GappyError, match="same repository and revision"):
            compare_configurations(pair, other)


def test_four_variant_filter_hides_unchanged_rows_but_retains_evidence() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    report = compare_configurations(pair, pair)
    assert not report["dependencies"][0]["hasChanges"]
    parsed = FilterMarkup()
    parsed.feed(render(report))
    assert parsed.rows == ["false"]
    assert parsed.checkbox is not None and "checked" in parsed.checkbox
    assert len(report["dependencies"]) == 1
    assert dump(report) == dump(compare_configurations(pair, pair))


@pytest.mark.parametrize("status", ["partial", "failed", "cancelled", "unsupported"])
def test_missing_incomplete_scan_is_not_a_dependency_difference(status: str) -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    empty = scan("renovate", []).model_copy(update={"status": status})
    report = compare_configurations(pair, (pair[0], empty))
    assert not report["dependencies"][0]["hasChanges"]
    parsed = FilterMarkup()
    parsed.feed(render(report))
    assert parsed.rows == ["false"]
    assert len(report["variants"]) == 4


def test_filter_keeps_real_differences_despite_an_incomplete_scan() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    changed = scan("dependabot", [None])
    obs = changed.observations[0]
    changed.observations[0] = obs.model_copy(
        update={"dependency": obs.dependency.model_copy(update={"installedVersion": "2.0.0"})}
    )
    empty = scan("renovate", []).model_copy(update={"status": "failed"})
    assert compare_configurations(pair, (changed, empty))["dependencies"][0]["hasChanges"]


def test_unknown_registry_alone_is_not_a_filter_difference() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    changed = scan("dependabot", [None])
    obs = changed.observations[0]
    changed.observations[0] = obs.model_copy(
        update={"dependency": obs.dependency.model_copy(update={"registry": None})}
    )
    report = compare_configurations(pair, (changed, pair[1]))
    assert not report["dependencies"][0]["hasChanges"]


def test_complete_missing_discovery_is_a_filter_difference() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    report = compare_configurations(pair, (pair[0], scan("renovate", [])))
    assert report["dependencies"][0]["hasChanges"]


def test_full_custom_mirrors_do_not_require_bundled_gateway_ca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.chdir(tmp_path)
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return scan("dependabot", []), scan("renovate", [])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--npm-mirror",
            "https://mirror.test/npm/",
            "--pypi-mirror",
            "https://mirror.test/pypi/",
            "--docker-mirror",
            "https://mirror.test:8444",
            "--helm-mirror",
            "https://charts.test=https://mirror.test/helm/",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(calls) == 2
    assert all(call[0][4] is None for call in calls)
    for key in ("npm_mirror", "pypi_mirror", "docker_mirror", "helm_mirror"):
        assert calls[0][1][key] == calls[1][1][key]


def test_four_variant_html_escapes_native_names_and_warnings() -> None:
    pair = scan("dependabot", [None]), scan("renovate", [None])
    for model in pair:
        item = model.observations[0]
        model.observations[0] = item.model_copy(
            update={
                "dependency": item.dependency.model_copy(
                    update={"package": "<script>alert(1)</script>"}
                )
            }
        )
    report = compare_configurations(pair, pair)
    output = render(report)
    assert "<script>" not in output
    assert "&lt;script&gt;" in output


def test_compare_rejects_incomplete_optimised_exports(tmp_path: Path) -> None:
    for provider in ("dependabot", "renovate"):
        (tmp_path / f"{provider}.scan.json").write_text(dump(scan(provider, []).model_dump()))
    (tmp_path / "dependabot.optimised.scan.json").write_text(
        dump(scan("dependabot", []).model_dump())
    )
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(tmp_path / "dependabot.scan.json"),
            "--renovate-json",
            str(tmp_path / "renovate.scan.json"),
        ],
    )
    assert result.exit_code != 0
    assert not (tmp_path / "comparison.html").exists()
