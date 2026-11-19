"""Redacted raw scanner logs are persisted before any processing or filtering."""

import subprocess
import sys
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.auth import GhcrCredentials
from gappy.scan.dependabot import Job
from gappy.scan.logs import raw_log
from gappy.scan.process import ProcessTimeout, run
from gappy.scan.session import dependabot_batch, execute, toolchain
from tests.test_auth import TOKEN
from tests.test_cli import LOG_DIRS, mock_collection
from tests.test_compare import scan
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES, fixture_repo

COOLDOWN = "updater | 2026/11/16 21:14:11 INFO Filtered out 2 versions due to cooldown\n"


def test_process_taps_every_line_before_capture_filtering() -> None:
    lines: list[str] = []
    code, output = run(
        [sys.executable, "-c", "print('keep'); print('drop'); print('tail', end='')"],
        capture_line=lambda line: line.startswith("keep"),
        raw_line=lines.append,
    )
    assert code == 0
    assert output == "keep\n"
    assert lines == ["keep\n", "drop\n", "tail"]


def test_process_tap_keeps_output_written_before_timeout() -> None:
    lines: list[str] = []
    with pytest.raises(ProcessTimeout):
        run(
            [sys.executable, "-uc", "import time; print('started'); time.sleep(30)"],
            timeout=1,
            raw_line=lines.append,
        )
    assert lines == ["started\n"]


def test_process_tap_keeps_output_before_evidence_limit_failure() -> None:
    lines: list[str] = []
    with pytest.raises(GappyError, match="safety limit"):
        run(
            [sys.executable, "-c", "print('output before failure')"],
            output_limit=1,
            raw_line=lines.append,
        )
    assert "".join(lines) == "output before failure\n"


def test_process_saves_stdout_and_stderr_on_nonzero_exit(tmp_path: Path) -> None:
    with raw_log(tmp_path, "failed") as log:
        assert log is not None
        code, _ = run(
            [
                sys.executable,
                "-uc",
                "import sys; print('started'); print('failed', file=sys.stderr); sys.exit(2)",
            ],
            raw_line=log.line,
        )
    assert code == 2
    assert (tmp_path / "failed.log").read_text() == "started\nfailed\n"


def test_log_write_failure_still_cleans_up_process(monkeypatch: pytest.MonkeyPatch) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    start = subprocess.Popen

    def launch(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = start(*args, **kwargs)
        processes.append(process)
        return process

    def fail_write(text: str) -> None:
        raise OSError("log disk full")

    monkeypatch.setattr("gappy.scan.process.subprocess.Popen", launch)
    with pytest.raises(OSError, match="log disk full"):
        run(
            [sys.executable, "-uc", "import time; print('started', end=''); time.sleep(30)"],
            timeout=1,
            raw_line=fail_write,
        )
    [process] = processes
    assert process.poll() is not None
    assert process.stdout is not None and process.stdout.closed


def test_raw_log_redacts_credentials_and_is_private(tmp_path: Path) -> None:
    ghcr = GhcrCredentials("fixture-user", "ghcr-canary-secret")
    with raw_log(tmp_path / "logs", "Dependabot npm/job 1", TOKEN, ghcr) as log:
        assert log is not None
        log.note("header")
        log.line(f"token {TOKEN} ghcr ghcr-canary-secret\n")
    path = tmp_path / "logs" / "Dependabot-npm-job-1.log"
    text = path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert TOKEN not in text and "ghcr-canary-secret" not in text
    assert text == "# gappy: header\ntoken [REDACTED] ghcr [REDACTED]\n"
    with raw_log(None, "unused") as disabled:
        assert disabled is None


def test_dependabot_jobs_and_cooldown_comparison_save_raw_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls: list[str] = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args[args.index("--file") + 1])
        if len(calls) == 1:
            output = COOLDOWN + (FIXTURES / "dependabot-no-update.jsonl").read_text()
        else:
            output = (FIXTURES / "dependabot-patch.jsonl").read_text()
        output = f"updater | secret {TOKEN}\n" + output
        for line in output.splitlines(keepends=True):
            kwargs["raw_line"](line)
        return 0, output

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    definition = {"package-manager": "npm_and_yarn", "cooldown": {"default-days": 7}}
    logs = tmp_path / "logs"
    result = dependabot_batch(
        [(0, Job("/", "npm", definition, [], True, policy_keys=frozenset({"cooldown"})))],
        source,
        "gappy/fixture",
        10,
        {"updaterImage:npm": "fixture", "proxyImage": "proxy"},
        None,
        False,
        None,
        None,
        Event(),
        github_token=TOKEN,
        log_dir=logs,
    )
    assert result.complete, result.warnings
    main_log = (logs / "dependabot-01-npm.log").read_text()
    probe_log = (logs / "dependabot-01-npm-without-cooldown.log").read_text()
    assert "Filtered out 2 versions due to cooldown" in main_log
    assert "attempt 1 exited with code 0" in main_log
    assert "create_pull_request" in probe_log
    assert TOKEN not in main_log + probe_log
    assert "[REDACTED]" in main_log


def test_renovate_raw_log_keeps_lines_dropped_by_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:3] == ["docker", "container", "ls"]:
            return 0, ""
        kwargs["raw_line"]('{"level":20,"msg":"debug detail dropped by capture"}\n')
        output = (FIXTURES / "renovate-no-update.jsonl").read_text()
        output += '\n{"msg":"branches","branches":[]}\n'
        return 0, output

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    logs = tmp_path / "logs"
    result = execute(
        "renovate",
        source,
        snapshot(),
        "fixture",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        log_dir=logs,
    )
    assert result.status == "complete", result.warnings
    text = (logs / "renovate.log").read_text()
    assert "debug detail dropped by capture" in text
    assert "Renovate exited with code 0" in text


def test_full_saves_logs_per_variant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_cli import config_pair

    monkeypatch.chdir(tmp_path)
    config_pair(tmp_path)
    config_pair(tmp_path / "optimised")
    mock_collection(
        monkeypatch, lambda *args, **kwargs: (scan("dependabot", []), scan("renovate", []))
    )
    source = tmp_path / "source"
    source.mkdir()
    result = CliRunner().invoke(
        main, ["full", "--repository", "gappy/fixture", "--source", str(source)]
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(path.name for path in LOG_DIRS) == ["baseline", "optimised"]
    assert {path.parent.name for path in LOG_DIRS} == {"logs"}
    assert len({path.parent for path in LOG_DIRS}) == 1
