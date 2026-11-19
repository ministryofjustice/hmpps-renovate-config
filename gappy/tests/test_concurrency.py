import json
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from gappy.cli import main
from gappy.io import GappyError
from gappy.scan.process import run
from gappy.scan.session import execute, toolchain
from tests.test_compare import scan
from tests.test_dependabot import snapshot
from tests.test_scan import fixture_repo


def configuration(source: Path) -> None:
    config = {
        "version": 2,
        "updates": [
            {
                "package-ecosystem": ecosystem,
                "directory": directory,
                "schedule": {"interval": "weekly"},
            }
            for ecosystem, directory in (
                ("npm", "/"),
                ("helm", "/"),
                ("npm", "/nested"),
                ("github-actions", "/"),
            )
        ],
    }
    (source / ".github/dependabot.yml").write_text(yaml.safe_dump(config))


def evidence(name: str) -> str:
    return "\n".join(
        json.dumps(event)
        for event in [
            {
                "type": "update_dependency_list",
                "data": {
                    "dependencies": [
                        {"name": name, "version": "1.0", "requirements": [{"file": "manifest"}]},
                    ]
                },
            },
            {"type": "mark_as_processed", "data": {}},
        ]
    )


@pytest.mark.parametrize("ecosystem", ["github-actions", "docker"])
def test_common_ecosystem_updater_uses_bundled_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ecosystem: str
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    config = {
        "version": 2,
        "updates": [
            {
                "package-ecosystem": ecosystem,
                "directory": "/",
                "schedule": {"interval": "weekly"},
            }
        ],
    }
    (source / ".github/dependabot.yml").write_text(yaml.safe_dump(config))
    chain = toolchain()
    image = chain["dependabot"]["updaterImage:" + ecosystem]
    assert re.fullmatch(
        rf"ghcr\.io/dependabot/dependabot-updater-{ecosystem}@sha256:[a-f0-9]{{64}}",
        image,
    )
    commands = []

    def scanner(args: list[str], **kwargs: Any) -> tuple[int, str]:
        commands.append(args)
        assert args[0] == "dependabot"
        assert args[args.index("--updater-image") + 1] == image
        return 0, evidence(ecosystem)

    monkeypatch.setattr("gappy.scan.session.run", scanner)
    result = execute("dependabot", source, snapshot(), "session", "gappy/fixture", 10, chain, None)
    assert result.status == "complete", result.warnings
    assert result.toolchain["updaterImage:" + ecosystem] == image
    assert len(commands) == 1


@pytest.mark.parametrize("workers", [1, 2, 3])
def test_workers_overlap_only_independent_ecosystems_and_isolate_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    configuration(source)
    chain = toolchain()
    chain["dependabot"]["updaterImage:github-actions"] = "fixture-actions-image"
    lock = threading.Lock()
    active: set[str] = set()
    maximum = 0
    sources: dict[str, Path] = {}
    starts = []
    initial = threading.Barrier(min(workers, 3))

    @contextmanager
    def stage(*args: Any, **kwargs: Any) -> Any:
        namespace = kwargs["namespace"]
        with lock:
            assert namespace not in active
            active.add(namespace)
        try:
            yield tmp_path / namespace
        finally:
            with lock:
                active.remove(namespace)

    def scanner(args: list[str], **kwargs: Any) -> tuple[int, str]:
        nonlocal maximum
        job = yaml.safe_load(Path(args[args.index("--file") + 1]).read_text())["job"]
        ecosystem = job["package-manager"]
        local = Path(args[args.index("--local") + 1])
        with lock:
            maximum = max(maximum, len(active))
            starts.append((ecosystem, job["source"]["directory"]))
            if ecosystem not in sources:
                sources[ecosystem] = local
                first = True
            else:
                assert sources[ecosystem] == local
                first = False
            assert local != source
        if first and len(sources) <= min(workers, 3):
            initial.wait(timeout=5)
        (local / "scanner-write").write_text(ecosystem)
        time.sleep(0.03)
        assert (local / "scanner-write").read_text() == ecosystem
        return 0, evidence(ecosystem)

    monkeypatch.setattr("gappy.scan.session.prepare", lambda *args: "test-common-volume")
    monkeypatch.setattr("gappy.scan.session.staged", stage)
    monkeypatch.setattr("gappy.scan.session.run", scanner)
    result = execute(
        "dependabot",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        chain,
        None,
        cache=True,
        dependabot_workers=workers,
    )
    assert result.status == "complete", result.warnings
    assert maximum == 2 * min(workers, 3)
    assert len(set(sources.values())) == 3
    assert all(not path.exists() for path in sources.values())
    assert not (source / "scanner-write").exists()
    assert starts.index(("npm_and_yarn", "/")) < starts.index(("npm_and_yarn", "/nested"))
    assert [item.dependency.ecosystem for item in result.observations] == [
        "npm",
        "npm",
        "helm",
        "github-actions",
    ]
    assert result.cacheVolumes == ["test-common-volume"]
    assert sum("elapsed:" in warning for warning in result.warnings) == 4
    assert not active


@pytest.mark.parametrize("workers", [9, 16])
def test_runner_accepts_full_cli_worker_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    monkeypatch.setattr("gappy.scan.session.run", lambda *args, **kwargs: (0, evidence("npm")))
    result = execute(
        "dependabot",
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        dependabot_workers=workers,
    )
    assert result.status == "complete", result.warnings


@pytest.mark.parametrize("provider", ["dependabot", "renovate"])
def test_shared_cancellation_stops_before_scanner_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    cancel = threading.Event()
    cancel.set()

    def run(args: list[str], **kwargs: Any) -> tuple[int, str]:
        assert args[:3] == ["docker", "container", "ls"]
        return 0, ""

    monkeypatch.setattr("gappy.scan.session.run", run)
    result = execute(
        provider,
        source,
        snapshot(),
        "session",
        "gappy/fixture",
        10,
        toolchain(),
        None,
        cancel=cancel,
    )
    assert result.status == "cancelled"
    assert not result.observations


def test_failure_does_not_skip_other_ecosystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    configuration(source)
    chain = toolchain()
    chain["dependabot"]["updaterImage:github-actions"] = "fixture-actions-image"

    def scanner(args: list[str], **kwargs: Any) -> tuple[int, str]:
        job = yaml.safe_load(Path(args[args.index("--file") + 1]).read_text())["job"]
        if job["package-manager"] == "npm_and_yarn":
            raise GappyError("Synthetic npm failure.")
        return 0, evidence(job["package-manager"])

    monkeypatch.setattr("gappy.scan.session.run", scanner)
    result = execute("dependabot", source, snapshot(), "session", "gappy/fixture", 10, chain, None)
    assert result.status == "partial"
    assert len(result.observations) == 2
    assert sum("Synthetic npm failure" in warning for warning in result.warnings) == 2


def test_worker_interrupt_cancels_running_and_queued_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    fixture_repo(source)
    configuration(source)
    paths = []
    barrier = threading.Barrier(2)

    def scanner(args: list[str], **kwargs: Any) -> tuple[int, str]:
        local = Path(args[args.index("--local") + 1])
        paths.append(local)
        barrier.wait(timeout=5)
        job = yaml.safe_load(Path(args[args.index("--file") + 1]).read_text())["job"]
        if job["package-manager"] == "npm_and_yarn":
            raise KeyboardInterrupt
        assert kwargs["cancel"].wait(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr("gappy.scan.session.run", scanner)
    chain = toolchain()
    chain["dependabot"]["updaterImage:github-actions"] = "fixture-actions-image"
    result = execute("dependabot", source, snapshot(), "session", "gappy/fixture", 10, chain, None)
    assert result.status == "cancelled"
    assert len(paths) == 2
    assert all(not path.exists() for path in paths)


def test_main_thread_interrupt_waits_for_workers_and_preserves_completed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    source = tmp_path / "source"
    fixture_repo(source)
    configuration(source)
    completed = threading.Event()
    waiting = threading.Event()
    paths = []

    pool = ThreadPoolExecutor(max_workers=2)
    count = 0

    def submit_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        future = ThreadPoolExecutor.submit(pool, *args, **kwargs)
        if count == 2:
            original = future.result
            interrupted = False

            def result(*args: Any, **kwargs: Any) -> Any:
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    assert completed.wait(5)
                    assert waiting.wait(5)
                    raise KeyboardInterrupt
                return original(*args, **kwargs)

            future.result = result
        return future

    pool.submit = submit_once
    monkeypatch.setattr("gappy.scan.session.ThreadPoolExecutor", lambda **kwargs: pool)

    def scanner(args: list[str], **kwargs: Any) -> tuple[int, str]:
        paths.append(Path(args[args.index("--local") + 1]))
        job = yaml.safe_load(Path(args[args.index("--file") + 1]).read_text())["job"]
        if job["package-manager"] == "npm_and_yarn":
            completed.set()
            return 0, evidence("npm")
        waiting.set()
        assert kwargs["cancel"].wait(5)
        raise KeyboardInterrupt

    monkeypatch.setattr("gappy.scan.session.run", scanner)
    chain = toolchain()
    chain["dependabot"]["updaterImage:github-actions"] = "fixture-actions-image"
    result = execute("dependabot", source, snapshot(), "session", "gappy/fixture", 10, chain, None)
    assert result.status == "cancelled"
    assert result.observations
    assert all(item.dependency.ecosystem == "npm" for item in result.observations)
    assert all(not path.exists() for path in paths)


def test_process_cancellation_terminates_running_child() -> None:
    import sys

    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            run([sys.executable, "-c", "import time; time.sleep(20)"], cancel=cancel)
    finally:
        timer.cancel()
        timer.join()
    assert time.monotonic() - started < 5
    with pytest.raises(KeyboardInterrupt):
        run(["/never-start-this"], cancel=cancel)


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("workers", [1, 3])
def test_workers_cli_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, workers: int
) -> None:
    from tests.test_cli import mock_collection, scan_options

    expected = scan_options(tmp_path, command, dependabot_workers=workers)
    monkeypatch.chdir(tmp_path)
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
            "--dependabot-workers",
            str(workers),
        ],
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert sorted(seen, key=lambda x: x.get("cache_scope", "")) == expected


@pytest.mark.parametrize("command", ["scan", "full"])
@pytest.mark.parametrize("workers", ["0", "17", "invalid"])
def test_invalid_worker_count_rejected(command: str, workers: str) -> None:
    result = CliRunner().invoke(
        main,
        [
            command,
            "--repository",
            "gappy/fixture",
            "--dependabot-workers",
            workers,
        ],
    )
    assert result.exit_code == 2
