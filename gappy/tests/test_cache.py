from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.cache import (
    DOWNLOAD_ENV,
    TARGET,
    VOLUME,
    cache_namespace,
    prepare,
    proxy_cache_scope,
    scoped_ecosystem,
    staged,
    volume_name,
)
from gappy.scan.session import execute, toolchain
from tests.test_compare import scan
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES, fixture_repo


def test_cache_volume_is_common_across_repositories_scanners_and_images() -> None:
    args = ("owner/repo", "dependabot", "npm", "image@sha256:abc")
    name = volume_name(*args)
    assert name == volume_name(*args)
    assert name == volume_name("OWNER/REPO", *args[1:])
    assert {
        name,
        volume_name("other/repo", *args[1:]),
        volume_name("owner/repo", "renovate", *args[2:]),
        volume_name("owner/repo", "dependabot", "pip", args[3]),
        volume_name(*args[:3], "different-image"),
    } == {VOLUME}


def test_cache_namespaces_separate_incompatible_toolchains() -> None:
    namespace = cache_namespace("dependabot", "npm", "image")
    assert namespace == cache_namespace("dependabot", "npm", "image")
    assert (
        len(
            {
                namespace,
                cache_namespace("renovate", "npm", "image"),
                cache_namespace("dependabot", "pip", "image"),
                cache_namespace("dependabot", "npm", "other-image"),
            }
        )
        == 4
    )


def test_variant_cache_namespaces_are_independent_and_stable() -> None:
    scopes = {
        scoped_ecosystem(repository, "npm", variant)
        for repository in ("owner/repo", "other/repo")
        for variant in ("baseline", "optimised")
    }
    assert len(scopes) == 4
    assert scoped_ecosystem("OWNER/REPO", "npm", "baseline") == scoped_ecosystem(
        "owner/repo", "npm", "baseline"
    )
    assert scoped_ecosystem("owner/repo", "npm", None) == "npm"
    assert len({cache_namespace("dependabot", scope, "image") for scope in scopes}) == 4
    assert (
        len(
            {
                cache_namespace(
                    "dependabot", proxy_cache_scope("owner/repo", scope, "proxy"), "image"
                )
                for scope in scopes
            }
        )
        == 4
    )


def test_proxy_cache_is_scoped_to_repository_ecosystem_and_image() -> None:
    scope = proxy_cache_scope("owner/repo", "npm", "proxy-image")
    assert scope == proxy_cache_scope("OWNER/REPO", "npm", "proxy-image")
    assert (
        len(
            {
                scope,
                proxy_cache_scope("other/repo", "npm", "proxy-image"),
                proxy_cache_scope("owner/repo", "docker", "proxy-image"),
                proxy_cache_scope("owner/repo", "npm", "other-image"),
            }
        )
        == 4
    )
    assert cache_namespace("dependabot", scope, "updater") != cache_namespace(
        "dependabot", "npm", "updater"
    )


@pytest.mark.parametrize("failure", [0, 1])
def test_cache_setup_failure_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    failure: int,
) -> None:
    calls = []

    def run(*args: Any, **kwargs: Any) -> tuple[int, str]:
        calls.append(args[0])
        return (1 if len(calls) == failure + 1 else 0), ""

    monkeypatch.setattr("gappy.scan.cache.run", run)
    with pytest.raises(GappyError, match="cache"):
        prepare("owner/repo", "dependabot", "npm", "image", {}, 30)
    assert len(calls) == failure + 1


def test_cache_initializes_only_root_and_never_mounts_host_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        return 0, ""

    monkeypatch.setattr("gappy.scan.cache.run", run)
    name = prepare("owner/repo", "renovate", "all", "image", {}, 30)
    assert calls[0][-1] == name
    assert "--network=none" in calls[1]
    assert "0:0" in calls[1]
    assert f"type=volume,src={name},dst={TARGET}" in calls[1]
    assert calls[1][-2:] == [
        TARGET + "/" + cache_namespace("renovate", "all", "image"),
        "/home/ubuntu",
    ]
    assert "-R" not in calls[1]
    assert all(
        value.startswith(TARGET) for key, value in DOWNLOAD_ENV.items() if key != "MAVEN_OPTS"
    )


@pytest.mark.parametrize("fail_at", [None, "restore", "persist"])
def test_dependabot_staging_persists_and_cleans_owned_helper(
    monkeypatch: pytest.MonkeyPatch,
    fail_at: str | None,
) -> None:
    calls = []

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        if args[:2] == ["docker", "cp"]:
            restoring = args[2].startswith("gappy-cache-transfer-")
            return int(fail_at == ("restore" if restoring else "persist")), ""
        return 0, ""

    monkeypatch.setattr("gappy.scan.cache.run", run)
    if fail_at:
        with pytest.raises(GappyError, match="cache volume"):
            with staged("test-volume", "image", {}, 30, namespace="test-namespace"):
                pass
    else:
        with staged("test-volume", "image", {}, 30, namespace="test-namespace") as path:
            assert path.parent.stat().st_mode & 0o777 == 0o700
            assert path.stat().st_mode & 0o777 == 0o777
            path.joinpath("download").write_text("synthetic package archive")
        assert not path.parent.exists()
    assert calls[-1][:2] == ["docker", "rm"]
    assert calls[-1][2].startswith("gappy-cache-transfer-")
    assert "test-volume" not in calls[-1]
    assert f"type=volume,src=test-volume,dst={TARGET},volume-subpath=test-namespace" in calls[0]


@pytest.mark.parametrize("provider", ["dependabot", "renovate"])
@pytest.mark.parametrize("cached", [True, False])
@pytest.mark.parametrize("scope", [None, "baseline", "optimised"])
def test_native_cache_mounts_and_exports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    cached: bool,
    scope: str | None,
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    calls = []
    setups = []

    def prepare(*args: Any) -> str:
        setups.append(args)
        return "gappy-cache-test"

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        calls.append(args)
        if args[:3] == ["docker", "container", "ls"]:
            return 0, ""
        path = FIXTURES / "policy/group"
        capture = next(path.glob(f"{provider}-*.jsonl"))
        return 0, capture.read_text()

    monkeypatch.setattr("gappy.scan.session.run", run)
    monkeypatch.setattr("gappy.scan.session.prepare", prepare)

    @contextmanager
    def staged(*args: Any, **kwargs: Any) -> Any:
        spec = toolchain()["dependabot"]
        namespace = kwargs["namespace"]
        ecosystem = scoped_ecosystem("owner/repo", "npm", scope)
        updater = cache_namespace("dependabot", ecosystem, spec["updaterImage"])
        proxy = cache_namespace(
            "dependabot",
            proxy_cache_scope("owner/repo", ecosystem, spec["proxyImage"]),
            spec["updaterImage"],
        )
        assert namespace in {updater, proxy}
        yield tmp_path / ("gappy-cache-test" if namespace == updater else "proxy-cache-test")

    monkeypatch.setattr("gappy.scan.session.staged", staged)
    result = execute(
        provider,
        source,
        snapshot(),
        "session",
        "owner/repo",
        30,
        toolchain(),
        None,
        cache=cached,
        cache_scope=scope,
    )
    assert result.status == "complete", result.warnings
    assert result.cacheMode == ("persistent" if cached else "fresh")
    assert result.cacheVolumes == (["gappy-cache-test"] if cached else [])
    assert len(setups) == (2 if cached and provider == "dependabot" else int(cached))
    if cached:
        ecosystem = scoped_ecosystem(
            "owner/repo", "npm" if provider == "dependabot" else "all-managers", scope
        )
        assert setups[0][2] == ecosystem
        if provider == "dependabot":
            assert setups[1][2] == proxy_cache_scope(
                "owner/repo", ecosystem, toolchain()["dependabot"]["proxyImage"]
            )
    command = calls[0]
    assert ("gappy-cache-test" in " ".join(command)) == cached
    if cached and provider == "dependabot":
        assert command[command.index("--volume") + 1] == (
            f"{tmp_path / 'gappy-cache-test'}:{TARGET}"
        )
        assert f"npm_config_cache={TARGET}/npm" in command
        assert not any(value.startswith("XDG_CACHE_HOME=") for value in command)
        assert command[command.index("--cache") + 1] == str(tmp_path / "proxy-cache-test")
        assert "proxy-cache-test" not in command[command.index("--volume") + 1]
    if not cached:
        assert "--cache" not in command
    if cached and provider == "renovate":
        namespace = cache_namespace(
            "renovate",
            scoped_ecosystem("owner/repo", "all-managers", scope),
            toolchain()["renovate"]["image"],
        )
        assert (
            f"type=volume,src=gappy-cache-test,dst={TARGET},volume-subpath={namespace}"
        ) in command
        assert f"RENOVATE_CACHE_DIR={TARGET}/renovate" in command
        assert sum(value.startswith("RENOVATE_CACHE_DIR=") for value in command) == 1


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("mirror", [None, "https://mirror.test/repository/npmjs/"])
def test_no_cache_cli_reaches_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    mirror: str | None,
) -> None:
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(
        tmp_path, command, cache=False, **({"npm_mirror": mirror} if mirror else {})
    )
    source = tmp_path / "source"
    source.mkdir()
    seen = []

    def collect(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return scan("dependabot", []), scan("renovate", [])

    monkeypatch.chdir(tmp_path)
    mock_collection(monkeypatch, collect)
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--source",
            str(source),
            "--no-cache",
        ]
        + (["--npm-mirror", mirror] if mirror else []),
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected
