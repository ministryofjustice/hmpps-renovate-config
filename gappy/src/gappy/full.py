"""Collect baseline and optimised scanner pairs for the full command."""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Event
from typing import Any

import click

from gappy.compare.configurations import compare_configurations
from gappy.compare.discovered import compare
from gappy.contracts import Provider, Scan
from gappy.forecast.config import load_config
from gappy.io import GappyError, dump, preflight, publish, read_json
from gappy.report.html import render
from gappy.scan.auth import ghcr_credentials, github_token
from gappy.scan.process import run as run_process
from gappy.scan.snapshot import checkout


def resolve_revision(source: Path, ref: str) -> str:
    if ref.startswith("-"):
        raise GappyError("Git ref must identify a revision.")
    code, revision = run_process(
        ["git", "-C", str(source), "rev-parse", "--verify", f"{ref}^{{commit}}"], timeout=30
    )
    if code:
        raise GappyError("Cannot resolve the shared Git revision before scanning.")
    return revision.strip()


def reusable_pair(
    directory: Path | None, label: str, repository: str, revision: str
) -> tuple[Scan, Scan] | None:
    if directory is None:
        click.echo(f"No previous run found; scanning {label} configurations too.")
        return None
    suffix = ".optimised" if label == "optimised" else ""
    paths = [directory / f"{provider}{suffix}.scan.json" for provider in ("dependabot", "renovate")]
    if not all(path.is_file() for path in paths):
        click.echo(f"Previous {label} exports are missing; scanning {label} configurations too.")
        return None
    try:
        left, right = (Scan.model_validate(read_json(path)) for path in paths)
        compare(left, right)
    except (GappyError, ValueError):
        raise GappyError(
            f"Previous {label} exports are invalid or inconsistent; "
            "choose another --reuse-from directory or omit the single-variant flag."
        ) from None
    if any(s.repository != repository or s.snapshot.revision != revision for s in (left, right)):
        click.echo(
            f"Previous {label} exports do not match the repository/revision; "
            f"scanning {label} configurations too."
        )
        return None
    if any(s.status != "complete" for s in (left, right)):
        click.echo(f"Previous {label} scans were incomplete; scanning {label} configurations too.")
        return None
    warning = (
        f"Reused {label} scan evidence from {directory.resolve()}; original timestamps and "
        "configuration hashes retained. This variant was not rescanned and may be older "
        "than the current configuration or registry state."
    )
    click.echo(warning)
    return (
        left.model_copy(update={"warnings": [*left.warnings, warning]}),
        right.model_copy(update={"warnings": [*right.warnings, warning]}),
    )


def run(
    ctx: click.Context,
    *,
    repository: str,
    output_dir: Path | None,
    config_dir: Path | None,
    repository_config: bool,
    optimised_config_dir: Path | None,
    verbose: bool,
    open_chrome: bool,
    selected_variant: str | None = None,
    reuse_from: Path | None = None,
    **options: Any,
) -> None:
    from gappy.cli import latest_run_directory, new_run_directory, open_in_chrome, scan

    baseline_dir = config_dir or Path.cwd()
    optimised_dir = optimised_config_dir or baseline_dir / "optimised"
    uses_default_mirrors = (
        options.get("npm_mirror") is None
        or options.get("pypi_mirror") is None
        or options.get("docker_mirror") is None
        or not options.get("helm_mirror")
    )
    if options.get("registry_ca") is None and options.get("mirror", True) and uses_default_mirrors:
        options["registry_ca"] = baseline_dir / ".gappy-mirror" / "ca.crt"
    source_ca = baseline_dir / ".gappy-mirror" / "source-ca.crt"
    if options.get("source_ca") is None and source_ca.is_file():
        options["source_ca"] = source_ca

    def validate_configs(labels: tuple[str, ...]) -> None:
        for label in labels:
            if label == "baseline" and repository_config:
                continue
            directory = baseline_dir if label == "baseline" else optimised_dir
            for provider, name in (("dependabot", "dependabot.yml"), ("renovate", "renovate.json")):
                typed: Provider = "dependabot" if provider == "dependabot" else "renovate"
                try:
                    load_config(directory / name, typed)
                except GappyError:
                    raise GappyError(
                        f"Both readable dependabot.yml and renovate.json are required in "
                        f"{directory}; check the selected baseline/optimised config directory."
                    ) from None

    try:
        github_token(options.get("github_token_env"))
        ghcr_credentials(options.get("ghcr_username"), options.get("ghcr_token_env"))
        labels = (selected_variant,) if selected_variant is not None else ("baseline", "optimised")
        validate_configs(labels)
        previous = (
            (reuse_from if reuse_from is not None else latest_run_directory(repository))
            if selected_variant is not None
            else None
        )
        directory = output_dir if output_dir is not None else new_run_directory(repository)
        paths = {
            label: tuple(
                directory / f"{provider}{suffix}.scan.json"
                for provider in ("dependabot", "renovate")
            )
            for label, suffix in (("baseline", ""), ("optimised", ".optimised"))
        }
        json_path, html_path = directory / "comparison.json", directory / "comparison.html"
        inputs = [
            p
            for p in (options.get("source"), options.get("registry_ca"), options.get("source_ca"))
            if p is not None
        ]
        inputs += [optimised_dir, baseline_dir]
        preflight([*paths["baseline"], *paths["optimised"], json_path, html_path], inputs)
        pairs: dict[str, tuple[Scan, Scan]] = {}
        cancel = Event()

        def collect_pair(label: str, source: Path, revision: str) -> tuple[Scan, Scan]:
            click.echo(f"Scanning {label} configurations.")
            left, right = paths[label]
            try:
                with click.Context(scan, parent=ctx) as worker_ctx:
                    worker_ctx.invoke(
                        scan,
                        repository=repository,
                        dependabot_json=left,
                        renovate_json=right,
                        config_dir=config_dir if label == "baseline" else optimised_dir,
                        repository_config=repository_config if label == "baseline" else False,
                        replace_alternate_configs=label == "optimised",
                        cache_scope=label,
                        cancel=cancel,
                        log_dir=directory / "logs" / label,
                        **{**options, "source": source, "ref": revision},
                    )
            except click.exceptions.Exit as exc:
                if exc.exit_code != 1:
                    cancel.set()
                    raise
                click.echo(f"{label.title()} scans were incomplete; retaining diagnostic evidence.")
            return (
                Scan.model_validate(read_json(left)),
                Scan.model_validate(read_json(right)),
            )

        with checkout(options.get("source"), repository) as source:
            revision = resolve_revision(source, options.get("ref", "HEAD"))
            if selected_variant is not None:
                other = "baseline" if selected_variant == "optimised" else "optimised"
                reused = reusable_pair(previous, other, repository, revision)
                if reused is None:
                    validate_configs((other,))
                    labels = ("baseline", "optimised")
                else:
                    pairs[other] = reused
                    reused_logs = previous / "logs" / other if previous is not None else None
                    if reused_logs is not None and reused_logs.is_dir():
                        shutil.copytree(reused_logs, directory / "logs" / other)
                    publish(
                        {
                            path: dump(scan.model_dump())
                            for path, scan in zip(paths[other], reused, strict=True)
                        }
                    )
            pool = ThreadPoolExecutor(max_workers=2)
            futures = {
                pool.submit(collect_pair, label, source, revision): label for label in labels
            }
            try:
                for future in as_completed(futures):
                    pairs[futures[future]] = future.result()
            except BaseException:
                cancel.set()
                raise
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
        report = compare_configurations(pairs["baseline"], pairs["optimised"])
        publish({json_path: dump(report), html_path: render(report, verbose=verbose)})
    except (GappyError, OSError, ValueError) as exc:
        raise click.ClickException(
            "Cannot complete baseline/optimised comparison: " + str(exc)
        ) from None
    click.echo(f"Comparison JSON: {json_path.resolve()}")
    click.echo(f"Comparison HTML: {html_path.resolve()}")
    if open_chrome:
        open_in_chrome(html_path)
    if any(s.status != "complete" for pair in pairs.values() for s in pair):
        raise click.exceptions.Exit(1)
