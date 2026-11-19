import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gappy.io import GappyError
from gappy.scan.native import (
    capture_renovate_line,
    dependabot,
    diagnostics,
    registry_identity,
    renovate,
)
from gappy.scan.process import run
from gappy.scan.snapshot import content_hash, freeze

FIXTURES = Path(__file__).parent / "fixtures" / "native"


@pytest.mark.parametrize("scenario", ["patch", "no-update"])
def test_real_dependabot_captures(scenario: str) -> None:
    observations, complete = dependabot(
        (FIXTURES / f"dependabot-{scenario}.jsonl").read_text(),
        "/",
        "https://host.docker.internal:18947",
    )
    assert complete
    assert observations[0].dependency.package == "gappy-fixture"
    assert observations[0].lookupComplete
    if scenario == "patch":
        assert observations[0].candidates[0].targetVersion == "1.2.2"
    else:
        assert observations[0].candidates == []


@pytest.mark.parametrize("scenario", ["patch", "no-update"])
def test_real_renovate_captures(scenario: str) -> None:
    observations, complete = renovate((FIXTURES / f"renovate-{scenario}.jsonl").read_text())
    assert complete
    assert observations[0].lookupComplete
    assert bool(observations[0].candidates) == (scenario == "patch")


@pytest.mark.parametrize("pending", ["isPending", "pendingChecks"])
@pytest.mark.parametrize(
    ("field", "target"),
    [("newVersion", "1.2.2"), ("newValue", "v2"), ("newDigest", "sha256:" + "b" * 64)],
)
def test_renovate_retains_explicitly_deferred_native_targets(
    pending: str, field: str, target: str
) -> None:
    events = [
        json.loads(line) for line in (FIXTURES / "renovate-patch.jsonl").read_text().splitlines()
    ]
    dependency = events[0]["config"]["npm"][0]["deps"][0]
    dependency["updates"] = [{field: target, pending: True}]
    events.append({"msg": "branches", "branches": []})
    observations, complete = renovate(
        "\n".join(map(json.dumps, events)), policy_filtered=True, require_branches=True
    )
    assert complete
    assert observations[0].policy == "deferred"
    assert observations[0].lookupComplete
    assert [candidate.targetVersion for candidate in observations[0].candidates] == [target]


@pytest.mark.parametrize("blocked", ["disabled", "ignored", "isSkipped", "mixed"])
def test_renovate_deferred_retention_does_not_restore_filtered_or_mixed_updates(
    blocked: str,
) -> None:
    events = [
        json.loads(line) for line in (FIXTURES / "renovate-patch.jsonl").read_text().splitlines()
    ]
    dependency = events[0]["config"]["npm"][0]["deps"][0]
    dependency["updates"] = [{"newVersion": "1.2.2", "pendingChecks": True}]
    if blocked in {"disabled", "ignored"}:
        dependency["skipReason"] = blocked
    elif blocked == "isSkipped":
        dependency["updates"][0]["isSkipped"] = True
    else:
        dependency["updates"].append({"newVersion": "2.0.0"})
    events.append({"msg": "branches", "branches": []})
    observations, complete = renovate(
        "\n".join(map(json.dumps, events)), policy_filtered=True, require_branches=True
    )
    assert complete
    assert not observations[0].candidates
    assert observations[0].policy == (
        "excluded" if blocked in {"disabled", "ignored"} else "unknown"
    )


def test_renovate_actionable_target_is_not_replaced_by_a_pending_alternative() -> None:
    events = [
        json.loads(line) for line in (FIXTURES / "renovate-patch.jsonl").read_text().splitlines()
    ]
    dependency = events[0]["config"]["npm"][0]["deps"][0]
    dependency["updates"] = [
        {"newVersion": "1.2.2"},
        {"newVersion": "2.0.0", "pendingChecks": True},
    ]
    events.append(
        {
            "msg": "branches",
            "branches": [
                {
                    "upgrades": [
                        {
                            "packageFile": "package.json",
                            "depName": "gappy-fixture",
                            "newVersion": "1.2.2",
                        }
                    ]
                }
            ],
        }
    )
    observations, complete = renovate(
        "\n".join(map(json.dumps, events)), policy_filtered=True, require_branches=True
    )
    assert complete
    assert observations[0].policy == "eligible"
    assert [candidate.targetVersion for candidate in observations[0].candidates] == ["1.2.2"]


@pytest.mark.parametrize("parser", [renovate, lambda text: dependabot(text, "/", None)])
def test_absent_or_malformed_native_output_is_error(parser: object) -> None:
    for text in ("", "{}", "{invalid"):
        with pytest.raises(GappyError):
            parser(text)


def test_native_error_prevents_negative_claim() -> None:
    text = (FIXTURES / "dependabot-no-update.jsonl").read_text()
    text += '{"type":"record_update_job_error","data":{"error-type":"fixture"}}\n'
    observations, complete = dependabot(text, "/", None)
    assert not complete
    assert not observations[0].lookupComplete


def test_dependabot_failed_job_retains_discovery_without_claiming_lookup_complete() -> None:
    text = (FIXTURES / "dependabot-no-update.jsonl").read_text()
    text = "\n".join(
        line for line in text.splitlines() if json.loads(line).get("type") != "mark_as_processed"
    )
    text += (
        '\n{"type":"record_update_job_error",'
        '"data":{"error-type":"private_source_authentication_failure",'
        '"error-details":{"secret":"private-canary"}}}\n'
    )
    observations, complete = dependabot(text, "/", None)
    assert observations
    assert not complete
    assert all(not item.lookupComplete for item in observations)
    messages = diagnostics("dependabot", text)
    assert "private_source_authentication_failure" in " ".join(messages)
    assert "private-canary" not in " ".join(messages)


def test_dependabot_incomplete_job_retains_discovery_without_terminal_event() -> None:
    text = (FIXTURES / "dependabot-no-update.jsonl").read_text()
    text = "\n".join(
        line for line in text.splitlines() if json.loads(line).get("type") != "mark_as_processed"
    )
    observations, complete = dependabot(text, "/", None)
    assert observations
    assert not complete
    assert all(not item.lookupComplete for item in observations)


def test_native_diagnostics_are_actionable_and_do_not_export_arbitrary_messages() -> None:
    assert (
        "GitHub token"
        in diagnostics(
            "renovate", '{"level":40,"msg":"GitHub token is required for some dependencies"}'
        )[0]
    )
    for provider, text in (
        ("dependabot", '{"type":"record_update_job_error","data":{"error-type":"secret-canary"}}'),
        ("renovate", '{"level":40,"msg":"secret-canary","err":{"token":"secret-canary"}}'),
    ):
        messages = diagnostics(provider, text)
        assert messages
        assert "secret-canary" not in " ".join(messages)
    assert diagnostics("dependabot", '{"type":"mark_as_processed","data":{}}') == []
    assert diagnostics("renovate", '{"level":30,"msg":"Repository finished"}') == []


def test_dependabot_launcher_timeout_diagnostic_is_specific_and_sanitized() -> None:
    text = "    cli | 2026/11/12 17:50:16 update timed out after 3m0s\nprivate-canary\n"
    messages = diagnostics("dependabot", text)
    assert len(messages) == 1
    assert "native job timeout" in messages[0]
    assert "3m0s" in messages[0]
    assert "--timeout-seconds" in messages[0]
    assert "private-canary" not in messages[0]
    assert diagnostics("dependabot", '{"message":"update timed out after 3m0s"}') == []


def test_subprocess_timeout_reports_the_configured_duration() -> None:
    with pytest.raises(GappyError, match=r"execution timeout \(0\.1s\)"):
        run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)


def test_scanner_subprocess_does_not_inherit_piped_stdin() -> None:
    parent = (
        "import sys; from gappy.scan.process import run; "
        "code, output = run([sys.executable, '-c', "
        "'import sys; print(repr(sys.stdin.read()))']); "
        "print(output.strip()); sys.exit(code)"
    )
    result = subprocess.run(
        [sys.executable, "-c", parent],
        input="secret-canary\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "''"
    assert "secret-canary" not in result.stderr


def test_renovate_partial_scan_explains_required_github_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gappy.scan.session import execute, toolchain
    from tests.test_dependabot import snapshot

    source = tmp_path / "source"
    fixture_repo(source)
    output = (FIXTURES / "renovate-patch.jsonl").read_text()
    output += (
        '\n{"msg":"branches","branches":[]}\n'
        '{"level":40,"msg":"GitHub token is required for some dependencies"}\n'
    )

    def run_scanner(args: list[str], **kwargs: object) -> tuple[int, str]:
        return (0, output) if "run" in args else (0, "")

    monkeypatch.setattr("gappy.scan.session.run", run_scanner)
    result = execute(
        "renovate", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "partial"
    assert any("requires a GitHub token" in warning for warning in result.warnings)
    assert all(not item.lookupComplete for item in result.observations)


def test_renovate_nonzero_exit_does_not_claim_completed_lookups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gappy.scan.session import execute, toolchain
    from tests.test_dependabot import snapshot

    source = tmp_path / "source"
    fixture_repo(source)
    output = (FIXTURES / "renovate-patch.jsonl").read_text()
    output += '\n{"msg":"branches","branches":[]}\n'

    def run_scanner(args: list[str], **kwargs: object) -> tuple[int, str]:
        return (1, output) if "run" in args else (0, "")

    monkeypatch.setattr("gappy.scan.session.run", run_scanner)
    result = execute(
        "renovate", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "failed"
    assert result.observations
    assert all(not item.lookupComplete for item in result.observations)


def test_renovate_startup_auth_failure_retains_safe_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gappy.scan.session import execute, toolchain
    from tests.test_dependabot import snapshot

    source = tmp_path / "source"
    fixture_repo(source)
    output = "\n".join(
        json.dumps(event)
        for event in (
            {"level": 40, "msg": "github.com token 401 unauthorized"},
            {
                "level": 50,
                "msg": "Repository has invalid config",
                "err": {
                    "message": "config-validation",
                    "validationError": "secret-canary",
                    "stack": "secret-canary",
                },
            },
        )
    )

    def run_scanner(args: list[str], **kwargs: object) -> tuple[int, str]:
        if "run" in args:
            retained = "".join(
                line + "\n" for line in output.splitlines() if capture_renovate_line(line)
            )
            return 0, retained
        return 0, ""

    monkeypatch.setattr("gappy.scan.session.run", run_scanner)
    result = execute(
        "renovate", source, snapshot(), "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "failed"
    assert not result.observations
    warnings = " ".join(result.warnings)
    assert "HTTP 401 Unauthorized" in warnings
    assert "--github-token-env" in warnings
    assert "configuration" in warnings
    assert "preset" in warnings
    assert "secret-canary" not in result.model_dump_json()


def test_registry_identity_removes_secrets() -> None:
    value = registry_identity("https://user:canary@example.test/registry?token=canary")
    assert value == "https://example.test/registry"
    assert "canary" not in value


def test_process_handles_failure_and_does_not_log_raw_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = run([sys.executable, "-c", "print('private-canary'); raise SystemExit(3)"])
    assert code == 3
    assert "private-canary" in output
    assert "private-canary" not in capsys.readouterr().out


def test_process_timeout() -> None:
    with pytest.raises(GappyError, match="timeout"):
        run([sys.executable, "-c", "import time; time.sleep(20)"], timeout=0.1)


def test_missing_process() -> None:
    with pytest.raises(GappyError, match="executable"):
        run(["/nonexistent-gappy-executable"])


def test_renovate_trace_larger_than_limit_keeps_required_evidence() -> None:
    fixture = FIXTURES / "renovate-patch.jsonl"
    code, output = run(
        [
            sys.executable,
            "-c",
            "import pathlib, sys; "
            'line = \'{"level":10,"msg":"irrelevant trace",'
            "\"detail\":\"' + 'x'*1024 + '\"}\\n'; "
            "sys.stdout.write(line * 17000); "
            "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text())",
            str(fixture),
        ],
        capture_line=capture_renovate_line,
    )
    assert code == 0
    expected = renovate(fixture.read_text())
    assert renovate(output) == expected
    assert "irrelevant trace" not in output
    assert len(output.encode()) < 16 * 1024 * 1024


@pytest.mark.parametrize("filtered", [False, True])
def test_process_still_limits_retained_output(filtered: bool) -> None:
    with pytest.raises(GappyError, match="16 MiB"):
        run(
            [sys.executable, "-c", "print('x' * 1024 * 1024) " + "; print('x' * 1024 * 1024)" * 16],
            capture_line=(lambda line: True) if filtered else None,
        )


def test_process_limits_unterminated_lines_even_if_filtered_out() -> None:
    with pytest.raises(GappyError, match="16 MiB"):
        run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * (17 * 1024 * 1024))"],
            capture_line=lambda line: False,
        )


def test_capture_renovate_preserves_warning_error_and_final_unterminated_line() -> None:
    text = (
        '{"level":40,"msg":"warning"}\n'
        '{"level":50,"msg":"failure"}\n'
        '{"level":10,"msg":"irrelevant"}\n'
        '{"level":20,"msg":"Repository finished"}'
    )
    code, output = run(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", text],
        capture_line=capture_renovate_line,
    )
    assert code == 0
    assert output == text.replace('{"level":10,"msg":"irrelevant"}\n', "")


def test_capture_renovate_rejects_malformed_structured_evidence() -> None:
    with pytest.raises(GappyError, match="malformed"):
        run(
            [sys.executable, "-c", "print('{invalid')"],
            capture_line=capture_renovate_line,
        )
    assert not capture_renovate_line("unstructured diagnostic\n")


def fixture_repo(root: Path) -> None:
    root.mkdir()
    import shutil

    shutil.copytree(FIXTURES / "repository", root, dirs_exist_ok=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Gappy Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Synthetic scan fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *args], env=env, check=True, capture_output=True)


def test_snapshot_is_immutable_and_ignores_untracked(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "untracked-secret").write_text("private-canary")
    (source / "package.json").write_text("dirty")
    target = tmp_path / "frozen"
    target.mkdir()
    snapshot = freeze(source, "HEAD", target)
    assert len(snapshot.contentHash) == 64
    assert not (target / "untracked-secret").exists()
    assert (target / "package.json").read_text() != "dirty"
    assert (source / "package.json").read_text() == "dirty"


def test_snapshot_missing_revision_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(GappyError):
        freeze(tmp_path, "HEAD", tmp_path / "out")


@pytest.mark.parametrize("name", ["package.json", "packages/app/package.json"])
@pytest.mark.parametrize(
    "content",
    [
        '{"name":"application","version":"0.0.1"}',
        '{"name":"application","private":false}',
        '{"name":"application","private":true}',
        '{"name":"application","private":null}',
        '{"name":',
        "[]",
    ],
)
def test_snapshot_defaults_missing_private_only(tmp_path: Path, name: str, content: str) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    manifest = source / name
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(content)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Gappy Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Package fixture",
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "frozen"
    target.mkdir()
    snapshot = freeze(source, "HEAD", target)
    if content == '{"name":"application","version":"0.0.1"}':
        assert json.loads((target / name).read_text()) == {
            "name": "application",
            "version": "0.0.1",
            "private": True,
        }
    else:
        assert (target / name).read_text() == content
    assert manifest.read_text() == content
    assert snapshot.contentHash == content_hash(target)
    other = tmp_path / "other"
    other.mkdir()
    assert freeze(source, "HEAD", other).contentHash == snapshot.contentHash


def test_supplied_configs_replace_project_configs_only_in_snapshot(tmp_path: Path) -> None:
    import hashlib

    source = tmp_path / "source"
    fixture_repo(source)
    original = (source / "renovate.json").read_bytes()
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "dependabot.yml").write_text("version: 2\nupdates: []\n")
    (configs / "renovate.json").write_text('{"enabledManagers":["npm"]}')
    target = tmp_path / "frozen"
    target.mkdir()
    result = freeze(source, "HEAD", target, config_dir=configs)
    for source_name, target_name in (
        ("dependabot.yml", ".github/dependabot.yml"),
        ("renovate.json", "renovate.json"),
    ):
        content = (configs / source_name).read_bytes()
        assert (target / target_name).read_bytes() == content
        assert result.configHashes[target_name] == hashlib.sha256(content).hexdigest()
    assert (source / "renovate.json").read_bytes() == original
    (configs / "renovate.json").write_text("{}")
    other = tmp_path / "other"
    other.mkdir()
    assert freeze(source, "HEAD", other, config_dir=configs).contentHash != result.contentHash


def test_supplied_configs_allow_project_without_bot_configs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    configs = tmp_path / "configs"
    configs.mkdir()
    for original, name in (
        (".github/dependabot.yml", "dependabot.yml"),
        ("renovate.json", "renovate.json"),
    ):
        (configs / name).write_bytes((source / original).read_bytes())
    subprocess.run(
        ["git", "-C", str(source), "rm", ".github/dependabot.yml", "renovate.json"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Remove repository configs",
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "frozen"
    target.mkdir()
    result = freeze(source, "HEAD", target, config_dir=configs)
    assert set(result.configHashes) == {".github/dependabot.yml", "renovate.json"}
    assert not (source / "renovate.json").exists()
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(GappyError, match="Revision must contain"):
        freeze(source, "HEAD", missing)


def test_supplied_configs_must_both_exist(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    target = tmp_path / "frozen"
    target.mkdir()
    with pytest.raises(GappyError, match="readable dependabot.yml and renovate.json"):
        freeze(source, "HEAD", target, config_dir=tmp_path)


def test_unsupported_policy_is_not_silently_removed(tmp_path: Path) -> None:
    import yaml

    from gappy.scan.session import execute, toolchain

    source = tmp_path / "source"
    fixture_repo(source)
    target = tmp_path / "frozen"
    target.mkdir()
    snapshot = freeze(source, "HEAD", target)
    path = target / ".github/dependabot.yml"
    config = yaml.safe_load(path.read_text())
    config["updates"][0]["unverified-option"] = {"default-days": 7}
    path.write_text(yaml.safe_dump(config))
    before = path.read_bytes()
    result = execute(
        "dependabot", target, snapshot, "fixture", "gappy/fixture", 10, toolchain(), None
    )
    assert result.status == "unsupported"
    assert result.observations == []
    assert path.read_bytes() == before


def test_source_symlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    (source / "escape").symlink_to("/etc/passwd")
    subprocess.run(["git", "-C", str(source), "add", "escape"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Synthetic symlink fixture",
        ],
        check=True,
        capture_output=True,
    )
    target = tmp_path / "frozen"
    target.mkdir()
    with pytest.raises(GappyError, match="symbolic"):
        freeze(source, "HEAD", target)


def test_process_arguments_are_not_shell_expanded() -> None:
    code, output = run([sys.executable, "-c", "import sys; print(sys.argv[1])", "$(echo unsafe)"])
    assert code == 0
    assert output.strip() == "$(echo unsafe)"


def test_default_checkout_clone_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from gappy.scan.snapshot import checkout

    captured = []

    def clone(args: list[str], **kwargs: object) -> tuple[int, str]:
        captured.append((args, kwargs))
        Path(args[-1]).mkdir()
        return 0, ""

    monkeypatch.setattr("gappy.scan.snapshot.run", clone)
    monkeypatch.setenv("GITHUB_TOKEN", "secret-canary")
    with checkout(None, "owner/repo") as path:
        assert path.is_dir()
        created = path
    assert not created.parent.exists()
    args, kwargs = captured[0]
    assert args[:5] == ["git", "clone", "--no-checkout", "--", "https://github.com/owner/repo.git"]
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GITHUB_TOKEN" not in kwargs["env"]


def test_default_checkout_cleanup_after_scan_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from gappy.scan.snapshot import checkout

    def clone(args: list[str], **kwargs: object) -> tuple[int, str]:
        Path(args[-1]).mkdir()
        return 0, ""

    monkeypatch.setattr("gappy.scan.snapshot.run", clone)
    with pytest.raises(GappyError):
        with checkout(None, "owner/repo") as path:
            created = path
            raise GappyError("scanner failed")
    assert not created.parent.exists()
