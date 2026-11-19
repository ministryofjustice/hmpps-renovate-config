import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from gappy.cli import main, open_in_chrome
from tests.test_cli import comparison_run, mock_collection, scan_options
from tests.test_compare import scan


@pytest.mark.parametrize(
    "platform,executable",
    [
        ("darwin", None),
        ("linux", "/usr/bin/google-chrome"),
        ("win32", "C:/chrome.exe"),
    ],
)
def test_chrome_launcher_uses_file_uri_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, executable: str | None
) -> None:
    report = tmp_path / "report with spaces.html"
    report.write_text("<html></html>")
    launch = Mock()
    monkeypatch.setattr("gappy.cli.sys.platform", platform)
    monkeypatch.setattr("gappy.cli.shutil.which", lambda name: executable)
    monkeypatch.setattr("gappy.cli.subprocess.run", launch)
    browser = Mock()
    monkeypatch.setattr("gappy.cli.webbrowser.BackgroundBrowser", browser)
    open_in_chrome(report)
    if platform == "darwin":
        launch.assert_called_once_with(
            ["open", "-a", "Google Chrome", report.as_uri()],
            check=True,
            capture_output=True,
            timeout=15,
        )
        browser.assert_not_called()
    else:
        browser.assert_called_once_with(executable)
        browser.return_value.open.assert_called_once_with(report.as_uri())
        launch.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError(),
        subprocess.CalledProcessError(1, ["open"]),
        subprocess.TimeoutExpired("open", 15),
    ],
)
def test_chrome_errors_explicitly_preserve_saved_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    report = tmp_path / "report.html"
    report.write_text("saved")
    monkeypatch.setattr("gappy.cli.sys.platform", "darwin")
    monkeypatch.setattr("gappy.cli.subprocess.run", Mock(side_effect=error))
    with pytest.raises(click.ClickException, match="Report saved.*Chrome could not be opened"):
        open_in_chrome(report)
    assert report.read_text() == "saved"


def test_chrome_not_installed_is_an_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gappy.cli.sys.platform", "linux")
    monkeypatch.setattr("gappy.cli.shutil.which", lambda name: None)
    with pytest.raises(click.ClickException, match="Chrome was not found"):
        open_in_chrome(tmp_path / "report.html")


def test_background_chrome_launch_failure_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gappy.cli.sys.platform", "linux")
    monkeypatch.setattr("gappy.cli.shutil.which", lambda name: "/usr/bin/google-chrome")
    browser = Mock()
    browser.return_value.open.return_value = False
    monkeypatch.setattr("gappy.cli.webbrowser.BackgroundBrowser", browser)
    with pytest.raises(click.ClickException, match="Chrome could not be opened"):
        open_in_chrome(tmp_path / "report.html")


@pytest.mark.parametrize("command", ["compare", "full"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("status", ["complete", "partial"])
def test_report_opens_only_when_requested_after_being_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, enabled: bool, status: str
) -> None:
    scan_options(tmp_path, command)
    monkeypatch.chdir(tmp_path)
    opened = []

    def launch(path: Path) -> None:
        assert path.is_file()
        assert (
            "Baseline and optimised dependency comparison"
            if command == "full"
            else "Direct dependency discovery"
        ) in path.read_text()
        opened.append(path)

    def collect(*args: Any, **kwargs: Any) -> Any:
        left = scan("dependabot", [])
        if status != "complete":
            left = left.model_copy(update={"status": status, "warnings": ["Synthetic failure."]})
        return left, scan("renovate", [])

    monkeypatch.setattr("gappy.cli.open_in_chrome", launch)
    mock_collection(monkeypatch, collect)
    if command == "compare":
        comparison_run(tmp_path, "20261112T100000Z-abcdef012345")
    source = tmp_path / "source"
    source.mkdir()
    args = [command, "--repository", "gappy/fixture"]
    if command == "full":
        args += ["--source", str(source)]
    if enabled:
        args += ["--open-chrome"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == (1 if command == "full" and status == "partial" else 0), (
        result.output,
        result.exception,
    )
    assert len(opened) == int(enabled)
