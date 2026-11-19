"""Combine two same-revision scanner comparisons without equating their configs."""

from typing import Any

from gappy.compare.discovered import compare, installed_version_key
from gappy.compare.scoring import score_configurations
from gappy.contracts import Scan
from gappy.io import GappyError


def compare_configurations(
    baseline: tuple[Scan, Scan], optimised: tuple[Scan, Scan]
) -> dict[str, Any]:
    scans = (*baseline, *optimised)
    if len({s.repository for s in scans}) != 1 or len({s.snapshot.revision for s in scans}) != 1:
        raise GappyError("All configuration variants must scan the same repository and revision.")
    comparisons: dict[str, Any] = {"baseline": compare(*baseline), "optimised": compare(*optimised)}
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    variants: list[dict[str, Any]] = []
    for label, pair in (("baseline", baseline), ("optimised", optimised)):
        for scan in pair:
            variants.append(
                {"id": f"{scan.provider}.{label}", "label": label, "scan": scan.model_dump()}
            )
        for row in comparisons[label]["dependencies"]:
            key = row["ecosystem"], row["package"], row["manifestPath"]
            result = grouped.setdefault(
                key,
                {
                    "ecosystem": key[0],
                    "package": key[1],
                    "manifestPath": key[2],
                    "variants": {},
                    "outcomes": {},
                },
            )
            result["outcomes"][label] = row["result"]
            for provider in ("dependabot", "renovate"):
                result["variants"][f"{provider}.{label}"] = row[provider]
    for row in grouped.values():
        sides = [row["variants"].get(v["id"]) for v in variants]
        observed = [side for side in sides if side and side["discovered"]]
        installed = {
            tuple(
                sorted(
                    {installed_version_key(row["ecosystem"], x) for x in side["installedVersions"]}
                )
            )
            for side in observed
        }
        registries = {
            registry
            for side in observed
            for registry in side["registries"]
            if registry != "unknown"
        }
        missing_from_complete_scan = any(
            v["scan"]["status"] == "complete" and (not side or not side["discovered"])
            for v, side in zip(variants, sides, strict=True)
        )
        row["hasChanges"] = (
            any(side["recommendations"] for side in observed)
            or (bool(observed) and missing_from_complete_scan)
            or len(installed) > 1
            or len(registries) > 1
        )
    dependencies = [grouped[key] for key in sorted(grouped)]
    return {
        "schemaVersion": "1.1.0",
        "mode": "configuration-comparison",
        "repository": baseline[0].repository,
        "revision": baseline[0].snapshot.revision,
        "variants": variants,
        "comparisons": comparisons,
        "dependencies": dependencies,
        "scoring": score_configurations(dependencies, variants),
        "warnings": [
            "Same tracked revision, with different configuration snapshots.",
            "Scanner evidence is not an atomic registry snapshot; differences can reflect "
            "registry timing, native caches or failures, not only configuration.",
            "Only direct dependency findings are compared. Unknown is not up to date.",
            "GitHub Actions are compared by repository and manifest, not individual "
            "sub-action or reusable workflow paths. Native observations retain those paths.",
        ],
    }
