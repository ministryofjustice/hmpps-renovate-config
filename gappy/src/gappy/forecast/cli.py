"""Forecast collection and credential-free replay commands."""

from pathlib import Path

import click
from pydantic import ValidationError

from gappy.forecast.engine import forecast as calculate
from gappy.forecast.models import ForecastInput, Window
from gappy.forecast.report import render
from gappy.io import GappyError, dump, preflight, publish, read_json


@click.command("forecast")
@click.option("--repository", required=True, help="GitHub repository as owner/name.")
@click.option("--source", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--ref", default="HEAD", show_default=True)
@click.option("--window-start", required=True, help="Timezone-aware start timestamp.")
@click.option("--window-end", required=True, help="Timezone-aware exclusive end timestamp.")
@click.option(
    "--invocation-minutes",
    default=60,
    type=click.IntRange(1, 1440),
    show_default=True,
    help="Declared bot invocation cadence; not a claim about GitHub service timing.",
)
@click.option(
    "--fallback-config-dir",
    required=True,
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Gappy directory with fallback dependabot.yml and renovate.json.",
)
@click.option(
    "--optimised-config-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Complete optimised configs; defaults to FALLBACK/optimised.",
)
@click.option(
    "--github-token-env", help="Explicit environment variable containing a read-only token."
)
@click.option("--timeout-seconds", default=180, type=click.IntRange(1, 3600), show_default=True)
@click.option(
    "--dependabot-workers",
    default=2,
    type=click.IntRange(1, 16),
    show_default=True,
    help="Maximum concurrent Dependabot ecosystems in each configuration evaluation.",
)
@click.option(
    "--renovate-output-limit-mib",
    type=click.IntRange(1, 256),
    help="Optional retained Renovate evidence limit; default has no fixed byte ceiling.",
)
@click.option("--open-chrome", is_flag=True, help="Open the published forecast in Chrome.")
@click.option("--verbose", is_flag=True, help="Show all dependency results in the HTML report.")
@click.option("--ghcr-username", help="GitHub account for private GHCR image lookups.")
@click.option(
    "--ghcr-token-env",
    metavar="NAME",
    help="Environment variable containing a GHCR read:packages token.",
)
@click.option("--cache/--no-cache", default=True, show_default=True)
@click.option("--mirror/--no-mirror", default=True, show_default=True)
@click.option("--npm-mirror")
@click.option("--pypi-mirror")
@click.option("--docker-mirror")
@click.option("--helm-mirror", multiple=True)
@click.option("--registry-ca", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--source-ca", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--output-dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
def forecast_command(
    repository: str,
    source: Path | None,
    ref: str,
    window_start: str,
    window_end: str,
    invocation_minutes: int,
    fallback_config_dir: Path,
    optimised_config_dir: Path | None,
    github_token_env: str | None,
    timeout_seconds: int,
    cache: bool,
    mirror: bool,
    registry_ca: Path | None,
    source_ca: Path | None,
    output_dir: Path | None,
    npm_mirror: str | None,
    pypi_mirror: str | None,
    docker_mirror: str | None,
    helm_mirror: tuple[str, ...],
    dependabot_workers: int,
    renovate_output_limit_mib: int | None,
    open_chrome: bool,
    verbose: bool = False,
    ghcr_username: str | None = None,
    ghcr_token_env: str | None = None,
) -> None:
    """Compare baseline/optimised configs for both scanners without creating real PRs."""
    from gappy.cli import new_run_directory, validate_repository
    from gappy.forecast.collect import collect
    from gappy.scan.auth import ghcr_credentials, github_token
    from gappy.scan.snapshot import checkout

    validate_repository(repository)
    try:
        window = Window(start=window_start, end=window_end, invocationMinutes=invocation_minutes)
        token = github_token(github_token_env)
        ghcr = ghcr_credentials(ghcr_username, ghcr_token_env)
        directory = output_dir if output_dir is not None else new_run_directory(repository)
        paths = [directory / n for n in ("forecast-input.json", "forecast.json", "forecast.html")]
        paths += [
            directory / f"{provider}.{label}.scan.json"
            for provider in ("dependabot", "renovate")
            for label in ("baseline", "optimised")
        ]
        preflight(paths, [])
        if registry_ca is None and mirror:
            registry_ca = fallback_config_dir / ".gappy-mirror/ca.crt"
        if source_ca is None and (fallback_config_dir / ".gappy-mirror/source-ca.crt").is_file():
            source_ca = fallback_config_dir / ".gappy-mirror/source-ca.crt"
        with checkout(source, repository) as checked:
            inputs = collect(
                checked,
                ref,
                repository,
                fallback_config_dir,
                optimised_config_dir or fallback_config_dir / "optimised",
                window,
                token,
                timeout=timeout_seconds,
                cache=cache,
                mirror=mirror,
                registry_ca=registry_ca,
                source_ca=source_ca,
                npm_mirror=npm_mirror,
                pypi_mirror=pypi_mirror,
                docker_mirror=docker_mirror,
                helm_mirror=helm_mirror,
                dependabot_workers=dependabot_workers,
                renovate_output_limit_mib=renovate_output_limit_mib,
                log_dir=directory / "logs",
                **({"ghcr": ghcr} if ghcr is not None else {}),
            )
        write_outputs(inputs, directory, verbose=verbose)
    except GappyError as exc:
        raise click.ClickException(str(exc)) from None
    except (OSError, ValidationError, ValueError):
        raise click.ClickException(
            "Forecast could not be collected. Check window, scanner/config support, "
            "credentials, CA files and output paths; existing invalid configs never fall back."
        ) from None
    click.echo(f"Forecast: {(directory / 'forecast.html').resolve()}")
    click.echo(f"Raw scanner logs: {(directory / 'logs').resolve()}")
    if open_chrome:
        from gappy.cli import open_in_chrome

        open_in_chrome(directory / "forecast.html")
    if any(v.scan.status == "cancelled" for v in inputs.variants):
        raise click.exceptions.Exit(130)
    if not inputs.platform.complete or any(
        v.scan.status != "complete" or not v.proposalEvidenceComplete for v in inputs.variants
    ):
        raise click.exceptions.Exit(1)


def write_outputs(inputs: ForecastInput, directory: Path, *, verbose: bool = False) -> None:
    contents = {
        directory / "forecast-input.json": dump(inputs.model_dump()),
        directory / "forecast.json": dump(calculate(inputs)),
        directory / "forecast.html": render(inputs, verbose=verbose),
    }
    for variant in inputs.variants:
        contents[directory / f"{variant.provider}.{variant.label}.scan.json"] = dump(
            variant.scan.model_dump()
        )
    publish(contents)


@click.command("forecast-replay")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
)
@click.option(
    "--output-dir", required=True, type=click.Path(path_type=Path, exists=True, file_okay=False)
)
@click.option("--verbose", is_flag=True, help="Show all dependency results in the HTML report.")
def replay(input_path: Path, output_dir: Path, verbose: bool = False) -> None:
    """Rebuild a forecast offline from its exact saved inputs; no Docker or GitHub."""
    try:
        inputs = ForecastInput.model_validate(read_json(input_path))
        write_outputs(inputs, output_dir, verbose=verbose)
    except (GappyError, OSError, ValueError):
        raise click.ClickException(
            "Cannot replay forecast; check input schema and output paths."
        ) from None
    click.echo(f"Forecast: {(output_dir / 'forecast.html').resolve()}")
