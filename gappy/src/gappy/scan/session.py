"""Docker-backed scan collection with conservative, explicit capability limits."""

import json
import os
import re
import shutil
import ssl
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import click
import yaml

from gappy.contracts import Candidate, Observation, Provider, Scan, Snapshot
from gappy.io import GappyError
from gappy.scan import native
from gappy.scan.auth import GhcrCredentials, redact
from gappy.scan.cache import (
    DOWNLOAD_ENV,
    TARGET,
    cache_namespace,
    prepare,
    proxy_cache_scope,
    scoped_ecosystem,
    staged,
)
from gappy.scan.dependabot import Job, jobs
from gappy.scan.ecosystems import DEPENDABOT
from gappy.scan.logs import RawLog, raw_log
from gappy.scan.mirror import (
    DEFAULT_DOCKER_MIRROR,
    DEFAULT_HELM_MIRROR,
    DEFAULT_NPM_MIRROR,
    DEFAULT_PYPI_MIRROR,
    apply_npm_mirror,
    apply_registry_mirrors,
    registry_credentials,
)
from gappy.scan.process import ProcessTimeout, run
from gappy.scan.snapshot import freeze

SOURCE_RETRY_DELAYS = (5, 10)


def now() -> str:
    return datetime.now(UTC).isoformat()


def environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "TMPDIR"}
    }


def toolchain() -> dict[str, Any]:
    value: dict[str, Any] = json.loads(files("gappy.scan").joinpath("toolchain.json").read_text())
    return value


def scanner_ca_bundle(ca: Path | None, source_ca: Path | None, destination: Path) -> Path | None:
    if source_ca is None:
        return ca
    certificates = []
    for path in (ca, source_ca):
        if path is None:
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                raise GappyError("Scanner CA file exceeds the 1 MiB certificate budget.")
            content = path.read_bytes()
            if b"PRIVATE KEY" in content:
                raise GappyError("Scanner CA files must contain public certificates, not keys.")
            if b"-----BEGIN CERTIFICATE-----" not in content:
                raise GappyError("Scanner CA file must contain PEM public certificates.")
            ssl.create_default_context(cadata=content.decode("ascii"))
        except (OSError, UnicodeError, ValueError, ssl.SSLError):
            raise GappyError(
                "Scanner CA file is unreadable or is not a valid PEM certificate bundle."
            ) from None
        certificates.append(content)
    destination.write_bytes(b"\n".join(certificates))
    return destination


def default_registry(root: Path) -> str | None:
    path = root / ".npmrc"
    if not path.exists():
        return "https://registry.npmjs.org"
    for line in path.read_text().splitlines():
        if line.strip().startswith("registry="):
            return native.registry_identity(line.split("=", 1)[1].strip())
    return None


@dataclass
class DependabotBatch:
    observations: list[Observation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    images: dict[str, str] = field(default_factory=dict)
    complete: bool = True
    cancelled: bool = False


def log_timeout(label: str, detail: str, *, manifest_known: bool = False) -> str:
    message = f"{label}: {detail}"
    if not manifest_known:
        message += " The specific dependency or manifest was not identified."
    click.echo(message, err=True)
    return message


def log_renovate_timeouts(
    output: str,
    github_token: str | None,
    reported: set[tuple[str | None, bool]],
    warnings: list[str],
) -> None:
    if "ETIMEDOUT" not in output:
        return
    for manifest, invalid in native.renovate_timeout_contexts(redact(output, github_token)):
        if (manifest, invalid) in reported:
            continue
        reported.add((manifest, invalid))
        if manifest is None:
            label = 'Renovate (repository directory "/")'
        else:
            label = f"Renovate (repository manifest {json.dumps(manifest)})"
        detail = native.RENOVATE_SOURCE_TIMEOUT + (
            " Scanner-reported manifest path was invalid and has been withheld." if invalid else ""
        )
        warnings.append(log_timeout(label, detail, manifest_known=manifest is not None))


def wait_for_source_retry(cancel: Event, delay: int) -> bool:
    return cancel.wait(delay)


def run_dependabot_job(
    args: list[str],
    source: Path,
    timeout: int,
    env: dict[str, str],
    cancel: Event,
    token: str | None,
    label: str,
    warnings: list[str],
    log: RawLog | None = None,
) -> tuple[int, str]:
    retried = False
    for attempt in range(len(SOURCE_RETRY_DELAYS) + 1):
        if cancel.is_set():
            raise KeyboardInterrupt
        # Native retries must start with the same source, not a prior attempt's changes.
        with tempfile.TemporaryDirectory(prefix="gappy-retry-") as directory:
            command = list(args)
            if attempt:
                local = Path(directory) / "source"
                shutil.copytree(source, local)
                command[command.index("--local") + 1] = str(local)
            if log is not None:
                log.note(f"{label}: attempt {attempt + 1}")
            code, output = run(
                command,
                timeout=timeout + 20,
                env=env,
                cancel=cancel,
                output_limit=None,
                raw_line=log.line if log is not None else None,
            )
            if log is not None:
                log.note(f"{label}: attempt {attempt + 1} exited with code {code}")
        output = redact(output, token)
        if not native.source_timed_out(output):
            if retried:
                message = (
                    f"{label}: source timeout cleared on attempt {attempt + 1}; "
                    "validating final scanner evidence."
                )
                click.echo(message, err=True)
                warnings.append(message)
            return code, output
        if attempt == len(SOURCE_RETRY_DELAYS):
            message = (
                f"{label}: source timed out after {attempt + 1} attempts; "
                "retry limit reached. Results remain incomplete."
            )
            click.echo(message, err=True)
            warnings.append(message)
            return code, output
        delay = SOURCE_RETRY_DELAYS[attempt]
        message = (
            f"{label}: dependency source timed out (private_source_timed_out). "
            f"Pausing {delay}s before attempt {attempt + 2}/{len(SOURCE_RETRY_DELAYS) + 1}. "
            "Press Ctrl+C to cancel."
        )
        click.echo(message, err=True)
        warnings.append(message)
        retried = True
        if wait_for_source_retry(cancel, delay):
            click.echo(f"{label}: source-timeout retry cancelled.", err=True)
            raise KeyboardInterrupt
        click.echo(f"{label}: retrying dependency source now.", err=True)
    raise AssertionError("Source retry loop did not return.")


def dependabot_cooldown_probe(
    job: Job,
    index: int,
    path: Path,
    write_job: Callable[[Path, dict[str, Any]], None],
    invoke: Callable[[Path, str, str], tuple[int, str]],
    label: str,
    github_token: str | None,
    ghcr: GhcrCredentials | None,
    local: Path,
    warnings: list[str],
) -> dict[tuple[str, str, str], list[Candidate]] | None:
    """Rerun a cooldown-filtered job without cooldown to name the withheld target versions."""
    probe_label = f"{label} without cooldown"
    write_job(path, {key: value for key, value in job.definition.items() if key != "cooldown"})
    code, output = invoke(
        path, probe_label, f"dependabot-{index + 1:02d}-{job.ecosystem}-without-cooldown"
    )
    output = redact(output, github_token)
    if ghcr is not None:
        output = ghcr.redact(output)
    try:
        observations, complete = native.dependabot(
            output,
            job.directory,
            default_registry(local / job.directory.lstrip("/")) if job.ecosystem == "npm" else None,
            evidence_prefix=f"dependabot-{index}-cooldown",
            ecosystem=job.ecosystem,
        )
    except GappyError as exc:
        complete, observations = False, []
        warnings.append(f"{probe_label}: {exc}")
    if code or not complete:
        warnings.append(
            f"{probe_label}: comparison run was incomplete; cooldown-withheld versions "
            "remain unknown."
        )
        return None
    warnings.append(
        f"{label}: native logs reported cooldown filtering; reran the same job without cooldown "
        "to record withheld versions as deferred recommendations (no proposals are created)."
    )
    return {
        (
            item.dependency.package,
            item.dependency.manifestPath,
            item.dependency.installedVersion,
        ): item.candidates
        for item in observations
        if item.candidates
    }


def dependabot_batch(
    planned: list[tuple[int, Job]],
    source: Path,
    repository: str,
    timeout: int,
    spec: dict[str, str],
    ca: Path | None,
    cache: bool,
    pypi_mirror: str | None,
    docker_mirror: str | None,
    cancel: Event,
    github_token: str | None = None,
    proposal_sink: Callable[[str, str, str, str], None] | None = None,
    cache_scope: str | None = None,
    ghcr: GhcrCredentials | None = None,
    log_dir: Path | None = None,
) -> DependabotBatch:
    """Run one ecosystem sequentially so cache staging never overlaps its namespace."""
    result = DependabotBatch()
    spec = dict(spec)
    runtime_env = environment()
    if github_token is not None:
        # CLI 1.94.0 injects this as git_source credentials into its proxy, not the updater.
        runtime_env["LOCAL_GITHUB_ACCESS_TOKEN"] = github_token
    with tempfile.TemporaryDirectory(prefix="gappy-dependabot-") as directory:
        local = Path(directory) / "source"
        shutil.copytree(source, local)
        for index, job in planned:
            if cancel.is_set():
                result.cancelled = True
                break
            display_directory = json.dumps(redact(job.directory, github_token))
            label = (
                f"Dependabot {job.ecosystem} job {index + 1} "
                f"(repository directory {display_directory})"
            )
            started = monotonic()
            try:
                if job.ecosystem == "npm":
                    result.warnings.append(
                        f"Dependabot npm job {index + 1}: native alias resolution enabled "
                        "(dealias-packages); registry lookups use actual package names, "
                        "not npm installation aliases."
                    )
                job_path = Path(directory) / f"job-{index}.yml"
                credentials = registry_credentials(
                    job.ecosystem, pypi=pypi_mirror, docker=docker_mirror
                )
                if ghcr is not None and job.ecosystem in {"docker", "docker-compose", "helm"}:
                    credentials.append(
                        {
                            "type": "docker_registry",
                            "registry": "ghcr.io",
                            "username": ghcr.username,
                            "password": ghcr.password,
                        }
                    )

                def write_job(
                    path: Path,
                    definition: dict[str, Any],
                    credentials: list[dict[str, object]] = credentials,
                ) -> None:
                    path.touch(mode=0o600)
                    path.write_text(
                        yaml.safe_dump(
                            {
                                "job": definition,
                                **({"credentials": credentials} if credentials else {}),
                            }
                        )
                    )

                write_job(job_path, job.definition)
                image = spec.get("updaterImage:" + job.ecosystem)
                if image is None:
                    if job.ecosystem == "npm":
                        image = spec["updaterImage"]
                    else:
                        image_name = (
                            "ghcr.io/dependabot/dependabot-updater-" + DEPENDABOT[job.ecosystem][1]
                        )
                        digest_code, digest = run(
                            [
                                "docker",
                                "buildx",
                                "imagetools",
                                "inspect",
                                image_name + ":latest",
                                "--format",
                                "{{.Manifest.Digest}}",
                            ],
                            timeout=timeout,
                            env=environment(),
                            cancel=cancel,
                        )
                        if digest_code or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest.strip()):
                            raise GappyError("Cannot resolve official updater image digest.")
                        image = image_name + "@" + digest.strip()
                        result.warnings.append(
                            f"{job.ecosystem}: updater digest resolved at scan time; "
                            "this ecosystem's native format has not been fully certified."
                        )
                    spec["updaterImage:" + job.ecosystem] = image
                result.images["updaterImage:" + job.ecosystem] = image
                args = [
                    "dependabot",
                    "--updater-image",
                    image,
                    "--proxy-image",
                    spec["proxyImage"],
                    "update",
                    "--file",
                    str(job_path),
                    "--local",
                    str(local),
                    "--timeout",
                    f"{timeout}s",
                ]
                if ca is not None:
                    args += ["--proxy-cert", str(ca)]
                staging: tuple[str, str, str, str] | None = None
                if cache:
                    cache_ecosystem = scoped_ecosystem(repository, job.ecosystem, cache_scope)
                    volume = prepare(
                        repository, "dependabot", cache_ecosystem, image, environment(), timeout
                    )
                    proxy_scope = proxy_cache_scope(repository, cache_ecosystem, spec["proxyImage"])
                    prepare(repository, "dependabot", proxy_scope, image, environment(), timeout)
                    if volume not in result.volumes:
                        result.volumes.append(volume)
                    for key, value in DOWNLOAD_ENV.items():
                        if key != "XDG_CACHE_HOME":
                            args += ["--updater-env", f"{key}={value}"]
                    staging = (
                        volume,
                        image,
                        cache_namespace("dependabot", cache_ecosystem, image),
                        cache_namespace("dependabot", proxy_scope, image),
                    )

                def invoke(
                    path: Path,
                    run_label: str,
                    log_name: str,
                    args: list[str] = args,
                    staging: tuple[str, str, str, str] | None = staging,
                ) -> tuple[int, str]:
                    command = list(args)
                    command[command.index("--file") + 1] = str(path)
                    with raw_log(log_dir, log_name, github_token, ghcr) as log:
                        if staging is None:
                            return run_dependabot_job(
                                command,
                                source,
                                timeout,
                                runtime_env,
                                cancel,
                                github_token,
                                run_label,
                                result.warnings,
                                log,
                            )
                        return run_staged(command, run_label, log, staging)

                def run_staged(
                    command: list[str],
                    run_label: str,
                    log: RawLog | None,
                    staging: tuple[str, str, str, str],
                ) -> tuple[int, str]:
                    volume, staged_image, namespace, proxy_namespace = staging
                    with (
                        staged(
                            volume, staged_image, environment(), timeout, namespace=namespace
                        ) as cache_path,
                        staged(
                            volume, staged_image, environment(), timeout, namespace=proxy_namespace
                        ) as proxy_cache_path,
                    ):
                        command += ["--volume", f"{cache_path}:{TARGET}"]
                        command += ["--cache", str(proxy_cache_path)]
                        return run_dependabot_job(
                            command,
                            source,
                            timeout,
                            runtime_env,
                            cancel,
                            github_token,
                            run_label,
                            result.warnings,
                            log,
                        )

                job_code, output = invoke(
                    job_path, label, f"dependabot-{index + 1:02d}-{job.ecosystem}"
                )
                output = redact(output, github_token)
                if ghcr is not None:
                    output = ghcr.redact(output)
                launcher_timeout = native.dependabot_launcher_timeout(output)
                for message in native.diagnostics("dependabot", output):
                    if message == native.DEPENDABOT_SOURCE_TIMEOUT or message == launcher_timeout:
                        result.warnings.append(log_timeout(label, message))
                    else:
                        result.warnings.append(f"{label}: {message}")
                if proposal_sink is not None:
                    proposal_sink("dependabot", job.ecosystem, job.directory, output)
                extracted, complete = native.dependabot(
                    output,
                    job.directory,
                    default_registry(local / job.directory.lstrip("/"))
                    if job.ecosystem == "npm"
                    else None,
                    evidence_prefix=f"dependabot-{index}",
                    ecosystem=job.ecosystem,
                )
                if job_code:
                    extracted = [
                        item.model_copy(update={"lookupComplete": False}) for item in extracted
                    ]
                cooldown_probe = None
                if (
                    "cooldown" in job.definition
                    and not job_code
                    and complete
                    and any(not item.candidates for item in extracted)
                    and native.dependabot_cooldown_filtered(output)
                ):
                    cooldown_probe = dependabot_cooldown_probe(
                        job,
                        index,
                        Path(directory) / f"job-{index}-without-cooldown.yml",
                        write_job,
                        invoke,
                        label,
                        github_token,
                        ghcr,
                        local,
                        result.warnings,
                    )
                result.observations.extend(
                    job.policy(
                        extracted,
                        docker_no_updates=native.dependabot_docker_no_updates(output)
                        if job.ecosystem in {"docker", "docker-compose"}
                        else None,
                        cooldown_probe=cooldown_probe,
                    )
                )
                if job_code or not complete:
                    result.complete = False
                    result.warnings.append(
                        f"Dependabot {job.ecosystem} job {index + 1} has incomplete evidence."
                    )
            except GappyError as exc:
                result.complete = False
                result.warnings.append(
                    log_timeout(label, str(exc))
                    if isinstance(exc, ProcessTimeout)
                    else f"{label}: {exc}"
                )
            except MemoryError:
                result.complete = False
                result.warnings.append(
                    f"Dependabot {job.ecosystem} job {index + 1}: insufficient memory while "
                    "processing scanner evidence; reduce scan scope or add memory."
                )
            except KeyboardInterrupt:
                cancel.set()
                result.cancelled = True
                break
            finally:
                result.warnings.append(
                    f"Dependabot {job.ecosystem} job {index + 1} elapsed: "
                    f"{monotonic() - started:.2f}s (including image/cache preparation)."
                )
    return result


def execute(
    provider: Provider,
    source: Path,
    snapshot: Snapshot,
    session: str,
    repository: str,
    timeout: int,
    chain: dict[str, Any],
    ca: Path | None,
    *,
    cache: bool = False,
    pypi_mirror: str | None = None,
    docker_mirror: str | None = None,
    dependabot_workers: int = 2,
    github_token: str | None = None,
    ghcr: GhcrCredentials | None = None,
    renovate_output_limit_mib: int | None = None,
    proposal_sink: Callable[[str, str, str, str], None] | None = None,
    cache_scope: str | None = None,
    cancel: Event | None = None,
    log_dir: Path | None = None,
) -> Scan:
    if renovate_output_limit_mib is not None and not 1 <= renovate_output_limit_mib <= 256:
        raise GappyError("Renovate output budget must be between 1 and 256 MiB.")
    started = now()
    warnings = [
        "Discovery and recommended versions come from native configured-manager evidence.",
        "Existing schedules are not replayed; this is an explicitly invoked lookup.",
        "Native observation interval uses scan start/finish bounds, not an exact lookup time.",
        "Safety overrides: reject external code in Dependabot; disable Renovate scripts/plugins.",
    ]
    if github_token is not None:
        warnings.append(
            "Explicit GitHub authentication enabled for github.com dependency/source lookups; "
            "credentials are not included in scan exports or snapshot hashes."
        )
    if ghcr is not None:
        warnings.append(
            "Explicit private GHCR authentication enabled for ghcr.io image lookups; "
            "credentials are not included in snapshots or exports."
        )
    observations: list[Observation] = []
    status = "failed"
    spec: dict[str, str] = chain[provider]
    spec = dict(spec)
    container_name = f"gappy-{session}-{provider}"
    cache_volumes: list[str] = []
    if cache:
        if cache_scope is not None:
            warnings.append(
                f"Persistent cache namespace isolated by repository and variant: {cache_scope}."
            )
        warnings.append(
            "Persistent download/registry caches enabled; native expiry applies. "
            "Cached observations are not equivalent to fresh-cache scans. "
            "Use --no-cache for isolated fresh runs."
        )
    try:
        if cancel is not None and cancel.is_set():
            raise KeyboardInterrupt
        if provider == "dependabot":
            warnings.append(
                "Dependabot native output has no fixed byte limit and may use substantial memory."
            )
            config = yaml.safe_load((source / ".github/dependabot.yml").read_text())
            planned, diagnostics = jobs(config, snapshot, repository)
            warnings.extend(diagnostics)
            if config.get("multi-ecosystem-groups"):
                warnings.append(
                    "Dependabot multi-ecosystem groups use separate native ecosystem/directory "
                    "lookups with group schedules and dependency patterns. Cross-ecosystem PR "
                    "consolidation, labels, assignees and shared PR capacity are not simulated."
                )
            if not planned:
                status = "unsupported"
                raise GappyError("No configured Dependabot entries have a supported adapter.")
            complete = not diagnostics
            code = 0
            if not 1 <= dependabot_workers <= 16:
                raise GappyError("Dependabot workers must be between 1 and 16.")
            groups: dict[str, list[tuple[int, Job]]] = {}
            for index, job in enumerate(planned):
                groups.setdefault(job.ecosystem, []).append((index, job))
            warnings.append(
                f"Dependabot concurrency: up to {dependabot_workers} ecosystems; "
                "jobs in the same ecosystem run sequentially."
            )
            cancel = cancel if cancel is not None else Event()
            pool = ThreadPoolExecutor(max_workers=min(dependabot_workers, len(groups)))
            futures = []
            interrupted = False
            try:
                for group in groups.values():
                    futures.append(
                        pool.submit(
                            dependabot_batch,
                            group,
                            source,
                            repository,
                            timeout,
                            spec,
                            ca,
                            cache,
                            pypi_mirror,
                            docker_mirror,
                            cancel,
                            github_token,
                            proposal_sink,
                            cache_scope,
                            ghcr,
                            log_dir,
                        )
                    )
                # Merge in configured ecosystem order, not completion order.
                for future in futures:
                    future.result()
            except BaseException:
                cancel.set()
                interrupted = True
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=interrupted)
                for future in futures:
                    if future.cancelled() or future.exception() is not None:
                        continue
                    batch = future.result()
                    observations.extend(batch.observations)
                    warnings.extend(batch.warnings)
                    spec.update(batch.images)
                    for volume in batch.volumes:
                        if volume not in cache_volumes:
                            cache_volumes.append(volume)
                    complete = complete and batch.complete
                    interrupted = interrupted or batch.cancelled
            if interrupted:
                raise KeyboardInterrupt
        else:
            warnings.append(
                "Renovate retained-output budget: no fixed byte limit; large evidence may use "
                "substantial memory. Set --renovate-output-limit-mib (1-256) for a safety limit."
                if renovate_output_limit_mib is None
                else f"Renovate retained-output budget: {renovate_output_limit_mib} MiB; "
                "this is a byte safety budget, not a dependency-count limit. "
                "Adjust with --renovate-output-limit-mib (1-256)."
            )
            config = json.loads((source / "renovate.json").read_text())
            if not isinstance(config, dict):
                raise GappyError("Renovate configuration must be an object.")
            if config.get("postUpgradeTasks") or config.get("allowedCommands"):
                status = "unsupported"
                raise GappyError("Repository custom commands are not supported.")
            args = [
                "docker",
                "run",
                "--rm",
                "--name",
                container_name,
                "--mount",
                f"type=bind,src={source},dst=/workspace,readonly",
                "--workdir",
                "/workspace",
                "--env",
                "LOG_FORMAT=json",
                "--env",
                "LOG_LEVEL=trace",
                "--env",
                "RENOVATE_ALLOW_SCRIPTS=false",
                "--env",
                "RENOVATE_ALLOW_PLUGINS=false",
                "--env",
                "RENOVATE_BASE_DIR=/tmp/gappy",
                "--env",
                f"RENOVATE_CACHE_DIR={TARGET}/renovate"
                if cache
                else "RENOVATE_CACHE_DIR=/tmp/cache",
            ]
            if ca is not None:
                args += [
                    "--mount",
                    f"type=bind,src={ca},dst=/tmp/gappy-ca.pem,readonly",
                    "--env",
                    "NODE_EXTRA_CA_CERTS=/tmp/gappy-ca.pem",
                ]
            if cache:
                cache_ecosystem = scoped_ecosystem(repository, "all-managers", cache_scope)
                volume = prepare(
                    repository, provider, cache_ecosystem, spec["image"], environment(), timeout
                )
                cache_volumes.append(volume)
                args += [
                    "--mount",
                    f"type=volume,src={volume},dst={TARGET},"
                    f"volume-subpath={cache_namespace(provider, cache_ecosystem, spec['image'])}",
                ]
                for key, value in DOWNLOAD_ENV.items():
                    args += ["--env", f"{key}={value}"]
            args += [spec["image"], "--platform=local", "--dry-run=lookup"]
            runtime_env = environment()
            if github_token is not None:
                # Inherit by name so the token never appears in Docker command arguments.
                args[2:2] = ["--env", "RENOVATE_GITHUB_COM_TOKEN"]
                runtime_env["RENOVATE_GITHUB_COM_TOKEN"] = github_token
            if ghcr is not None:
                args[2:2] = ["--env", "RENOVATE_HOST_RULES"]
                runtime_env["RENOVATE_HOST_RULES"] = ghcr.host_rules()
            reported_timeouts: set[tuple[str | None, bool]] = set()

            def capture_renovate_line(line: str) -> bool:
                retained = native.capture_renovate_line(line)
                if retained:
                    if ghcr is not None:
                        line = ghcr.redact(line)
                    log_renovate_timeouts(line, github_token, reported_timeouts, warnings)
                return retained

            with raw_log(log_dir, "renovate", github_token, ghcr) as log:
                if log is not None:
                    log.note('Renovate (repository directory "/"): native lookup')
                code, output = run(
                    args,
                    timeout=timeout,
                    env=runtime_env,
                    capture_line=capture_renovate_line,
                    output_limit=(
                        renovate_output_limit_mib * 1024 * 1024
                        if renovate_output_limit_mib is not None
                        else None
                    ),
                    cancel=cancel,
                    raw_line=log.line if log is not None else None,
                )
                if log is not None:
                    log.note(f"Renovate exited with code {code}")
            output = redact(output, github_token)
            if ghcr is not None:
                output = ghcr.redact(output)
            for message in native.diagnostics("renovate", output):
                if message != native.RENOVATE_SOURCE_TIMEOUT:
                    warnings.append(message)
                    continue
                log_renovate_timeouts(output, github_token, reported_timeouts, warnings)
            if proposal_sink is not None:
                proposal_sink("renovate", "all-managers", "/", output)
            observations, complete = native.renovate(
                output,
                configuration=config,
                registry=default_registry(source),
                require_branches=True,
                policy_filtered=bool(
                    config.get("extends")
                    or config.get("minimumReleaseAge")
                    or any(
                        isinstance(rule, dict)
                        and set(rule)
                        - {
                            "matchPackageNames",
                            "registryUrls",
                            "groupName",
                            "groupSlug",
                            "matchManagers",
                            "rangeStrategy",
                            "description",
                            "labels",
                            "addLabels",
                        }
                        for rule in config.get("packageRules", [])
                    )
                ),
            )
            warnings.append(
                "Renovate resolves local configuration and presets natively; filtered absence "
                "without an explicit policy outcome remains unknown."
            )
        if code != 0:
            observations = [
                item.model_copy(update={"lookupComplete": False}) for item in observations
            ]
            raise GappyError(
                "Native scanner failed; see the redacted raw log in the results logs directory."
                if log_dir is not None
                else "Native scanner failed; raw output is withheld to protect secrets."
            )
        status = "complete" if complete else "partial"
        if not complete:
            warnings.append("Native extraction or lookup was incomplete.")
    except (GappyError, ValueError, yaml.YAMLError) as exc:
        if isinstance(exc, ProcessTimeout):
            warnings.append(log_timeout(f'{provider.title()} (repository directory "/")', str(exc)))
        else:
            warnings.append(
                str(exc) if isinstance(exc, GappyError) else "Invalid native/config input."
            )
    except MemoryError:
        warnings.append(
            "Insufficient memory while processing scanner evidence; "
            "reduce scan scope or add memory."
        )
    except KeyboardInterrupt:
        status = "cancelled"
        warnings.append("Scan was interrupted.")
    finally:
        if provider == "renovate":
            try:
                inspect_code, containers = run(
                    [
                        "docker",
                        "container",
                        "ls",
                        "-a",
                        "--filter",
                        f"name=^/{container_name}$",
                        "--format",
                        "{{.ID}}",
                    ],
                    timeout=15,
                    env=environment(),
                )
                if inspect_code:
                    raise GappyError("Cannot verify owned scanner container cleanup.")
                if containers.strip():
                    cleanup_code, _ = run(
                        ["docker", "rm", "--force", container_name],
                        timeout=15,
                        env=environment(),
                    )
                    if cleanup_code:
                        raise GappyError("Owned scanner container cleanup failed.")
            except GappyError as exc:
                warnings.append(
                    log_timeout('Renovate cleanup (repository directory "/")', str(exc))
                    if isinstance(exc, ProcessTimeout)
                    else str(exc)
                )
                status = "partial" if status == "complete" else status
    return Scan.model_validate(
        {
            "schemaVersion": "1.1.0",
            "provider": provider,
            "sessionId": session,
            "repository": repository,
            "snapshot": snapshot.model_dump(),
            "toolchain": spec,
            "startedAt": started,
            "finishedAt": now(),
            "cacheMode": "persistent" if cache else "fresh",
            "cacheVolumes": cache_volumes,
            "status": status,
            "observations": [item.model_dump() for item in native.merge_observations(observations)],
            "warnings": warnings,
        }
    )


def scan(
    source: Path,
    ref: str,
    repository: str,
    timeout: int,
    ca: Path | None = None,
    *,
    before_scan: Callable[[Path, Snapshot], None] | None = None,
    cache: bool = True,
    npm_mirror: str | None = None,
    config_dir: Path | None = None,
    pypi_mirror: str | None = None,
    docker_mirror: str | None = None,
    helm_mirror: tuple[str, ...] = (),
    dependabot_workers: int = 2,
    github_token: str | None = None,
    ghcr: GhcrCredentials | None = None,
    renovate_output_limit_mib: int | None = None,
    mirror: bool = True,
    source_ca: Path | None = None,
    proposal_sink: Callable[[str, str, str, str], None] | None = None,
    replace_alternate_configs: bool = False,
    cache_scope: str | None = None,
    cancel: Event | None = None,
    log_dir: Path | None = None,
) -> tuple[Scan, Scan]:
    if renovate_output_limit_mib is not None and not 1 <= renovate_output_limit_mib <= 256:
        raise GappyError("Renovate output budget must be between 1 and 256 MiB.")
    if not 1 <= dependabot_workers <= 16:
        raise GappyError("Dependabot workers must be between 1 and 16.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise GappyError("Repository must have owner/name format.")
    if mirror:
        uses_defaults = (
            npm_mirror is None or pypi_mirror is None or docker_mirror is None or not helm_mirror
        )
        npm_mirror = npm_mirror if npm_mirror is not None else DEFAULT_NPM_MIRROR
        pypi_mirror = pypi_mirror if pypi_mirror is not None else DEFAULT_PYPI_MIRROR
        docker_mirror = docker_mirror if docker_mirror is not None else DEFAULT_DOCKER_MIRROR
        helm_mirror = helm_mirror or (DEFAULT_HELM_MIRROR,)
        if uses_defaults and ca is None:
            ca = (config_dir or Path.cwd()) / ".gappy-mirror" / "ca.crt"
            if not ca.is_file():
                raise GappyError(
                    "Default Nexus mirrors require the gateway CA at "
                    f"{ca}. Start/provision the bundled Nexus services and export the CA, "
                    "supply --registry-ca, or use --no-mirror for direct upstream access. "
                    "Gappy will not silently bypass mirrors or disable TLS verification."
                )
    if ca is not None and not ca.is_file():
        raise GappyError("Registry CA certificate is unreadable.")
    if source_ca is None:
        configured_source_ca = (config_dir or Path.cwd()) / ".gappy-mirror" / "source-ca.crt"
        if configured_source_ca.exists():
            source_ca = configured_source_ca
    chain = toolchain()
    for executable in ("docker", "dependabot", "git"):
        if shutil.which(executable) is None:
            raise GappyError(f"Required executable {executable} is missing from PATH.")
    code, _ = run(["docker", "info", "--format", "{{.OSType}}"], env=environment())
    if code:
        raise GappyError("Docker daemon is unavailable.")
    code, version = run(["dependabot", "--version"], env=environment())
    if code or f"version {chain['dependabot']['launcherVersion']}" not in version:
        raise GappyError("Installed Dependabot launcher version differs from the tested version.")
    session = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="gappy-") as directory:
        root = Path(directory)
        ca = scanner_ca_bundle(ca, source_ca, root / "trusted-ca.pem")
        frozen = root / "frozen"
        frozen.mkdir()
        snapshot = freeze(
            source,
            ref,
            frozen,
            config_dir=config_dir,
            replace_alternate_configs=replace_alternate_configs,
        )
        if npm_mirror is not None:
            snapshot = apply_npm_mirror(frozen, snapshot, npm_mirror)
        if pypi_mirror is not None or docker_mirror is not None or helm_mirror:
            snapshot = apply_registry_mirrors(
                frozen, snapshot, pypi=pypi_mirror, docker=docker_mirror, helm=helm_mirror
            )
        if before_scan is not None:
            before_scan(frozen, snapshot)
        left, right = root / "dependabot", root / "renovate"
        shutil.copytree(frozen, left)
        shutil.copytree(frozen, right)
        dependabot = execute(
            "dependabot",
            left,
            snapshot,
            session,
            repository,
            timeout,
            chain,
            ca,
            cache=cache,
            pypi_mirror=pypi_mirror.rstrip("/") + "/" if pypi_mirror is not None else None,
            docker_mirror=docker_mirror,
            dependabot_workers=dependabot_workers,
            github_token=github_token,
            ghcr=ghcr,
            proposal_sink=proposal_sink,
            log_dir=log_dir,
            cache_scope=cache_scope,
            cancel=cancel,
        )
        if dependabot.status == "cancelled":
            right_scan = Scan.model_validate(
                {
                    **dependabot.model_dump(),
                    "provider": "renovate",
                    "toolchain": chain["renovate"],
                    "observations": [],
                }
            )
        else:
            right_scan = execute(
                "renovate",
                right,
                snapshot,
                session,
                repository,
                timeout,
                chain,
                ca,
                cache=cache,
                github_token=github_token,
                ghcr=ghcr,
                renovate_output_limit_mib=renovate_output_limit_mib,
                proposal_sink=proposal_sink,
                log_dir=log_dir,
                cache_scope=cache_scope,
                cancel=cancel,
            )
        for result in (dependabot, right_scan):
            result.warnings.append(
                'Disposable snapshot preparation adds "private": true to package.json '
                "files without a private property; explicit values are preserved. "
                "This suppresses Dependabot published-library detection for those packages."
            )
            if source_ca is not None:
                result.warnings.append(
                    "Additional source CA trust enabled for both scanners, combined with "
                    "the registry/gateway CA when selected. TLS verification remains enabled."
                )
            result.warnings.append(
                "Bundled Nexus mirror defaults enabled for npm, PyPI, Docker Hub and the "
                "HMPPS Helm upstream; explicit mirror options override those defaults. "
                "Other registries/ecosystems retain native routing and download caches."
                if mirror
                else "Bundled Nexus mirror defaults disabled; only explicit mirrors are applied."
            )
        if config_dir is not None:
            for result in (dependabot, right_scan):
                result.warnings.append(
                    f"Scanner configurations supplied from {config_dir.resolve()}; "
                    "target repository configurations replaced in the disposable snapshot."
                )
        if npm_mirror is not None:
            for result in (dependabot, right_scan):
                result.warnings.append(
                    "npm mirror transport override applied to the disposable snapshot. "
                    "Registry freshness and availability may differ from npmjs.org."
                )
        if pypi_mirror is not None or docker_mirror is not None or helm_mirror:
            for result in (dependabot, right_scan):
                result.warnings.append(
                    "Registry mirror overrides applied: PyPI base index for pip, "
                    "Docker Hub base registry, and/or mapped Helm HTTP repositories. "
                    "Registry freshness and availability may differ from upstream. "
                    "Explicit Docker registries, Python source URLs and Helm OCI sources "
                    "are not universally redirected."
                )
        return dependabot, right_scan
