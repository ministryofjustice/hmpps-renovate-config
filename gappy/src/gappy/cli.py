"""Click commands separating network-enabled scans from offline comparison."""

import re
import shutil
import subprocess
import sys
import uuid
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import TypedDict

import click
from pydantic import ValidationError

from gappy.compare.assess import compare as compare_results
from gappy.compare.discovered import compare as compare_discovered
from gappy.contracts import Inventory, Scan
from gappy.forecast.cli import forecast_command, replay
from gappy.io import GappyError, dump, preflight, publish, read_json
from gappy.report.html import render
from gappy.scan.auth import GhcrCredentials, ghcr_credentials


class ScanOverrides(TypedDict, total=False):
    npm_mirror: str
    config_dir: Path
    pypi_mirror: str
    docker_mirror: str
    helm_mirror: tuple[str, ...]
    dependabot_workers: int
    github_token: str
    ghcr: "GhcrCredentials"
    renovate_output_limit_mib: int
    mirror: bool
    source_ca: Path
    replace_alternate_configs: bool
    cache_scope: str
    cancel: Event
    log_dir: Path


def open_in_chrome(path: Path) -> None:
    url = path.resolve().as_uri()
    if sys.platform == "darwin":
        args = ["open", "-a", "Google Chrome", url]
    else:
        executable = next(
            (
                found
                for name in ("google-chrome", "google-chrome-stable", "chrome")
                if (found := shutil.which(name))
            ),
            None,
        )
        if executable is None:
            raise click.ClickException(
                f"Report saved at {path.resolve()}, but Chrome was not found on PATH."
            )
        if not webbrowser.BackgroundBrowser(executable).open(url):
            raise click.ClickException(
                f"Report saved at {path.resolve()}, but Chrome could not be opened."
            )
        return
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise click.ClickException(
            f"Report saved at {path.resolve()}, but Chrome could not be opened."
        ) from None


class GappyGroup(click.Group):
    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except KeyboardInterrupt:
            raise click.exceptions.Exit(130) from None


def validate_repository(repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or any(
        part in {".", ".."} for part in repository.split("/")
    ):
        raise click.BadParameter("Use owner/name format.", param_hint="--repository")


def new_run_directory(repository: str) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
    directory = Path.cwd() / "gappy-results" / repository / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def export_scans(left: Scan, right: Scan, left_path: Path, right_path: Path) -> int:
    publish({left_path: dump(left.model_dump()), right_path: dump(right.model_dump())})
    click.echo(f"Dependabot: {left.status}; Renovate: {right.status}.")
    click.echo(f"Dependabot JSON: {left_path.resolve()}")
    click.echo(f"Renovate JSON: {right_path.resolve()}")
    if "cancelled" in {left.status, right.status}:
        return 130
    return 0 if left.status == right.status == "complete" else 1


def latest_run_directory(repository: str) -> Path | None:
    root = Path.cwd() / "gappy-results" / repository
    try:
        runs = (
            sorted(
                (
                    path
                    for path in root.iterdir()
                    if path.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{12}", path.name)
                ),
                key=lambda path: (path.name[:16], path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
            if root.is_dir()
            else []
        )
    except OSError:
        raise click.UsageError("Cannot inspect repository scan runs; check permissions.") from None
    return runs[0] if runs else None


def latest_run(repository: str) -> Path:
    selected = latest_run_directory(repository)
    if selected is None:
        raise click.UsageError("No scan run found; run gappy scan or supply both scan JSON paths.")
    if not all(
        (selected / f"{provider}.scan.json").is_file() for provider in ("dependabot", "renovate")
    ):
        raise click.UsageError(
            "The latest scan run is missing exports; finish the scan or supply both JSON paths. "
            "Older runs are not selected silently."
        )
    return selected


@click.group(cls=GappyGroup)
@click.version_option(package_name="gappy")
def main() -> None:
    """Compare dependencies discovered and updates recommended by Docker scanners."""


main.add_command(forecast_command)
main.add_command(replay)


@main.command()
@click.option("--repository", required=True, help="Repository identity as owner/name.")
@click.option(
    "--source",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Existing checkout; by default clone the GitHub repository temporarily.",
)
@click.option(
    "--ref",
    default="HEAD",
    show_default=True,
    help="Tracked Git revision; untracked files are excluded.",
)
@click.option(
    "--dependabot-json",
    type=click.Path(path_type=Path),
    help="Export destination; defaults to a new run directory under ./gappy-results/.",
)
@click.option(
    "--renovate-json",
    type=click.Path(path_type=Path),
    help="Export destination; defaults to the same new run directory.",
)
@click.option("--timeout-seconds", default=180, show_default=True, type=click.IntRange(1, 3600))
@click.option(
    "--renovate-output-limit-mib",
    type=click.IntRange(1, 256),
    help="Optional retained Renovate output limit in MiB (1-256); no fixed limit by default.",
)
@click.option(
    "--dependabot-workers",
    default=2,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent Dependabot ecosystems; use 1 for sequential scanning.",
)
@click.option(
    "--cache/--no-cache",
    default=True,
    show_default=True,
    help="Reuse the common Docker download volume; --no-cache uses fresh native caches.",
)
@click.option(
    "--registry-ca",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Explicit CA certificate for a controlled registry; never disables TLS checks.",
)
@click.option(
    "--source-ca",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Additional trusted PEM CA for source TLS; defaults to .gappy-mirror/source-ca.crt.",
)
@click.option(
    "--mirror/--no-mirror",
    default=True,
    show_default=True,
    help="Use bundled Nexus defaults for npm, PyPI, Docker Hub and the HMPPS Helm upstream.",
)
@click.option("--npm-mirror", help="Override the default public npm proxy URL.")
@click.option("--pypi-mirror", help="Public PyPI proxy simple-index URL for pip lookups.")
@click.option("--docker-mirror", help="HTTPS Docker Hub proxy origin (no repository path).")
@click.option(
    "--helm-mirror",
    multiple=True,
    help="Route a Helm HTTP repository via UPSTREAM_URL=PROXY_URL; repeat for each upstream.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Folder containing dependabot.yml and renovate.json; defaults to the current directory.",
)
@click.option(
    "--repository-config",
    is_flag=True,
    help="Use the target revision's configs instead of Gappy's root configs.",
)
@click.option(
    "--github-token-env",
    metavar="NAME",
    help="Opt in to GitHub authentication using the token in environment variable NAME.",
)
@click.option("--ghcr-username", help="GitHub account for private GHCR image lookups.")
@click.option(
    "--ghcr-token-env",
    metavar="NAME",
    help="Opt-in environment variable containing a GHCR read:packages token.",
)
def scan(
    repository: str,
    source: Path | None,
    ref: str,
    dependabot_json: Path | None,
    renovate_json: Path | None,
    timeout_seconds: int,
    registry_ca: Path | None,
    cache: bool,
    npm_mirror: str | None = None,
    config_dir: Path | None = None,
    repository_config: bool = False,
    pypi_mirror: str | None = None,
    docker_mirror: str | None = None,
    helm_mirror: tuple[str, ...] = (),
    dependabot_workers: int = 2,
    github_token_env: str | None = None,
    renovate_output_limit_mib: int | None = None,
    mirror: bool = True,
    source_ca: Path | None = None,
    replace_alternate_configs: bool = False,
    cache_scope: str | None = None,
    cancel: Event | None = None,
    ghcr_username: str | None = None,
    ghcr_token_env: str | None = None,
    log_dir: Path | None = None,
) -> None:
    """Run pinned Docker scanners; network access is allowed only in this stage.

    Requires Docker, Git, Buildx, and Dependabot CLI 1.94.0 on PATH.
    Configured ecosystems run as separate native jobs. Unsupported settings and
    failed jobs produce explicit diagnostics, never successful empty discovery lists.
    Redacted raw scanner output is saved under logs/ next to the scan JSON.
    """
    from gappy.scan.auth import github_token
    from gappy.scan.session import scan as collect
    from gappy.scan.snapshot import checkout

    validate_repository(repository)
    if repository_config and config_dir is not None:
        raise click.UsageError("--config-dir and --repository-config cannot be combined.")
    try:
        token = github_token(github_token_env)
        ghcr = ghcr_credentials(ghcr_username, ghcr_token_env)
        if dependabot_json is None or renovate_json is None:
            output_directory = new_run_directory(repository)
            dependabot_json = dependabot_json or output_directory / "dependabot.scan.json"
            renovate_json = renovate_json or output_directory / "renovate.scan.json"
        inputs = (
            ([source] if source is not None else [])
            + ([registry_ca] if registry_ca is not None else [])
            + ([source_ca] if source_ca is not None else [])
        )
        preflight([dependabot_json, renovate_json], inputs)
        with checkout(source, repository) as resolved_source:
            overrides: ScanOverrides = {"npm_mirror": npm_mirror} if npm_mirror is not None else {}
            if not repository_config:
                overrides["config_dir"] = config_dir or Path.cwd()
            if replace_alternate_configs:
                overrides["replace_alternate_configs"] = True
            if cache_scope is not None:
                overrides["cache_scope"] = cache_scope
            if cancel is not None:
                overrides["cancel"] = cancel
            overrides["log_dir"] = (
                log_dir if log_dir is not None else dependabot_json.parent / "logs"
            )
            if pypi_mirror is not None:
                overrides["pypi_mirror"] = pypi_mirror
            if docker_mirror is not None:
                overrides["docker_mirror"] = docker_mirror
            if helm_mirror:
                overrides["helm_mirror"] = helm_mirror
            if dependabot_workers != 2:
                overrides["dependabot_workers"] = dependabot_workers
            if token is not None:
                overrides["github_token"] = token
            if ghcr is not None:
                overrides["ghcr"] = ghcr
            if renovate_output_limit_mib is not None:
                overrides["renovate_output_limit_mib"] = renovate_output_limit_mib
            if not mirror:
                overrides["mirror"] = False
            if source_ca is not None:
                overrides["source_ca"] = source_ca
            if cache:
                left, right = collect(
                    resolved_source, ref, repository, timeout_seconds, registry_ca, **overrides
                )
            else:
                left, right = collect(
                    resolved_source,
                    ref,
                    repository,
                    timeout_seconds,
                    registry_ca,
                    cache=False,
                    **overrides,
                )
        exit_code = export_scans(left, right, dependabot_json, renovate_json)
        click.echo(f"Raw scanner logs: {overrides['log_dir'].resolve()}")
    except GappyError as exc:
        raise click.ClickException(str(exc)) from None
    except (OSError, ValidationError):
        raise click.ClickException(
            "Scan input, filesystem, or native contract validation failed."
        ) from None
    if exit_code:
        raise click.exceptions.Exit(exit_code)


@main.command()
@click.option("--repository", help="Select the latest run under ./gappy-results/owner/repository/.")
@click.option(
    "--dependabot-json",
    type=click.Path(path_type=Path),
    help="Override the Dependabot export; defaults to the latest repository run.",
)
@click.option(
    "--renovate-json",
    type=click.Path(path_type=Path),
    help="Override the Renovate export; defaults to the same repository run.",
)
@click.option(
    "--inventory",
    type=click.Path(path_type=Path),
    help="Optional legacy independent patch assessment; not needed for scanner comparison.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(path_type=Path),
    help="Defaults to comparison.json beside the selected scan exports.",
)
@click.option(
    "--html",
    "html_path",
    type=click.Path(path_type=Path),
    help="Defaults to comparison.html beside the selected scan exports.",
)
@click.option("--open-chrome", is_flag=True, help="Open the saved HTML report in Google Chrome.")
@click.option("--verbose", is_flag=True, help="Show all dependency results in the HTML report.")
@click.option("--optimised-dependabot-json", type=click.Path(path_type=Path))
@click.option("--optimised-renovate-json", type=click.Path(path_type=Path))
def compare(
    repository: str | None,
    dependabot_json: Path | None,
    renovate_json: Path | None,
    inventory: Path | None,
    json_path: Path | None,
    html_path: Path | None,
    open_chrome: bool = False,
    verbose: bool = False,
    optimised_dependabot_json: Path | None = None,
    optimised_renovate_json: Path | None = None,
) -> None:
    """Compare native discoveries and recommendations by dependency, across ecosystems.

    Offline: no Docker, registry calls, PR exports, or credentials are required.
    Existing outputs are never overwritten.
    """
    if repository is not None:
        validate_repository(repository)
    if dependabot_json is None or renovate_json is None:
        if repository is None:
            raise click.UsageError("Supply --repository or both scan JSON paths.")
        directory = latest_run(repository)
        dependabot_json = dependabot_json or directory / "dependabot.scan.json"
        renovate_json = renovate_json or directory / "renovate.scan.json"
    if optimised_dependabot_json is None and optimised_renovate_json is None:
        opt_left = dependabot_json.parent / "dependabot.optimised.scan.json"
        opt_right = renovate_json.parent / "renovate.optimised.scan.json"
        if opt_left.exists() or opt_right.exists():
            optimised_dependabot_json, optimised_renovate_json = opt_left, opt_right
    if (optimised_dependabot_json is None) != (optimised_renovate_json is None):
        raise click.UsageError("Supply both optimised scan JSON paths.")
    if inventory is not None and optimised_dependabot_json is not None:
        raise click.UsageError("--inventory cannot be combined with optimised scan exports.")
    if json_path is None or html_path is None:
        if dependabot_json.resolve().parent != renovate_json.resolve().parent:
            raise click.UsageError(
                "Scan exports are in different directories; supply both --json and --html paths."
            )
        directory = dependabot_json.resolve().parent
        json_path = json_path or directory / "comparison.json"
        html_path = html_path or directory / "comparison.html"
    try:
        preflight(
            [json_path, html_path],
            [dependabot_json, renovate_json]
            + ([inventory] if inventory is not None else [])
            + (
                [optimised_dependabot_json, optimised_renovate_json]
                if optimised_dependabot_json is not None and optimised_renovate_json is not None
                else []
            ),
        )
    except GappyError as exc:
        raise click.ClickException(str(exc)) from None
    try:
        left = Scan.model_validate(read_json(dependabot_json))
        right = Scan.model_validate(read_json(renovate_json))
        if repository is not None and (
            left.repository != repository or right.repository != repository
        ):
            raise GappyError("Scan exports do not match --repository.")
        report = (
            compare_results(left, right, Inventory.model_validate(read_json(inventory)))
            if inventory is not None
            else compare_discovered(left, right)
        )
        if optimised_dependabot_json is not None and optimised_renovate_json is not None:
            from gappy.compare.configurations import compare_configurations

            report = compare_configurations(
                (left, right),
                (
                    Scan.model_validate(read_json(optimised_dependabot_json)),
                    Scan.model_validate(read_json(optimised_renovate_json)),
                ),
            )
    except ValidationError:
        raise click.UsageError(
            "Input schema is invalid; check version, identity, dates, and fields."
        ) from None
    except GappyError as exc:
        raise click.UsageError(str(exc)) from None
    try:
        publish({json_path: dump(report), html_path: render(report, verbose=verbose)})
    except GappyError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo("Wrote JSON and HTML; review the assessed subset and limitations.")
    click.echo(f"Comparison JSON: {json_path.resolve()}")
    click.echo(f"Comparison HTML: {html_path.resolve()}")
    if open_chrome:
        open_in_chrome(html_path)


@main.command()
@click.option("--repository", required=True, help="GitHub repository identity as owner/name.")
@click.option(
    "--inventory",
    type=click.Path(path_type=Path),
    help="Optional legacy independent patch assessment; never generated by Gappy.",
)
@click.option(
    "--source",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Existing checkout; otherwise clone temporarily.",
)
@click.option("--ref", default="HEAD", show_default=True, help="Tracked revision to scan.")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    help="Existing report directory; defaults to a new run under ./gappy-results/.",
)
@click.option("--timeout-seconds", default=180, show_default=True, type=click.IntRange(1, 3600))
@click.option(
    "--renovate-output-limit-mib",
    type=click.IntRange(1, 256),
    help="Optional retained Renovate output limit in MiB (1-256); no fixed limit by default.",
)
@click.option(
    "--dependabot-workers",
    default=2,
    show_default=True,
    type=click.IntRange(1, 16),
    help="Maximum concurrent Dependabot ecosystems; use 1 for sequential scanning.",
)
@click.option(
    "--cache/--no-cache",
    default=True,
    show_default=True,
    help="Reuse the common Docker download volume; --no-cache uses fresh native caches.",
)
@click.option("--registry-ca", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--source-ca",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Additional trusted PEM CA for source TLS; defaults to .gappy-mirror/source-ca.crt.",
)
@click.option(
    "--mirror/--no-mirror",
    default=True,
    show_default=True,
    help="Use bundled Nexus defaults for npm, PyPI, Docker Hub and the HMPPS Helm upstream.",
)
@click.option("--npm-mirror", help="Override the default public npm proxy URL.")
@click.option("--pypi-mirror", help="Public PyPI proxy simple-index URL for pip lookups.")
@click.option("--docker-mirror", help="HTTPS Docker Hub proxy origin (no repository path).")
@click.option(
    "--helm-mirror",
    multiple=True,
    help="Route a Helm HTTP repository via UPSTREAM_URL=PROXY_URL; repeat for each upstream.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Folder containing dependabot.yml and renovate.json; defaults to the current directory.",
)
@click.option(
    "--repository-config",
    is_flag=True,
    help="Use the target revision's configs instead of Gappy's root configs.",
)
@click.option("--open-chrome", is_flag=True, help="Open the saved HTML report in Google Chrome.")
@click.option("--verbose", is_flag=True, help="Show all dependency results in the HTML report.")
@click.option(
    "--optimised-config-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Second config set; defaults to optimised/ under --config-dir or the current directory.",
)
@click.option(
    "--optimised",
    is_flag=True,
    help="Rerun optimised scans; reuse compatible baseline exports, or scan them if unavailable.",
)
@click.option(
    "--baseline",
    is_flag=True,
    help="Rerun baseline scans; reuse compatible optimised exports, or scan them if unavailable.",
)
@click.option(
    "--reuse-from",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Prior run for --baseline/--optimised; defaults to the latest repository run.",
)
@click.option(
    "--github-token-env",
    metavar="NAME",
    help="Opt in to GitHub authentication using the token in environment variable NAME.",
)
@click.option("--ghcr-username", help="GitHub account for private GHCR image lookups.")
@click.option(
    "--ghcr-token-env",
    metavar="NAME",
    help="Opt-in environment variable containing a GHCR read:packages token.",
)
@click.pass_context
def full(
    ctx: click.Context,
    repository: str,
    inventory: Path | None,
    source: Path | None,
    ref: str,
    output_dir: Path | None,
    timeout_seconds: int,
    registry_ca: Path | None,
    cache: bool,
    npm_mirror: str | None = None,
    config_dir: Path | None = None,
    repository_config: bool = False,
    open_chrome: bool = False,
    pypi_mirror: str | None = None,
    docker_mirror: str | None = None,
    helm_mirror: tuple[str, ...] = (),
    dependabot_workers: int = 2,
    github_token_env: str | None = None,
    renovate_output_limit_mib: int | None = None,
    mirror: bool = True,
    source_ca: Path | None = None,
    verbose: bool = False,
    optimised_config_dir: Path | None = None,
    optimised: bool = False,
    baseline: bool = False,
    reuse_from: Path | None = None,
    ghcr_username: str | None = None,
    ghcr_token_env: str | None = None,
) -> None:
    """Scan both tools with baseline and optimised configs in one report.

    Baseline uses --config-dir or the current directory unless --repository-config is set.
    Optimised defaults to CONFIG_DIR/optimised. Each scanned variant requires both configs.
    --baseline/--optimised reruns one variant and reuses a complete same-revision other pair.
    --inventory retains legacy single-pair assessment.
    Network is allowed during scanning only.
    Non-success scans retain exit status 1 even when a diagnostic report is written.
    """
    validate_repository(repository)
    if repository_config and config_dir is not None:
        raise click.UsageError("--config-dir and --repository-config cannot be combined.")
    if optimised and baseline:
        raise click.UsageError("Use --baseline or --optimised, not both; omit them to scan both.")
    if reuse_from is not None and not (baseline or optimised):
        raise click.UsageError("--reuse-from requires --baseline or --optimised.")
    if inventory is not None and (baseline or optimised):
        raise click.UsageError("--inventory cannot be combined with --baseline or --optimised.")
    if inventory is None:
        from gappy.full import run

        run(
            ctx,
            repository=repository,
            output_dir=output_dir,
            config_dir=config_dir,
            repository_config=repository_config,
            optimised_config_dir=optimised_config_dir,
            selected_variant="optimised" if optimised else "baseline" if baseline else None,
            reuse_from=reuse_from,
            verbose=verbose,
            open_chrome=open_chrome,
            source=source,
            ref=ref,
            timeout_seconds=timeout_seconds,
            registry_ca=registry_ca,
            source_ca=source_ca,
            cache=cache,
            npm_mirror=npm_mirror,
            pypi_mirror=pypi_mirror,
            docker_mirror=docker_mirror,
            helm_mirror=helm_mirror,
            dependabot_workers=dependabot_workers,
            github_token_env=github_token_env,
            ghcr_username=ghcr_username,
            ghcr_token_env=ghcr_token_env,
            renovate_output_limit_mib=renovate_output_limit_mib,
            mirror=mirror,
        )
        return
    if optimised_config_dir is not None:
        raise click.UsageError("--inventory cannot be combined with --optimised-config-dir.")
    if inventory is not None:
        try:
            baseline_inventory = Inventory.model_validate(read_json(inventory))
            if baseline_inventory.repository != repository:
                raise click.UsageError("Inventory does not match --repository.")
        except (ValidationError, GappyError):
            raise click.UsageError(
                "Invalid inventory; check its schema, dates, and readability."
            ) from None
    try:
        directory = output_dir if output_dir is not None else new_run_directory(repository)
        left = directory / "dependabot.scan.json"
        right = directory / "renovate.scan.json"
        json_path, html_path = directory / "comparison.json", directory / "comparison.html"
        inputs = (
            ([inventory] if inventory is not None else [])
            + ([source] if source is not None else [])
            + ([registry_ca] if registry_ca is not None else [])
            + ([source_ca] if source_ca is not None else [])
        )
        outputs = [left, right, json_path, html_path]
        preflight(outputs, inputs)
    except (OSError, GappyError):
        raise click.ClickException(
            "Cannot prepare full-run outputs; use an existing writable directory "
            "without conflicting input or output files."
        ) from None
    scan_exit = 0
    try:
        ctx.invoke(
            scan,
            repository=repository,
            source=source,
            ref=ref,
            dependabot_json=left,
            renovate_json=right,
            timeout_seconds=timeout_seconds,
            registry_ca=registry_ca,
            source_ca=source_ca,
            cache=cache,
            npm_mirror=npm_mirror,
            config_dir=config_dir,
            repository_config=repository_config,
            pypi_mirror=pypi_mirror,
            docker_mirror=docker_mirror,
            helm_mirror=helm_mirror,
            dependabot_workers=dependabot_workers,
            github_token_env=github_token_env,
            renovate_output_limit_mib=renovate_output_limit_mib,
            ghcr_username=ghcr_username,
            ghcr_token_env=ghcr_token_env,
            mirror=mirror,
        )
    except click.exceptions.Exit as exc:
        if exc.exit_code != 1:
            raise
        scan_exit = 1
        click.echo("Scans were not fully successful; generating a diagnostic comparison.")
    ctx.invoke(
        compare,
        repository=repository,
        dependabot_json=left,
        renovate_json=right,
        inventory=inventory,
        json_path=json_path,
        html_path=html_path,
        open_chrome=open_chrome,
        verbose=verbose,
    )
    if scan_exit:
        raise click.exceptions.Exit(scan_exit)
