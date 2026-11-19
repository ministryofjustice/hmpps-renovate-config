"""Opt-in real Docker check; uses only a synthetic repository and local registry."""

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from gappy.cli import main
from gappy.contracts import Scan
from gappy.scan.cache import cache_namespace
from tests.test_scan import FIXTURES, fixture_repo

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "scenario",
    [
        "patch",
        "no-update",
        "transitive",
        "group",
        "ignore-patch",
        "ignore-major",
        "cooldown",
        "cooldown-recent",
        "cooldown-exclude",
        "disable-patch",
        "directories",
        "helm",
        "helm-mirror",
        "docker-mirror",
        "pypi-mirror",
        "cache-reuse",
        "parallel",
    ],
)
def test_real_docker_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    if not shutil.which("dependabot") or not shutil.which("docker"):
        pytest.skip("Pinned Dependabot launcher and Docker must be available.")
    if scenario == "cache-reuse":
        monkeypatch.setattr("gappy.scan.cache.VOLUME", "gappy-test-cache-" + uuid.uuid4().hex)
    repository = (
        "gappy/cache-fixture-" + uuid.uuid4().hex
        if scenario == "cache-reuse"
        else "gappy/synthetic-fixture"
    )
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=host.docker.internal",
            "-addext",
            "subjectAltName=DNS:host.docker.internal,DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    source = tmp_path / "source"
    fixture_repo(source)
    dependabot_path = source / ".github/dependabot.yml"
    renovate_path = source / "renovate.json"
    dependabot_config = yaml.safe_load(dependabot_path.read_text())
    renovate_config = json.loads(renovate_path.read_text())
    entry = dependabot_config["updates"][0]
    if scenario == "parallel":
        dependabot_config["updates"].append(
            {
                "package-ecosystem": "helm",
                "directory": "/",
                "schedule": {"interval": "weekly"},
            }
        )
        renovate_config["enabledManagers"] = ["npm", "helmv3"]
        (source / "Chart.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "v2",
                    "name": "application",
                    "version": "0.1.0",
                    "dependencies": [
                        {
                            "name": "gappy-fixture",
                            "version": "1.2.0",
                            "repository": "https://host.docker.internal:18947/charts",
                        }
                    ],
                }
            )
        )
    if scenario in {"helm", "helm-mirror"}:
        (source / "package.json").unlink()
        entry["package-ecosystem"] = "helm"
        renovate_config = {"enabledManagers": ["helmv3"]}
        (source / "Chart.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "v2",
                    "name": "application",
                    "version": "0.1.0",
                    "dependencies": [
                        {
                            "name": "gappy-fixture",
                            "version": "1.2.0",
                            "repository": "https://unreachable.example/charts"
                            if scenario == "helm-mirror"
                            else "https://host.docker.internal:18947/charts",
                        }
                    ],
                }
            )
        )
    if scenario == "docker-mirror":
        (source / "package.json").unlink()
        (source / "Dockerfile").write_text("FROM gappy-fixture:1.2.0\n")
        entry["package-ecosystem"] = "docker"
        renovate_config = {"enabledManagers": ["dockerfile"]}
    if scenario == "pypi-mirror":
        (source / "package.json").unlink()
        (source / "requirements.txt").write_text("gappy-fixture==1.2.0\n")
        entry["package-ecosystem"] = "pip"
        renovate_config = {"enabledManagers": ["pip_requirements"]}
    if scenario == "no-update":
        path = source / "package.json"
        path.write_text(path.read_text().replace("1.2.0", "1.2.2"))
    if scenario == "transitive":
        path = source / "package.json"
        manifest = json.loads(path.read_text())
        manifest["dependencies"]["gappy-fixture"] = "1.2.2"
        path.write_text(json.dumps(manifest))
        (source / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": manifest["name"],
                    "version": manifest["version"],
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {
                        "": {
                            "name": manifest["name"],
                            "version": manifest["version"],
                            "dependencies": {"gappy-fixture": "1.2.2"},
                        },
                        "node_modules/gappy-fixture": {
                            "version": "1.2.2",
                            "dependencies": {"gappy-test-indirect-leaf": "1.0.0"},
                        },
                        "node_modules/gappy-test-indirect-leaf": {"version": "1.0.0"},
                    },
                }
            )
        )
    if scenario == "group":
        entry["groups"] = {"non-major": {"update-types": ["minor", "patch"]}}
        renovate_config["packageRules"].append(
            {
                "matchManagers": ["npm"],
                "groupName": "non-major",
            }
        )
    if scenario.startswith("ignore-") or scenario == "disable-patch":
        update_type = "major" if scenario == "ignore-major" else "patch"
        entry["ignore"] = [
            {
                "dependency-name": "gappy-*",
                "update-types": [f"version-update:semver-{update_type}"],
            }
        ]
        renovate_config["packageRules"].append(
            {
                "matchPackageNames": ["gappy-fixture"],
                "matchUpdateTypes": [update_type],
                "enabled": False,
            }
        )
    if scenario.startswith("cooldown"):
        entry["cooldown"] = {"default-days": 7}
        renovate_config["packageRules"].append(
            {
                "matchPackageNames": ["gappy-fixture"],
                "minimumReleaseAge": "7 days",
            }
        )
        if scenario == "cooldown-exclude":
            entry["cooldown"]["exclude"] = ["gappy-fixture"]
            renovate_config["packageRules"].append(
                {
                    "matchPackageNames": ["gappy-fixture"],
                    "minimumReleaseAge": "0 days",
                }
            )
    if scenario == "directories":
        entry.pop("directory")
        entry["directories"] = ["/", "/packages/app"]
        nested = source / "packages/app"
        nested.mkdir(parents=True)
        shutil.copy(source / "package.json", nested / "package.json")
        shutil.copy(source / ".npmrc", nested / ".npmrc")
    dependabot_path.write_text(yaml.safe_dump(dependabot_config))
    renovate_path.write_text(json.dumps(renovate_config))
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
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
            "--allow-empty",
            "-qm",
            "Controlled policy scenario",
        ],
        check=True,
        capture_output=True,
    )
    import gappy.scan.session
    from gappy.scan.native import json_events

    execute = gappy.scan.session.run
    captured = []

    def capture(args: list[str], **kwargs: object) -> tuple[int, str]:
        if (
            args[0] == "dependabot"
            and "update" in args
            and scenario
            in {
                "docker-mirror",
                "pypi-mirror",
            }
        ):
            job = yaml.safe_load(Path(args[args.index("--file") + 1]).read_text())
            expected_credentials = (
                [
                    {
                        "type": "python_index",
                        "index-url": "https://host.docker.internal:18947/simple/",
                        "replaces-base": True,
                    }
                ]
                if scenario == "pypi-mirror"
                else [
                    {
                        "type": "docker_registry",
                        "registry": "host.docker.internal:18947",
                        "replaces-base": True,
                    }
                ]
            )
            assert job["credentials"] == expected_credentials
        code, output = execute(args, **kwargs)
        if args[0] == "dependabot" and "update" in args:
            if code:
                (tmp_path / "synthetic-updater-failure.txt").write_text(output)
            events = [
                event
                for event in json_events(output)
                if event.get("type")
                in {
                    "update_dependency_list",
                    "create_pull_request",
                    "mark_as_processed",
                    "record_update_job_error",
                }
            ]
            captured.append(("dependabot", events))
        elif args[:2] == ["docker", "run"]:
            events = [
                event
                for event in json_events(output)
                if event.get("msg")
                in {
                    "packageFiles with updates",
                    "Repository finished",
                    "branches",
                }
            ]
            captured.append(("renovate", events))
        return code, output

    monkeypatch.setattr("gappy.scan.session.run", capture)
    server = subprocess.Popen(
        [sys.executable, str(FIXTURES / "registry.py"), "--cert", str(cert), "--key", str(key)]
        + (["--recent"] if scenario in {"cooldown-recent", "cooldown-exclude"} else []),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        import ssl

        context = ssl.create_default_context(cafile=str(cert))
        for _ in range(50):
            if server.poll() is not None:
                pytest.fail("Controlled registry could not start; port 18947 may be occupied.")
            try:
                with urllib.request.urlopen(
                    "https://127.0.0.1:18947/gappy-fixture", context=context, timeout=1
                ):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Controlled registry did not become ready.")
        resolve = socket.getaddrinfo

        def registry_address(host: str, *args: object, **kwargs: object) -> object:
            return resolve("127.0.0.1" if host == "host.docker.internal" else host, *args, **kwargs)

        # Docker resolves this hostname itself; the host inventory client needs loopback.
        monkeypatch.setattr(socket, "getaddrinfo", registry_address)
        result = CliRunner().invoke(
            main,
            [
                "full",
                "--no-mirror",
                "--repository-config",
                "--repository",
                repository,
                "--source",
                str(source),
                "--output-dir",
                str(tmp_path),
                "--registry-ca",
                str(cert),
                "--timeout-seconds",
                "120",
            ]
            + ([] if scenario == "cache-reuse" else ["--no-cache"])
            + (["--dependabot-workers", "1"] if scenario == "parallel" else [])
            + (
                [
                    "--helm-mirror",
                    "https://unreachable.example/charts=https://host.docker.internal:18947/charts",
                ]
                if scenario == "helm-mirror"
                else []
            )
            + (
                ["--docker-mirror", "https://host.docker.internal:18947"]
                if scenario == "docker-mirror"
                else []
            )
            + (
                ["--pypi-mirror", "https://host.docker.internal:18947/simple/"]
                if scenario == "pypi-mirror"
                else []
            ),
        )
        for index, (provider, events) in enumerate(captured):
            (tmp_path / f"{provider}-{scenario}-{index}.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n"
            )
        assert result.exit_code == (1 if scenario == "pypi-mirror" else 0), (
            result.output,
            result.exception,
        )
        left = Scan.model_validate_json((tmp_path / "dependabot.scan.json").read_text())
        right = Scan.model_validate_json((tmp_path / "renovate.scan.json").read_text())
        if scenario == "transitive":
            assert left.status == right.status == "complete", (left.warnings, right.warnings)
            directness = {item.dependency.package: item.directness for item in left.observations}
            assert directness["gappy-fixture"] == "direct"
            assert directness["gappy-test-indirect-leaf"] == "transitive"
            assert {item.dependency.package for item in right.observations} == {"gappy-fixture"}
            report = json.loads((tmp_path / "comparison.json").read_text())
            assert [row["package"] for row in report["dependencies"]] == ["gappy-fixture"]
            assert report["metrics"]["excludedObservations"]["dependabot"]["transitive"] == 1
            assert "gappy-test-indirect-leaf" not in (tmp_path / "comparison.html").read_text()
            return
        if scenario == "parallel":
            from gappy.contracts import timestamp

            assert left.status == right.status == "complete", (left.warnings, right.warnings)
            parallel = tmp_path / "parallel"
            parallel.mkdir()
            rerun = CliRunner().invoke(
                main,
                [
                    "full",
                    "--no-mirror",
                    "--repository-config",
                    "--repository",
                    repository,
                    "--source",
                    str(source),
                    "--output-dir",
                    str(parallel),
                    "--registry-ca",
                    str(cert),
                    "--timeout-seconds",
                    "120",
                    "--dependabot-workers",
                    "2",
                    "--no-cache",
                ],
            )
            assert rerun.exit_code == 0, (rerun.output, rerun.exception)
            concurrent = Scan.model_validate_json((parallel / "dependabot.scan.json").read_text())
            concurrent_right = Scan.model_validate_json(
                (parallel / "renovate.scan.json").read_text()
            )
            assert concurrent.status == concurrent_right.status == "complete"
            assert left.observations == concurrent.observations
            assert left.snapshot == concurrent.snapshot
            assert left.toolchain == concurrent.toolchain
            assert right.observations == concurrent_right.observations
            serial_seconds = (
                timestamp(left.finishedAt) - timestamp(left.startedAt)
            ).total_seconds()
            parallel_seconds = (
                timestamp(concurrent.finishedAt) - timestamp(concurrent.startedAt)
            ).total_seconds()
            print(f"Dependabot sequential={serial_seconds:.2f}s parallel={parallel_seconds:.2f}s")
            return
        if scenario == "pypi-mirror":
            assert left.status == "partial"
            assert any("unexpected_external_code" in warning for warning in left.warnings)
            assert right.status == "complete", right.warnings
            assert right.observations[0].candidates[0].targetVersion == "1.2.2"
            assert right.observations[0].dependency.registry == (
                "https://host.docker.internal:18947/simple"
            )
            assert left.snapshot == right.snapshot
            assert "gappy:pypi-mirror" in left.snapshot.configHashes
            assert (source / "requirements.txt").read_text() == "gappy-fixture==1.2.0\n"
            return
        assert left.status == "complete", left.warnings
        assert right.status == "complete", right.warnings
        assert left.snapshot == right.snapshot
        assert left.sessionId == right.sessionId
        has_update = scenario not in {
            "no-update",
            "ignore-patch",
            "disable-patch",
            "cooldown-recent",
        }
        assert bool(left.observations[0].candidates) == has_update, left.observations
        assert bool(right.observations[0].candidates) == has_update, right.observations
        if has_update:
            assert left.observations[0].candidates[0].targetVersion == "1.2.2"
            assert right.observations[0].candidates[0].targetVersion == "1.2.2"
        assert (
            source
            / (
                "Chart.yaml"
                if scenario in {"helm", "helm-mirror"}
                else "Dockerfile"
                if scenario == "docker-mirror"
                else "requirements.txt"
                if scenario == "pypi-mirror"
                else "package.json"
            )
        ).exists()
        assert not (tmp_path / "inventory.json").exists()
        count = 2 if scenario == "directories" else 1
        report = json.loads((tmp_path / "comparison.json").read_text())
        assert report["mode"] == "scanner-comparison"
        assert report["metrics"]["ecosystems"] == [
            "helm"
            if scenario in {"helm", "helm-mirror"}
            else "docker"
            if scenario == "docker-mirror"
            else "pip"
            if scenario == "pypi-mirror"
            else "npm"
        ]
        if scenario == "helm-mirror":
            assert "https://unreachable.example/charts" in (source / "Chart.yaml").read_text()
            assert "gappy:helm-mirror" in left.snapshot.configHashes
        assert report["metrics"]["dependencies"] == count
        for provider in ("dependabot", "renovate"):
            assert report["metrics"]["providers"][provider] == {
                "discovered": count,
                "withRecommendations": count if has_update else 0,
                "recommendedUpdates": count if has_update else 0,
            }
        assert (tmp_path / "comparison.html").is_file()
        if scenario == "no-update":
            html = (tmp_path / "comparison.html").read_text()
            assert (
                html.count('<span class="badge text-bg-success">No newer update recommended</span>')
                == 2
            )
        if scenario == "cache-reuse":
            first_volumes = {scan.provider: scan.cacheVolumes for scan in (left, right)}
            assert first_volumes["dependabot"] == first_volumes["renovate"]
            assert left.cacheMode == right.cacheMode == "persistent"
            for scan in (left, right):
                assert len(scan.cacheVolumes) == 1
                image = (
                    scan.toolchain["updaterImage"]
                    if scan.provider == "dependabot"
                    else scan.toolchain["image"]
                )
                # Check that the normal scanner user can write to the initialized cache.
                namespace = cache_namespace(
                    scan.provider, "npm" if scan.provider == "dependabot" else "all-managers", image
                )
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--mount",
                        f"type=volume,src={scan.cacheVolumes[0]},dst=/gappy-cache,"
                        f"volume-subpath={namespace}",
                        "--entrypoint",
                        "touch",
                        image,
                        "/gappy-cache/reuse-marker",
                    ],
                    check=True,
                    capture_output=True,
                )
            second = tmp_path / "second"
            second.mkdir()
            rerun = CliRunner().invoke(
                main,
                [
                    "full",
                    "--no-mirror",
                    "--repository-config",
                    "--repository",
                    repository + "-second",
                    "--source",
                    str(source),
                    "--output-dir",
                    str(second),
                    "--registry-ca",
                    str(cert),
                    "--timeout-seconds",
                    "120",
                ],
            )
            assert rerun.exit_code == 0, (rerun.output, rerun.exception)
            for provider in ("dependabot", "renovate"):
                scan = Scan.model_validate_json((second / f"{provider}.scan.json").read_text())
                assert scan.cacheMode == "persistent"
                assert scan.cacheVolumes == first_volumes[provider]
                image = (
                    scan.toolchain["image"]
                    if provider == "renovate"
                    else (scan.toolchain["updaterImage"])
                )
                namespace = cache_namespace(
                    provider, "npm" if provider == "dependabot" else "all-managers", image
                )
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network=none",
                        "--mount",
                        f"type=volume,src={scan.cacheVolumes[0]},dst=/gappy-cache,"
                        f"volume-subpath={namespace}",
                        "--entrypoint",
                        "test",
                        image,
                        "-f",
                        "/gappy-cache/reuse-marker",
                    ],
                    check=True,
                    capture_output=True,
                )
                if provider == "renovate":
                    files = subprocess.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network=none",
                            "--mount",
                            f"type=volume,src={scan.cacheVolumes[0]},dst=/gappy-cache,"
                            f"volume-subpath={cache_namespace(provider, 'all-managers', image)}",
                            "--entrypoint",
                            "find",
                            image,
                            "/gappy-cache/renovate",
                            "-type",
                            "f",
                            "-size",
                            "+0c",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    assert files.stdout.strip(), "Native Renovate did not persist cache files."
    finally:
        server.terminate()
        server.wait(timeout=10)
        cert.unlink(missing_ok=True)
        key.unlink(missing_ok=True)
        if scenario == "cache-reuse":
            from gappy.scan.cache import volume_name
            from gappy.scan.session import toolchain

            chain = toolchain()
            name = volume_name(repository, "renovate", "all-managers", chain["renovate"]["image"])
            subprocess.run(["docker", "volume", "rm", name], check=True, capture_output=True)
