import json
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from gappy.io import GappyError
from gappy.scan.native import source_timed_out
from gappy.scan.session import execute, toolchain
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES, fixture_repo


def failure(error: str = "private_source_timed_out") -> str:
    return (
        json.dumps(
            {
                "type": "record_update_job_error",
                "data": {"error-type": error, "error-details": {"source": "secret-canary"}},
            }
        )
        + "\n"
    )


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("failures", [1, 2, 3])
def test_source_timeout_waits_retries_and_logs_without_leaking_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cached: bool,
    failures: int,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    original = (source / "package.json").read_text()
    calls = []
    waits = []
    stages = []

    def wait(self: Event, delay: float) -> bool:
        waits.append(delay)
        return False

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        local = Path(args[args.index("--local") + 1])
        assert (local / "package.json").read_text() == original
        (local / "package.json").write_text("changed by attempt")
        calls.append(local)
        if len(calls) <= failures:
            return 1, (FIXTURES / "dependabot-no-update.jsonl").read_text() + failure()
        return 0, (FIXTURES / "dependabot-patch.jsonl").read_text()

    @contextmanager
    def staged(*args: Any, **kwargs: Any) -> Any:
        stages.append("enter")
        try:
            yield tmp_path / "cache"
        finally:
            stages.append("exit")

    monkeypatch.setattr("gappy.scan.session.run", run)
    monkeypatch.setattr("gappy.scan.session.prepare", lambda *args: "test-volume")
    monkeypatch.setattr("gappy.scan.session.staged", staged)
    monkeypatch.setattr("gappy.scan.session.wait_for_source_retry", wait)
    result = execute(
        "dependabot",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        cache=cached,
    )
    assert len(calls) == min(failures + 1, 3)
    assert waits == [5, 10][: min(failures, 2)]
    assert len(set(calls)) == len(calls)
    assert all(not path.exists() for path in calls)
    assert (source / "package.json").read_text() == original
    assert stages == (["enter", "enter", "exit", "exit"] if cached else [])
    output = capsys.readouterr().err
    assert "Pausing 5s before attempt 2/3" in output
    assert "retrying dependency source now" in output
    assert "secret-canary" not in output
    assert "secret-canary" not in " ".join(result.warnings)
    if failures < 3:
        assert result.status == "complete", result.warnings
        assert result.observations[0].lookupComplete
        assert result.observations[0].candidates
        assert "source timeout cleared" in output
        assert not any("has incomplete evidence" in warning for warning in result.warnings)
    else:
        assert result.status == "partial"
        assert not result.observations[0].lookupComplete
        assert "retry limit reached" in output


def test_retry_wait_is_cancellable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        return 1, failure()

    def wait(self: Event, delay: float) -> bool:
        self.set()
        return True

    monkeypatch.setattr("gappy.scan.session.run", run)
    monkeypatch.setattr("gappy.scan.session.wait_for_source_retry", wait)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    assert len(calls) == 1
    assert result.status == "cancelled"
    assert "retry cancelled" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [
        "private_source_authentication_failure",
        "unexpected_external_code",
        "dependency_file_not_found",
    ],
)
def test_non_timeout_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: str,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        return 1, failure(error)

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    assert len(calls) == 1
    assert result.status == "partial"
    assert "Pausing" not in capsys.readouterr().err


def test_mixed_errors_do_not_retry_authentication_failure() -> None:
    assert source_timed_out(failure())
    assert not source_timed_out(failure() + failure("private_source_authentication_failure"))
    assert not source_timed_out('{"type":"mark_as_processed","data":{}}')


def test_execution_timeout_is_not_retried_as_source_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        raise GappyError("Scanner exceeded its execution timeout.")

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    assert len(calls) == 1
    assert result.status == "partial"
    assert any("execution timeout" in warning for warning in result.warnings)


@pytest.mark.parametrize("with_discovery", [False, True])
def test_launcher_timeout_is_reported_and_preserves_partial_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_discovery: bool
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []
    output = "    cli | 2026/11/12 17:50:16 update timed out after 3m0s\nsecret-canary\n"
    if with_discovery:
        output = (
            "\n".join(
                line
                for line in (FIXTURES / "dependabot-no-update.jsonl").read_text().splitlines()
                if json.loads(line).get("type") != "mark_as_processed"
            )
            + "\n"
            + output
        )

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        return 1, output

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 180, toolchain(), None
    )
    assert len(calls) == 1
    assert result.status == "partial"
    assert bool(result.observations) == with_discovery
    assert all(not item.lookupComplete for item in result.observations)
    assert any("native job timeout" in warning for warning in result.warnings)
    assert any("--timeout-seconds" in warning for warning in result.warnings)
    assert "secret-canary" not in result.model_dump_json()
    if with_discovery:
        from gappy.compare.discovered import compare
        from gappy.report.html import render

        other = result.model_copy(
            update={
                "provider": "renovate",
                "status": "complete",
                "observations": [],
                "warnings": [],
            }
        )
        report = compare(result, other)
        rows = report["dependencies"]
        assert isinstance(rows, list) and rows
        assert not rows[0]["dependabot"]["lookupComplete"]
        html = render(report)
        assert "native job timeout" in html
        assert "--timeout-seconds" in html
        assert "secret-canary" not in html
        assert '<span class="badge text-bg-success">No newer update recommended</span>' not in html


def test_nonzero_launcher_exit_does_not_claim_completed_lookups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    output = (FIXTURES / "dependabot-no-update.jsonl").read_text()
    monkeypatch.setattr("gappy.scan.session.run", lambda *args, **kwargs: (1, output))
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "partial"
    assert result.observations
    assert all(not item.lookupComplete for item in result.observations)
