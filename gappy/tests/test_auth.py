import base64
import json
from pathlib import Path
from threading import Event
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError, dump
from gappy.scan.auth import GhcrCredentials, ghcr_credentials, github_token, redact
from gappy.scan.dependabot import Job
from gappy.scan.session import dependabot_batch, environment, execute, toolchain
from tests.test_cli import mock_collection, scan_options
from tests.test_compare import scan
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES, fixture_repo

TOKEN = "synthetic-token-canary-not-real"


def test_ghcr_credentials_are_separate_and_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    assert ghcr_credentials(None, None) is None
    with pytest.raises(GappyError, match="both"):
        ghcr_credentials("fixture-user", None)
    with pytest.raises(GappyError, match="both"):
        ghcr_credentials(None, "GITHUB_TOKEN")
    creds = ghcr_credentials("fixture-user", "GITHUB_TOKEN")
    assert creds is not None
    assert TOKEN not in repr(creds)
    assert json.loads(creds.host_rules()) == [
        {
            "hostType": "docker",
            "matchHost": "https://ghcr.io",
            "username": "fixture-user",
            "password": TOKEN,
        }
    ]
    basic = base64.b64encode(f"fixture-user:{TOKEN}".encode()).decode()
    assert TOKEN not in creds.redact(TOKEN + " " + basic)
    assert basic not in creds.redact(basic)


@pytest.mark.parametrize("command", ["scan", "full"])
def test_ghcr_cli_forwards_separate_credentials_without_exporting_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHCR_TEST_TOKEN", TOKEN)
    expected = scan_options(tmp_path, command, ghcr=GhcrCredentials("fixture-user", TOKEN))
    calls = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return scan("dependabot", []), scan("renovate", [])

    mock_collection(monkeypatch, collect)
    source = tmp_path / "source"
    source.mkdir()
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--ghcr-username",
            "fixture-user",
            "--ghcr-token-env",
            "GHCR_TEST_TOKEN",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(calls, key=lambda x: x.get("cache_scope", "")) == expected
    assert TOKEN not in result.output


def test_renovate_ghcr_credentials_are_runtime_only_and_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []
    creds = GhcrCredentials("fixture-user", TOKEN)

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:3] == ["docker", "container", "ls"]:
            return 0, ""
        calls.append(args)
        assert TOKEN not in " ".join(args)
        assert "RENOVATE_HOST_RULES" in args
        assert kwargs["env"]["RENOVATE_HOST_RULES"] == creds.host_rules()
        assert "RENOVATE_GITHUB_COM_TOKEN" not in kwargs["env"]
        assert all(TOKEN.encode() not in p.read_bytes() for p in source.rglob("*") if p.is_file())
        output = (FIXTURES / "renovate-no-update.jsonl").read_text()
        output += '\n{"msg":"branches","branches":[]}\n'
        return 0, output.replace("gappy-fixture", TOKEN)

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    original = snapshot()
    result = execute(
        "renovate", source, original, "fixture", "gappy/fixture", 10, toolchain(), None, ghcr=creds
    )
    assert calls
    assert result.status == "complete", result.warnings
    assert TOKEN not in dump(result.model_dump())
    assert result.snapshot == original
    assert any("private GHCR" in w for w in result.warnings)


def test_dependabot_ghcr_credentials_are_proxy_only_temporary_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    paths = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        assert TOKEN not in " ".join(args)
        path = Path(args[args.index("--file") + 1])
        paths.append(path)
        assert path.stat().st_mode & 0o777 == 0o600
        job = yaml.safe_load(path.read_text())
        assert job["credentials"] == [
            {
                "type": "docker_registry",
                "registry": "ghcr.io",
                "username": "fixture-user",
                "password": TOKEN,
            }
        ]
        assert "credentials" not in job["job"]
        local = Path(args[args.index("--local") + 1])
        assert all(TOKEN.encode() not in p.read_bytes() for p in local.rglob("*") if p.is_file())
        return 0, (FIXTURES / "dependabot-no-update.jsonl").read_text().replace(
            "gappy-fixture", TOKEN
        )

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    result = dependabot_batch(
        [(0, Job("/", "docker", {}, [], False))],
        source,
        "gappy/fixture",
        10,
        {"updaterImage:docker": "fixture", "proxyImage": "proxy"},
        None,
        False,
        None,
        None,
        Event(),
        ghcr=GhcrCredentials("fixture-user", TOKEN),
    )
    assert result.complete, result.warnings
    assert paths and all(not p.exists() for p in paths)
    assert TOKEN not in dump([o.model_dump() for o in result.observations])
    assert TOKEN not in " ".join(result.warnings)


def test_dependabot_cooldown_filtering_reruns_without_cooldown_for_deferred_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    paths: list[Path] = []
    proposals: list[str] = []
    log = "updater | 2026/11/16 21:14:11 INFO Filtered out 2 versions due to cooldown\n"

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        path = Path(args[args.index("--file") + 1])
        paths.append(path)
        assert path.stat().st_mode & 0o777 == 0o600
        job = yaml.safe_load(path.read_text())["job"]
        if len(paths) == 1:
            assert job["cooldown"] == {"default-days": 7}
            withheld = (FIXTURES / "dependabot-patch.jsonl").read_text().splitlines()[0]
            return 0, log + withheld + '\n{"type": "mark_as_processed", "data": {}}'
        assert "cooldown" not in job
        return 0, (FIXTURES / "dependabot-patch.jsonl").read_text()

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    definition = {"package-manager": "npm_and_yarn", "cooldown": {"default-days": 7}}
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
        proposal_sink=lambda *args: proposals.append(args[-1]),
    )
    assert result.complete, result.warnings
    assert len(paths) == 2 and all(not p.exists() for p in paths)
    assert len(proposals) == 1 and "create_pull_request" not in proposals[0]
    [item] = result.observations
    assert item.policy == "deferred"
    assert item.lookupComplete
    assert item.candidates and item.candidates[0].evidenceId.startswith("dependabot-0-cooldown")
    assert any("without cooldown" in w for w in result.warnings)


def test_incomplete_cooldown_comparison_keeps_result_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls: list[str] = []
    log = "updater | 2026/11/16 21:14:11 INFO Filtered out 2 versions due to cooldown\n"

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args[args.index("--file") + 1])
        if len(calls) == 1:
            return 0, log + (FIXTURES / "dependabot-no-update.jsonl").read_text()
        return 1, "updater | 2026/11/16 21:14:11 ERROR registry unavailable\n"

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    definition = {"package-manager": "npm_and_yarn", "cooldown": {"default-days": 7}}
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
    )
    assert len(calls) == 2
    [item] = result.observations
    assert item.policy == "unknown"
    assert not item.lookupComplete
    assert any("comparison run was incomplete" in w for w in result.warnings)


def test_token_selection_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.setenv("LOCAL_GITHUB_ACCESS_TOKEN", TOKEN)
    assert github_token(None) is None
    assert github_token("GITHUB_TOKEN") == TOKEN
    assert all(TOKEN not in value for value in environment().values())


@pytest.mark.parametrize("value", [None, "", " leading", "trailing ", "line\nbreak"])
def test_missing_or_invalid_token_is_not_silently_ignored(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.delenv("GAPPY_TEST_TOKEN", raising=False)
    if value is not None:
        monkeypatch.setenv("GAPPY_TEST_TOKEN", value)
    with pytest.raises(GappyError, match="missing, empty or invalid"):
        github_token("GAPPY_TEST_TOKEN")


@pytest.mark.parametrize("name", ["bad-name", "token=secret", "", " NAME", "$TOKEN"])
def test_token_option_requires_environment_variable_name(name: str) -> None:
    with pytest.raises(GappyError, match="environment variable name"):
        github_token(name)


def test_redaction_handles_common_encoded_forms() -> None:
    values = [
        TOKEN,
        base64.b64encode(TOKEN.encode()).decode(),
        base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode(),
    ]
    assert redact(" ".join(values), TOKEN) == " ".join(["[REDACTED]"] * 3)
    assert redact("untouched", None) == "untouched"


def test_write_capable_token_rejection_is_actionable_without_raw_output() -> None:
    from gappy.scan.native import diagnostics

    warnings = diagnostics(
        "dependabot",
        "for security, credentials used in update are not allowed to have write access "
        "to GitHub API\nsecret-canary",
    )
    assert any("read-only token" in warning for warning in warnings)
    assert all("secret-canary" not in warning for warning in warnings)


@pytest.mark.parametrize("provider", ["dependabot", "renovate"])
@pytest.mark.parametrize("authenticated", [False, True])
def test_runtime_auth_does_not_enter_arguments_snapshots_job_files_or_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, authenticated: bool
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[:3] == ["docker", "container", "ls"]:
            assert TOKEN not in dump(kwargs)
            return 0, ""
        calls.append((args, kwargs))
        assert TOKEN not in " ".join(args)
        env = kwargs["env"]
        key = (
            "LOCAL_GITHUB_ACCESS_TOKEN" if provider == "dependabot" else "RENOVATE_GITHUB_COM_TOKEN"
        )
        assert env.get(key) == (TOKEN if authenticated else None)
        if provider == "dependabot":
            path = Path(args[args.index("--file") + 1])
            assert TOKEN not in path.read_text()
            local = Path(args[args.index("--local") + 1])
            assert all(
                TOKEN.encode() not in path.read_bytes()
                for path in local.rglob("*")
                if path.is_file()
            )
            output = (FIXTURES / "dependabot-no-update.jsonl").read_text()
            output = output.replace("gappy-fixture", TOKEN if authenticated else "gappy-fixture")
        else:
            assert ("RENOVATE_GITHUB_COM_TOKEN" in args) == authenticated
            output = (FIXTURES / "renovate-no-update.jsonl").read_text()
            output += '\n{"msg":"branches","branches":[]}\n'
            output = output.replace("gappy-fixture", TOKEN if authenticated else "gappy-fixture")
        return 0, output

    monkeypatch.setattr("gappy.scan.session.run", native_run)
    original = snapshot()
    result = execute(
        provider,
        source,
        original,
        "fixture",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        github_token=TOKEN if authenticated else None,
    )
    assert calls
    assert result.status == "complete", result.warnings
    assert TOKEN not in dump(result.model_dump())
    assert result.snapshot == original
    assert (
        any("Explicit GitHub authentication" in warning for warning in result.warnings)
        == authenticated
    )


@pytest.mark.parametrize("command", ["scan", "full"])
def test_authentication_cli_forwarding_and_secret_free_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GAPPY_TEST_TOKEN", TOKEN)
    expected = scan_options(tmp_path, command, github_token=TOKEN)
    source = tmp_path / "source"
    source.mkdir()
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return scan("dependabot", []), scan("renovate", [])

    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--github-token-env",
            "GAPPY_TEST_TOKEN",
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected
    assert TOKEN not in result.output
    assert all(
        TOKEN not in path.read_text()
        for path in (tmp_path / "gappy-results").rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize("command", ["scan", "full"])
def test_missing_auth_env_fails_before_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GAPPY_MISSING_TOKEN", raising=False)
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--github-token-env",
            "GAPPY_MISSING_TOKEN",
        ],
    )
    assert result.exit_code == 1
    assert "missing, empty or invalid" in result.output
    assert not list(tmp_path.rglob("*.scan.json"))


@pytest.mark.parametrize("private_ghcr", [False, True])
def test_collection_passes_auth_to_both_scanners_without_changing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, private_ghcr: bool
) -> None:
    from gappy.scan.session import scan as collect

    source = tmp_path / "source"
    fixture_repo(source)
    calls = []
    credentials = GhcrCredentials("fixture-user", TOKEN) if private_ghcr else None

    def native_run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        if args[0] == "dependabot":
            return 0, "dependabot version 1.94.0"
        return 0, "linux"

    def execute_scan(provider: str, root: Path, frozen: Any, *args: Any, **kwargs: Any) -> Any:
        assert kwargs["github_token"] == TOKEN
        assert kwargs["ghcr"] == credentials
        assert TOKEN not in dump(frozen.model_dump())
        assert all(
            TOKEN.encode() not in path.read_bytes() for path in root.rglob("*") if path.is_file()
        )
        calls.append((provider, frozen))
        return scan(provider, []).model_copy(update={"snapshot": frozen})

    monkeypatch.setattr("gappy.scan.session.shutil.which", lambda name: "/fixture/" + name)
    monkeypatch.setattr("gappy.scan.session.run", native_run)
    monkeypatch.setattr("gappy.scan.session.execute", execute_scan)
    left, right = collect(
        source, "HEAD", "gappy/fixture", 10, github_token=TOKEN, mirror=False, ghcr=credentials
    )
    assert [provider for provider, _ in calls] == ["dependabot", "renovate"]
    assert left.snapshot == right.snapshot
