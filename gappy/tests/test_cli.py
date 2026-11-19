"""Exercise command help and validation without scanner dependencies."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError, dump
from tests.test_compare import inventory as fixture_inventory
from tests.test_compare import scan as fixture_scan

LOG_DIRS: list[Path] = []


def mock_collection(monkeypatch: pytest.MonkeyPatch, collect: Any) -> None:
    LOG_DIRS.clear()

    @contextmanager
    def checkout(source: Path | None, repository: str) -> Any:
        yield source or Path.cwd()

    monkeypatch.setattr("gappy.full.checkout", checkout)
    monkeypatch.setattr("gappy.full.resolve_revision", lambda *args: "b" * 40)

    def run(*args: Any, **kwargs: Any) -> Any:
        kwargs.pop("cancel", None)
        LOG_DIRS.append(kwargs.pop("log_dir"))
        return collect(*args, **kwargs)

    monkeypatch.setattr("gappy.scan.session.scan", run)


def config_pair(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dependabot.yml").write_text(
        "version: 2\nupdates:\n  - package-ecosystem: npm\n"
        "    directory: /\n    schedule:\n      interval: weekly\n"
    )
    (directory / "renovate.json").write_text("{}")


def scan_options(directory: Path, command: str, **settings: Any) -> list[dict[str, Any]]:
    expected = [{"config_dir": directory, **settings}]
    if command == "full":
        expected[0]["cache_scope"] = "baseline"
        config_pair(directory)
        config_pair(directory / "optimised")
        expected.append(
            {
                **expected[0],
                "config_dir": directory / "optimised",
                "replace_alternate_configs": True,
                "cache_scope": "optimised",
            }
        )
    return expected


def test_help() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "compare" in result.output


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("mode", ["default", "directory", "repository"])
def test_scanner_config_source_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, mode: str
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    if command == "full":
        config_pair(tmp_path)
        config_pair(configs)
        config_pair((configs if mode == "directory" else tmp_path) / "optimised")
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    mock_collection(monkeypatch, collect)
    options = (
        ["--config-dir", str(configs)]
        if mode == "directory"
        else ["--repository-config"]
        if mode == "repository"
        else []
    )
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            *options,
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    expected = [
        {} if mode == "repository" else {"config_dir": configs if mode == "directory" else tmp_path}
    ]
    if command == "full":
        expected[0]["cache_scope"] = "baseline"
        expected.append(
            {
                "config_dir": (configs if mode == "directory" else tmp_path) / "optimised",
                "replace_alternate_configs": True,
                "cache_scope": "optimised",
            }
        )
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("command", ["scan", "full"])
def test_conflicting_config_sources_fail_before_scan(tmp_path: Path, command: str) -> None:
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--config-dir",
            str(tmp_path),
            "--repository-config",
        ],
    )
    assert result.exit_code == 2
    assert "cannot be combined" in result.output


@pytest.mark.parametrize("command", ["scan", "full"])
def test_extra_mirror_options_reach_scanners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    if command == "full":
        config_pair(tmp_path)
        config_pair(tmp_path / "optimised")
    source = tmp_path / "source"
    source.mkdir()
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--pypi-mirror",
            "https://mirror.test/simple/",
            "--docker-mirror",
            "https://mirror.test:8444",
            "--helm-mirror",
            "https://charts.test=https://mirror.test/helm/",
            "--helm-mirror",
            "https://other.test=https://mirror.test/other/",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    expected = [
        {
            "config_dir": tmp_path,
            "pypi_mirror": "https://mirror.test/simple/",
            "docker_mirror": "https://mirror.test:8444",
            "helm_mirror": (
                "https://charts.test=https://mirror.test/helm/",
                "https://other.test=https://mirror.test/other/",
            ),
        }
    ]
    if command == "full":
        expected[0]["cache_scope"] = "baseline"
        expected.append(
            {
                **expected[0],
                "config_dir": tmp_path / "optimised",
                "replace_alternate_configs": True,
                "cache_scope": "optimised",
            }
        )
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("command", ["scan", "compare"])
def test_missing_options_fail_explicitly(command: str) -> None:
    result = CliRunner().invoke(main, [command])
    assert result.exit_code == 2
    assert "Error:" in result.output


def test_invalid_command() -> None:
    result = CliRunner().invoke(main, ["unknown"])
    assert result.exit_code == 2


def test_repository_only_scan_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[Any, ...]] = []
    temporary_source = tmp_path / "temporary-checkout"

    @contextmanager
    def fake_checkout(source: Path | None, repository: str) -> Any:
        assert source is None
        assert repository == "ministryofjustice/hmpps-developer-portal"
        temporary_source.mkdir()
        try:
            yield temporary_source
        finally:
            temporary_source.rmdir()

    def collect(*args: Any, **kwargs: Any) -> Any:
        log_dir = kwargs.pop("log_dir")
        assert log_dir.name == "logs"
        assert log_dir.parent.parent == (
            tmp_path / "gappy-results/ministryofjustice/hmpps-developer-portal"
        )
        assert kwargs == {"config_dir": tmp_path}
        calls.append(args)
        assert args[0].is_dir()
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    monkeypatch.setattr("gappy.scan.snapshot.checkout", fake_checkout)
    monkeypatch.setattr("gappy.scan.session.scan", collect)
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(
            main,
            [
                "scan",
                "--repository",
                "ministryofjustice/hmpps-developer-portal",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Dependabot JSON:" in result.output
        assert "Renovate JSON:" in result.output
    assert calls[0][1:] == (
        "HEAD",
        "ministryofjustice/hmpps-developer-portal",
        180,
        None,
    )
    exports = list((tmp_path / "gappy-results").rglob("*.scan.json"))
    assert len(exports) == 4
    assert len({path.parent for path in exports}) == 2
    assert not temporary_source.exists()


def test_scan_explicit_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "checkout"
    source.mkdir()
    outputs = [tmp_path / "left.json", tmp_path / "right.json"]
    calls: list[tuple[Any, ...]] = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        assert kwargs == {"config_dir": tmp_path}
        calls.append(args)
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    monkeypatch.chdir(tmp_path)
    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            "scan",
            "--repository",
            "owner/repo",
            "--source",
            str(source),
            "--ref",
            "chosen-commit",
            "--dependabot-json",
            str(outputs[0]),
            "--renovate-json",
            str(outputs[1]),
            "--timeout-seconds",
            "42",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [(source, "chosen-commit", "owner/repo", 42, None)]
    assert all(path.is_file() for path in outputs)
    assert not (tmp_path / "gappy-results").exists()


def test_one_output_override_and_failed_scan_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "checkout"
    source.mkdir()
    failed = fixture_scan("dependabot", []).model_copy(
        update={"status": "unsupported", "warnings": ["unsupported policy"]}
    )
    monkeypatch.setattr(
        "gappy.scan.session.scan", lambda *args, **kwargs: (failed, fixture_scan("renovate", []))
    )
    explicit = tmp_path / "dependabot.json"
    result = CliRunner().invoke(
        main,
        [
            "scan",
            "--repository",
            "owner/repo",
            "--source",
            str(source),
            "--dependabot-json",
            str(explicit),
        ],
    )
    assert result.exit_code == 1
    assert "unsupported" in result.output
    assert explicit.is_file()
    assert len(list((tmp_path / "gappy-results").rglob("renovate.scan.json"))) == 1


def test_existing_scan_output_fails_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "existing.json"
    output.write_text("unchanged")

    def no_checkout(*args: Any) -> Any:
        pytest.fail("Must not acquire a repository when output preflight fails.")

    monkeypatch.setattr("gappy.scan.snapshot.checkout", no_checkout)
    result = CliRunner().invoke(
        main,
        [
            "scan",
            "--repository",
            "owner/repo",
            "--dependabot-json",
            str(output),
            "--renovate-json",
            str(tmp_path / "right.json"),
        ],
    )
    assert result.exit_code == 1
    assert output.read_text() == "unchanged"


@pytest.mark.parametrize("repository", ["../../bad", "../repo", "owner/..", "./repo"])
def test_repository_validation_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repository: str
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["scan", "--repository", repository])
    assert result.exit_code == 2
    assert not list(tmp_path.iterdir())


def test_clone_failure_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise GappyError("Cannot clone GitHub repository; supply --source.")

    monkeypatch.setattr("gappy.scan.snapshot.run", fail)
    result = CliRunner().invoke(main, ["scan", "--repository", "owner/repo"])
    assert result.exit_code == 1
    assert "Cannot clone" in result.output
    assert "--source" in result.output


def comparison_run(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / "gappy-results" / "gappy" / "fixture" / name
    directory.mkdir(parents=True)
    for provider in ("dependabot", "renovate"):
        (directory / f"{provider}.scan.json").write_text(
            dump(fixture_scan(provider, []).model_dump())
        )
    return directory


def test_compare_repository_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Create the older run last to ensure selection is based on run time, not mtime.
    latest = comparison_run(tmp_path, "20261111T171500Z-abcdef012345")
    older = comparison_run(tmp_path, "20261111T170000Z-abcdef012345")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(fixture_inventory(0).model_dump()))
    args = ["compare", "--repository", "gappy/fixture", "--inventory", str(inventory)]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert (latest / "comparison.json").is_file()
    assert (latest / "comparison.html").is_file()
    assert not (older / "comparison.json").exists()
    assert str(latest / "comparison.json") in result.output
    contents = (latest / "comparison.json").read_bytes()
    repeated = CliRunner().invoke(main, args)
    assert repeated.exit_code == 1
    assert (latest / "comparison.json").read_bytes() == contents


def test_compare_defaults_require_existing_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["compare", "--repository", "gappy/fixture"])
    assert result.exit_code == 2
    assert "No scan run found" in result.output
    assert not list(tmp_path.iterdir())


def test_compare_no_runs_is_actionable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["compare", "--repository", "gappy/fixture", "--inventory", "inventory.json"]
    )
    assert result.exit_code == 2
    assert "No scan run found" in result.output


def test_compare_does_not_skip_incomplete_latest_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    older = comparison_run(tmp_path, "20261111T170000Z-abcdef012345")
    latest = comparison_run(tmp_path, "20261111T171500Z-abcdef012345")
    (latest / "renovate.scan.json").unlink()
    result = CliRunner().invoke(
        main, ["compare", "--repository", "gappy/fixture", "--inventory", "inventory.json"]
    )
    assert result.exit_code == 2
    assert "latest scan run is missing" in result.output
    assert not (older / "comparison.json").exists()


def test_compare_explicit_inputs_default_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    selected = comparison_run(tmp_path, "20261111T171500Z-abcdef012345")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(fixture_inventory(0).model_dump()))
    explicit_html = tmp_path / "custom.html"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(selected / "dependabot.scan.json"),
            "--renovate-json",
            str(selected / "renovate.scan.json"),
            "--inventory",
            str(inventory),
            "--html",
            str(explicit_html),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (selected / "comparison.json").is_file()
    assert explicit_html.is_file()
    assert not (selected / "comparison.html").exists()


def test_compare_repository_identity_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    selected = comparison_run(tmp_path, "20261111T171500Z-abcdef012345")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(fixture_inventory(0).model_dump()))
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--repository",
            "wrong/repo",
            "--dependabot-json",
            str(selected / "dependabot.scan.json"),
            "--renovate-json",
            str(selected / "renovate.scan.json"),
            "--inventory",
            str(inventory),
        ],
    )
    assert result.exit_code == 2
    assert "do not match --repository" in result.output
    assert not (selected / "comparison.json").exists()


def test_compare_separate_input_directories_need_explicit_outputs(tmp_path: Path) -> None:
    left = comparison_run(tmp_path, "20261111T170000Z-abcdef012345")
    right = comparison_run(tmp_path, "20261111T171500Z-abcdef012345")
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(left / "dependabot.scan.json"),
            "--renovate-json",
            str(right / "renovate.scan.json"),
            "--inventory",
            "inventory.json",
        ],
    )
    assert result.exit_code == 2
    assert "different directories" in result.output


@pytest.mark.parametrize("status", ["complete", "partial", "unsupported", "cancelled"])
@pytest.mark.parametrize("verbose", [False, True])
def test_full_passes_exact_scan_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str, verbose: bool
) -> None:
    import json

    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(fixture_inventory(1).model_dump()))
    left = fixture_scan("dependabot", ["1.2.2"])
    if status != "complete":
        left = left.model_copy(update={"status": status, "warnings": ["fixture limitation"]})
    right = fixture_scan("renovate", ["1.2.1"])
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        assert kwargs.pop("log_dir").name == "logs"
        assert kwargs == {"config_dir": tmp_path}
        calls.append(args)
        return left, right

    def no_latest(*args: Any) -> Any:
        pytest.fail("Full must pass the exact exports, never discover a latest run.")

    monkeypatch.setattr("gappy.scan.session.scan", collect)
    monkeypatch.setattr("gappy.cli.latest_run", no_latest)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--inventory",
            str(inventory),
            "--source",
            str(source),
            "--ref",
            "chosen",
            "--timeout-seconds",
            "42",
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == (
        0 if status == "complete" else 130 if status == "cancelled" else 1
    ), result.output
    assert calls == [(source, "chosen", "gappy/fixture", 42, None)]
    exports = list((tmp_path / "gappy-results").rglob("dependabot.scan.json"))
    assert len(exports) == 1
    directory = exports[0].parent
    if status == "cancelled":
        assert not (directory / "comparison.html").exists()
    else:
        report = json.loads((directory / "comparison.json").read_text())
        assert report["sessionId"] == left.sessionId
        assert (directory / "comparison.html").is_file()
        from tests.test_report_filter import FilterMarkup

        parsed = FilterMarkup()
        parsed.feed((directory / "comparison.html").read_text())
        assert parsed.checkbox is not None
        assert ("checked" in parsed.checkbox) is not verbose
        assert "Comparison HTML:" in result.output
        if status != "complete":
            assert "diagnostic comparison" in result.output


def test_full_invalid_inventory_fails_before_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    inventory = tmp_path / "bad.json"
    inventory.write_text("invalid")
    result = CliRunner().invoke(
        main, ["full", "--repository", "gappy/fixture", "--inventory", str(inventory)]
    )
    assert result.exit_code == 2
    assert not (tmp_path / "gappy-results").exists()


def test_full_preflights_reports_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(fixture_inventory(0).model_dump()))
    output = tmp_path / "comparison.html"
    output.write_text("keep")

    def no_collect(*args: Any) -> Any:
        pytest.fail("Existing report must be rejected before scanning.")

    monkeypatch.setattr("gappy.scan.session.scan", no_collect)
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--inventory",
            str(inventory),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert output.read_text() == "keep"
    assert not (tmp_path / "dependabot.scan.json").exists()


def test_full_snapshot_mismatch_preserves_exports_not_false_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    baseline = fixture_inventory(0).model_copy(update={"snapshotHash": "b" * 64})
    inventory = tmp_path / "inventory.json"
    inventory.write_text(dump(baseline.model_dump()))
    monkeypatch.setattr(
        "gappy.scan.session.scan",
        lambda *args, **kwargs: (fixture_scan("dependabot", []), fixture_scan("renovate", [])),
    )
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--inventory",
            str(inventory),
            "--source",
            str(source),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    assert (tmp_path / "dependabot.scan.json").is_file()
    assert not (tmp_path / "comparison.html").exists()


def test_full_uses_only_native_discovery_without_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.chdir(tmp_path)
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    events = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        assert "before_scan" not in kwargs
        events.append("scans")
        return fixture_scan("dependabot", ["1.2.2"]), fixture_scan("renovate", ["2.0.0"])

    monkeypatch.setattr(
        "gappy.inventory.generate", lambda *args: pytest.fail("No Gappy inventory allowed.")
    )
    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main, ["full", "--repository", "gappy/fixture", "--source", str(source)]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert events == ["scans", "scans"]
    directory = next((tmp_path / "gappy-results").rglob("comparison.json")).parent
    report = json.loads((directory / "comparison.json").read_text())
    assert report["mode"] == "configuration-comparison"
    assert report["dependencies"][0]["outcomes"] == {
        "baseline": "different-recommendations",
        "optimised": "different-recommendations",
    }
    assert "inventory" not in report
    assert not (directory / "inventory.json").exists()
    assert len(list(directory.iterdir())) == 6


def test_full_scan_failure_stops_before_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_scan import fixture_repo

    source = tmp_path / "source"
    fixture_repo(source)
    events = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        events.append("scanners-started")
        raise GappyError("Native scan preparation failed.")

    monkeypatch.setattr("gappy.scan.session.scan", collect)
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
    assert result.exit_code == 1
    assert "scan preparation failed" in result.output
    assert events == ["scanners-started"] * 2
    assert not (tmp_path / "inventory.json").exists()
    assert not (tmp_path / "comparison.html").exists()


def test_full_supplied_inventory_does_not_query_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    inventory = tmp_path / "baseline.json"
    inventory.write_text(dump(fixture_inventory(0).model_dump()))
    monkeypatch.setattr(
        "gappy.inventory.generate",
        lambda *args: pytest.fail("Supplied inventory must bypass collection."),
    )
    monkeypatch.setattr(
        "gappy.scan.session.scan",
        lambda *args, **kwargs: (fixture_scan("dependabot", []), fixture_scan("renovate", [])),
    )
    result = CliRunner().invoke(
        main,
        [
            "full",
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--inventory",
            str(inventory),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "inventory.json").exists()
