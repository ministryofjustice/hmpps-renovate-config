import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from gappy.cli import main
from gappy.scan.native import capture_renovate_line
from gappy.scan.process import run as run_process
from gappy.scan.session import execute, toolchain
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES, fixture_repo
from tests.test_source_retry import failure


def set_directory(source: Path, directory: str) -> None:
    config_path = source / ".github/dependabot.yml"
    config = yaml.safe_load(config_path.read_text())
    config["updates"][0]["directory"] = directory
    config_path.write_text(yaml.safe_dump(config))


@pytest.mark.parametrize("kind", ["launcher", "source-mixed", "process"])
def test_dependabot_timeout_logs_affected_directory_without_raw_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    kind: str,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    set_directory(source, "/packages/client")
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        if kind == "process":
            return run_process([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
        discovery = (FIXTURES / "dependabot-no-update.jsonl").read_text()
        if kind == "launcher":
            return 1, (
                discovery
                + "\n    cli | 2026/11/12 17:50:16 update timed out after 3m0s\nsecret-canary\n"
            )
        return 1, discovery + failure() + failure("private_source_authentication_failure")

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    output = capsys.readouterr().err
    assert len(calls) == 1
    assert result.status == "partial"
    assert 'Dependabot npm job 1 (repository directory "/packages/client")' in output
    assert "timed out" in output.lower() or "timeout" in output.lower()
    assert "specific dependency or manifest" in output.lower()
    assert "secret-canary" not in output
    assert any('repository directory "/packages/client"' in warning for warning in result.warnings)


def test_source_retry_messages_identify_the_affected_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    set_directory(source, "/packages/backend")
    attempts = 0

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return 1, (FIXTURES / "dependabot-no-update.jsonl").read_text() + failure()
        return 0, (FIXTURES / "dependabot-patch.jsonl").read_text()

    monkeypatch.setattr("gappy.scan.session.run", run)
    monkeypatch.setattr("gappy.scan.session.wait_for_source_retry", lambda *_args: False)
    result = execute(
        "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    output = capsys.readouterr().err
    assert result.status == "complete", result.warnings
    assert 'repository directory "/packages/backend"' in output
    assert "Pausing 5s before attempt 2/3" in output
    assert "secret-canary" not in output


def test_timeout_directory_is_terminal_safe_and_redacts_selected_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    set_directory(source, "/packages/token-canary\n\x1b")
    output = "    cli | 2026/11/12 17:50:16 update timed out after 3m0s\n"
    monkeypatch.setattr("gappy.scan.session.run", lambda *_args, **_kwargs: (1, output))
    result = execute(
        "dependabot",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        github_token="token-canary",
    )
    console = capsys.readouterr().err
    assert result.status == "partial"
    assert 'repository directory "/packages/[REDACTED]\\n\\u001b"' in console
    assert "token-canary" not in console
    assert "\x1b" not in console
    assert "token-canary" not in result.model_dump_json()


def test_renovate_execution_timeout_logs_repository_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:2] == ["docker", "run"]:
            return run_process([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
        return 0, ""

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "renovate", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    output = capsys.readouterr().err
    assert result.status == "failed"
    assert 'Renovate (repository directory "/")' in output
    assert "execution timeout" in output
    assert "specific dependency or manifest" in output.lower()
    assert any("execution timeout" in warning for warning in result.warnings)


@pytest.mark.parametrize("level", [20, 40])
def test_renovate_native_source_timeout_is_logged_without_raw_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    level: int,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    event = json.dumps(
        {
            "level": level,
            "msg": "Error connecting to docker registry",
            "err": {"code": "ETIMEDOUT", "message": "secret-canary"},
        }
    )
    assert capture_renovate_line(event)
    output = (
        (FIXTURES / "renovate-patch.jsonl").read_text()
        + '\n{"msg":"branches","branches":[]}\n'
        + event
    )

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        return (0, output) if args[:2] == ["docker", "run"] else (0, "")

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "renovate", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    console = capsys.readouterr().err
    assert result.status == ("complete" if level == 20 else "partial")
    assert 'Renovate (repository directory "/")' in console
    assert "ETIMEDOUT" in console
    assert "specific dependency or manifest" in console.lower()
    assert "secret-canary" not in console
    assert "secret-canary" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("path", "label", "invalid"),
    [
        (
            "services/token-canary/package.json",
            'Renovate (repository manifest "services/[REDACTED]/package.json")',
            False,
        ),
        ("../../secret-canary", 'Renovate (repository directory "/")', True),
        (
            "https://user:token-canary@private.invalid/secret",
            'Renovate (repository directory "/")',
            True,
        ),
    ],
)
def test_renovate_timeout_uses_safe_reported_manifest_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: str,
    label: str,
    invalid: bool,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    event = json.dumps(
        {
            "level": 40,
            "packageFile": path,
            "err": {"code": "ETIMEDOUT", "message": "secret-canary"},
        }
    )
    output = (
        (FIXTURES / "renovate-patch.jsonl").read_text()
        + '\n{"msg":"branches","branches":[]}\n'
        + event
        + "\n"
        + event
    )

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        return (0, output) if args[:2] == ["docker", "run"] else (0, "")

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "renovate",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        github_token="token-canary",
    )
    console = capsys.readouterr().err
    assert result.status == "partial"
    assert console.count(label) == 1
    assert ("invalid" in console.lower()) == invalid
    assert ("specific dependency or manifest" in console.lower()) == invalid
    assert "secret-canary" not in console
    assert "token-canary" not in console
    assert "private.invalid" not in console
    assert "secret-canary" not in result.model_dump_json()
    assert "token-canary" not in result.model_dump_json()


def test_renovate_logs_native_timeout_before_an_execution_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    event = json.dumps(
        {
            "level": 20,
            "packageFile": "apps/web/package.json",
            "err": {"code": "ETIMEDOUT", "message": "secret-canary"},
        }
    )
    live_output = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:2] == ["docker", "run"]:

            def capture(line: str) -> bool:
                retained = bool(kwargs["capture_line"](line))
                live_output.append(capsys.readouterr().err)
                return retained

            return run_process(
                [sys.executable, "-c", f"import time; print({event!r}, flush=True); time.sleep(5)"],
                timeout=0.25,
                capture_line=capture,
            )
        return 0, ""

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        "renovate", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
    )
    console = capsys.readouterr().err
    assert result.status == "failed"
    assert 'repository manifest "apps/web/package.json"' in live_output[0]
    assert "ETIMEDOUT" in live_output[0]
    assert "secret-canary" not in live_output[0]
    assert 'repository directory "/"' in console
    assert "execution timeout" in console
    assert any("apps/web/package.json" in warning for warning in result.warnings)


def test_full_command_prints_timeout_and_saves_same_directory_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cli import mock_collection, scan_options

    scan_options(tmp_path, "full")
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    fixture_repo(source)
    set_directory(source, "/services/app")
    output = "    cli | 2026/11/12 17:50:16 update timed out after 3m0s\nsecret-canary\n"
    monkeypatch.setattr("gappy.scan.session.run", lambda *_args, **_kwargs: (1, output))

    def collect(*_args: Any, **_kwargs: Any) -> Any:
        left = execute(
            "dependabot", source, snapshot(), "session", "gappy/fixture", 10, toolchain(), None
        )
        right = left.model_copy(
            update={
                "provider": "renovate",
                "status": "complete",
                "warnings": [],
                "observations": [],
            }
        )
        return left, right

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main, ["full", "--repository", "gappy/fixture", "--source", str(source)]
    )
    assert result.exit_code == 1, (result.output, result.exception)
    assert 'Dependabot npm job 1 (repository directory "/services/app")' in result.stderr
    assert "native job timeout" in result.stderr
    assert "secret-canary" not in result.output
    paths = list((tmp_path / "gappy-results").rglob("dependabot.scan.json"))
    assert len(paths) == 1
    saved = json.loads(paths[0].read_text())
    assert any('repository directory "/services/app"' in warning for warning in saved["warnings"])
