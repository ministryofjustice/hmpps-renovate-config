"""Collect four isolated config evaluations from one pinned repository revision."""

import tempfile
from pathlib import Path
from threading import Lock
from typing import Literal

from gappy.contracts import Scan, timestamp
from gappy.forecast.config import policies, resolve
from gappy.forecast.github import capture
from gappy.forecast.models import ForecastInput, Proposal, Variant, Window
from gappy.forecast.proposals import extract
from gappy.io import GappyError
from gappy.scan.auth import GhcrCredentials
from gappy.scan.session import scan
from gappy.scan.snapshot import freeze


class ProposalCollector:
    def __init__(self) -> None:
        self.proposals: dict[str, list[Proposal]] = {"dependabot": [], "renovate": []}
        self.completeness: dict[str, list[bool]] = {"dependabot": [], "renovate": []}
        self.errors: dict[str, list[str]] = {"dependabot": [], "renovate": []}
        self.lock = Lock()

    def capture(self, provider: str, ecosystem: str, path: str, output: str) -> None:
        try:
            extracted, complete = extract(provider, ecosystem, path, output)
        except GappyError as exc:
            with self.lock:
                self.errors[provider].append(str(exc))
                self.completeness[provider].append(False)
            return
        with self.lock:
            self.proposals[provider].extend(extracted)
            self.completeness[provider].append(complete)


def collect(
    source: Path,
    ref: str,
    repository: str,
    fallback: Path,
    optimised: Path,
    window: Window,
    token: str | None,
    *,
    timeout: int = 180,
    cache: bool = True,
    mirror: bool = True,
    registry_ca: Path | None = None,
    source_ca: Path | None = None,
    npm_mirror: str | None = None,
    pypi_mirror: str | None = None,
    docker_mirror: str | None = None,
    helm_mirror: tuple[str, ...] = (),
    dependabot_workers: int = 2,
    renovate_output_limit_mib: int | None = None,
    ghcr: GhcrCredentials | None = None,
    log_dir: Path | None = None,
) -> ForecastInput:
    variants = []
    cancelled: tuple[Scan, Scan] | None = None
    with tempfile.TemporaryDirectory(prefix="gappy-forecast-") as directory:
        root = Path(directory)
        frozen = root / "source"
        frozen.mkdir()
        shared = freeze(source, ref, frozen, require_configs=False)
        configs = resolve(frozen, fallback, optimised, root)
        platform = capture(repository, token)
        if timestamp(window.start) < timestamp(platform.capturedAt):
            raise GappyError("Forecast window must start in the future, after state capture.")
        for label in ("baseline", "optimised"):
            typed_label: Literal["baseline", "optimised"] = (
                "baseline" if label == "baseline" else "optimised"
            )
            collector = ProposalCollector()

            scans: tuple[Scan, ...]
            if cancelled is None:
                scans = scan(
                    source,
                    shared.revision,
                    repository,
                    timeout,
                    registry_ca,
                    config_dir=root / label,
                    cache=cache,
                    cache_scope=label,
                    github_token=token,
                    mirror=mirror,
                    source_ca=source_ca,
                    proposal_sink=collector.capture,
                    replace_alternate_configs=True,
                    npm_mirror=npm_mirror,
                    pypi_mirror=pypi_mirror,
                    docker_mirror=docker_mirror,
                    helm_mirror=helm_mirror,
                    dependabot_workers=dependabot_workers,
                    renovate_output_limit_mib=renovate_output_limit_mib,
                    ghcr=ghcr,
                    log_dir=log_dir / label if log_dir is not None else None,
                )
                if any(s.status == "cancelled" for s in scans):
                    cancelled = scans
            else:
                scans = tuple(
                    result.model_copy(
                        update={
                            "status": "cancelled",
                            "observations": [],
                            "warnings": [
                                "Not run: an earlier forecast scenario was cancelled. "
                                "Snapshot/toolchain metadata comes from that scenario, "
                                "not execution of this configuration."
                            ],
                        }
                    )
                    for result in cancelled
                )
            for result in scans:
                origin, identity, config = configs[label, result.provider]
                found = sorted(
                    {p.id: p for p in collector.proposals[result.provider]}.values(),
                    key=lambda p: p.id,
                )
                variants.append(
                    Variant(
                        label=typed_label,
                        provider=result.provider,
                        configOrigin=origin,
                        configHash=identity,
                        scan=result,
                        proposals=found,
                        policies=policies(result.provider, config),
                        proposalEvidenceComplete=bool(collector.completeness[result.provider])
                        and all(collector.completeness[result.provider]),
                        warnings=collector.errors[result.provider]
                        + [
                            "Native scanners use collection time, not an injected future clock.",
                            "Mutable external presets may differ between captures; unresolved "
                            "policy "
                            "prevents exact future creation/queue totals.",
                        ],
                    )
                )
    return ForecastInput(
        repository=repository,
        revision=shared.revision,
        sourceHash=shared.contentHash,
        window=window,
        platform=platform,
        variants=variants,
    )
