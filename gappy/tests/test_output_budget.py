import json
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.native import capture_renovate_line, renovate
from gappy.scan.process import run
from gappy.scan.session import execute, run_dependabot_job, scan, toolchain
from tests.test_compare import scan as fixture_scan
from tests.test_dependabot import snapshot
from tests.test_scan import fixture_repo

MIB = 1024 * 1024


@pytest.mark.parametrize("filtered", [False, True])
def test_unbounded_scanner_output_accepts_more_than_old_default(filtered: bool) -> None:
    code, output = run(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * (17 * 1024 * 1024))"],
        capture_line=(lambda line: True) if filtered else None,
        output_limit=None,
    )
    assert code == 0
    assert len(output) == 17 * MIB


def test_memory_exhaustion_is_an_explicit_scanner_error() -> None:
    def out_of_memory(line: str) -> bool:
        raise MemoryError

    with pytest.raises(GappyError, match="memory"):
        run(
            [sys.executable, "-c", "print('x')"],
            capture_line=out_of_memory,
            output_limit=None,
        )


@pytest.mark.parametrize("terminated", [False, True])
def test_raised_budget_accepts_real_output_above_old_ceiling(terminated: bool) -> None:
    code, output = run(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write("
            '\'{"level":30,"msg":"Repository finished","detail":"\' + '
            "'x' * (17 * 1024 * 1024) + '\"}'" + (" + '\\n'" if terminated else "") + ")",
        ],
        capture_line=capture_renovate_line,
        output_limit=18 * MIB,
    )
    assert code == 0
    assert len(output.encode()) > 16 * MIB
    assert json.loads(output)["msg"] == "Repository finished"


@pytest.mark.parametrize("filtered", [False, True])
def test_custom_budget_is_enforced_without_truncation(filtered: bool) -> None:
    with pytest.raises(GappyError, match="1 MiB"):
        run(
            [sys.executable, "-c", "print('x' * (1024 * 1024))"],
            capture_line=(lambda line: True) if filtered else None,
            output_limit=MIB,
        )


def test_custom_budget_limits_unterminated_discarded_line() -> None:
    with pytest.raises(GappyError, match="1 MiB"):
        run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * (1024 * 1024 + 1))"],
            capture_line=lambda line: False,
            output_limit=MIB,
        )


@pytest.mark.parametrize("budget", [0, -1, 256 * MIB + 1])
def test_invalid_process_budget_fails_before_launch(budget: int) -> None:
    with pytest.raises(GappyError, match="output budget"):
        run(["/nonexistent-gappy-executable"], output_limit=budget)


@pytest.mark.parametrize("command", ["scan", "full"])
def test_cli_has_no_default_scanner_evidence_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(tmp_path, command)
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return fixture_scan("dependabot", []), fixture_scan("renovate", [])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main, [command, "--repository", "gappy/fixture", "--source", str(source)]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("command", ["scan", "full"])
def test_cli_budget_reaches_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(tmp_path, command, renovate_output_limit_mib=64)
    monkeypatch.chdir(tmp_path)
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
            "--renovate-output-limit-mib",
            "64",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("budget", ["0", "-1", "257", "unlimited"])
def test_cli_rejects_unbounded_or_invalid_budget(command: str, budget: str) -> None:
    result = CliRunner().invoke(
        main,
        [command, "--repository", "gappy/fixture", "--renovate-output-limit-mib", budget],
    )
    assert result.exit_code == 2
    assert "--renovate-output-limit-mib" in result.output


def test_collection_budget_reaches_only_renovate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    seen = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        return (0, "dependabot version 1.94.0") if args[0] == "dependabot" else (0, "linux")

    def execute_scan(provider: str, root: Path, frozen: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append((provider, kwargs.get("renovate_output_limit_mib")))
        return fixture_scan(provider, []).model_copy(update={"snapshot": frozen})

    monkeypatch.setattr("gappy.scan.session.shutil.which", lambda name: "/fixture/" + name)
    monkeypatch.setattr("gappy.scan.session.run", native_run)
    monkeypatch.setattr("gappy.scan.session.execute", execute_scan)
    scan(source, "HEAD", "gappy/fixture", 10, renovate_output_limit_mib=64, mirror=False)
    assert seen == [("dependabot", None), ("renovate", 64)]


def test_native_launch_uses_budget_and_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    seen = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if "run" in args:
            seen.append(kwargs["output_limit"])
            raise GappyError("Scanner output exceeded the 64 MiB safety limit.")
        return 0, ""

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    result = execute(
        "renovate",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        renovate_output_limit_mib=64,
    )
    assert seen == [64 * MIB]
    assert result.status == "failed"
    assert not result.observations
    assert any("64 MiB" in message for message in result.warnings)
    assert any("--renovate-output-limit-mib" in message for message in result.warnings)


def test_scanner_launches_are_unbounded_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    seen = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[0] == "dependabot":
            seen.append(("dependabot", kwargs.get("output_limit", "missing")))
            return 0, '{"type":"mark_as_processed","data":{}}\n'
        if "run" in args:
            seen.append(("renovate", kwargs.get("output_limit", "missing")))
            return (
                0,
                (FIXTURES / "renovate-patch.jsonl").read_text()
                + '\n{"msg":"branches","branches":[]}\n',
            )
        return 0, ""

    from tests.test_scan import FIXTURES

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    run_dependabot_job(["dependabot", "update"], source, 10, {}, Event(), None, "job", [])
    result = execute(
        "renovate", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    assert seen == [("dependabot", None), ("renovate", None)]
    assert result.status == "complete"
    assert any("no fixed" in warning.lower() for warning in result.warnings)


def test_renovate_does_not_cap_dependency_count_at_100() -> None:
    text = "\n".join(
        json.dumps(event)
        for event in [
            {
                "msg": "packageFiles with updates",
                "config": {
                    "npm": [
                        {
                            "packageFile": "package.json",
                            "deps": [
                                {
                                    "depName": f"package-{index}",
                                    "currentVersion": "1.0.0",
                                    "updates": [{"newVersion": "1.0.1"}],
                                }
                                for index in range(150)
                            ],
                        }
                    ],
                },
            },
            {"msg": "Repository finished"},
        ]
    )
    observations, complete = renovate(text)
    assert complete
    assert len(observations) == 150
    assert all(item.lookupComplete and len(item.candidates) == 1 for item in observations)
