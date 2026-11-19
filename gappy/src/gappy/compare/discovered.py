"""Compare native discovery and recommendations without an independent inventory."""

import hashlib
import re
from collections import defaultdict
from typing import TypedDict

from gappy.contracts import Observation, Provider, Scan
from gappy.io import GappyError, dump
from gappy.scan.ecosystems import canonical_package
from gappy.versions import pinned_digest


class Side(TypedDict):
    discovered: bool
    installedVersions: list[str]
    registries: list[str]
    recommendations: list[str]
    policy: list[str]
    lookupComplete: bool
    evidence: list[str]
    reason: str
    configuration: list[dict[str, object]]


class Row(TypedDict):
    id: str
    ecosystem: str
    package: str
    manifestPath: str
    dependabot: Side
    renovate: Side
    result: str


def installed_version_key(ecosystem: str, version: str) -> str:
    if digest := pinned_digest(ecosystem, version):
        return digest
    if ecosystem in {"npm", "helm", "github-actions"} and re.fullmatch(
        r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        version,
    ):
        return version[1:]
    return version


def update_counts(rows: list[Row], provider: Provider) -> dict[str, int]:
    return {
        "discovered": sum(row[provider]["discovered"] for row in rows),
        "withRecommendations": sum(bool(row[provider]["recommendations"]) for row in rows),
        "recommendedUpdates": sum(len(row[provider]["recommendations"]) for row in rows),
    }


def compare(left: Scan, right: Scan) -> dict[str, object]:
    if left.provider != "dependabot" or right.provider != "renovate":
        raise GappyError("Provider exports are in the wrong input positions.")
    if (
        left.sessionId != right.sessionId
        or left.repository != right.repository
        or left.snapshot != right.snapshot
    ):
        raise GappyError("Repository, session, snapshot, or configuration identities do not match.")
    grouped: dict[tuple[str, str, str], dict[str, list[Observation]]] = defaultdict(
        lambda: {"dependabot": [], "renovate": []}
    )
    excluded = {
        scan.provider: {
            scope: sum(item.directness == scope for item in scan.observations)
            for scope in ("transitive", "unknown")
        }
        for scan in (left, right)
    }
    for scan in (left, right):
        for item in scan.observations:
            if item.directness != "direct":
                continue
            dep = item.dependency
            package = dep.package
            if dep.ecosystem == "github-actions":
                package = canonical_package(dep.ecosystem, package)
                parts = package.split("/")
                if len(parts) >= 2 and all(parts[:2]) and parts[0] not in {".", ".."}:
                    package = "/".join(parts[:2])
            grouped[(dep.ecosystem, package, dep.manifestPath)][scan.provider].append(item)
    rows: list[Row] = []
    for (ecosystem, package, manifest), providers in sorted(grouped.items()):
        sides: dict[str, Side] = {}
        for provider, items in providers.items():
            scan = left if provider == "dependabot" else right
            sides[provider] = {
                "discovered": any(item.extracted for item in items),
                "installedVersions": sorted({item.dependency.installedVersion for item in items}),
                "registries": sorted({item.dependency.registry or "unknown" for item in items}),
                "recommendations": sorted(
                    {
                        f"{candidate.targetPackage}:{candidate.targetVersion}"
                        if candidate.targetPackage
                        else candidate.targetVersion
                        for item in items
                        for candidate in item.candidates
                    }
                ),
                "policy": sorted({item.policy for item in items}),
                "lookupComplete": scan.status == "complete"
                and bool(items)
                and all(item.lookupComplete for item in items),
                "evidence": sorted(
                    {candidate.evidenceId for item in items for candidate in item.candidates}
                ),
                "reason": (
                    " ".join(sorted({item.reason for item in items}))
                    if items
                    else f"Not present in this {scan.status} scan's direct discovery evidence."
                ),
                "configuration": [
                    ref.model_dump()
                    for _, ref in sorted(
                        {
                            (ref.id, ref.basis): ref for item in items for ref in item.configuration
                        }.items()
                    )
                ],
            }
        a, b = sides["dependabot"], sides["renovate"]
        known_registries_a = set(a["registries"]) - {"unknown"}
        known_registries_b = set(b["registries"]) - {"unknown"}
        if not a["discovered"] and not b["discovered"]:
            outcome = "not-assessable"
        elif not a["discovered"] or not b["discovered"]:
            outcome = "dependabot-only-discovery" if a["discovered"] else "renovate-only-discovery"
        elif {installed_version_key(ecosystem, v) for v in a["installedVersions"]} != {
            installed_version_key(ecosystem, v) for v in b["installedVersions"]
        }:
            left_pins = [pinned_digest(ecosystem, v) is not None for v in a["installedVersions"]]
            right_pins = [pinned_digest(ecosystem, v) is not None for v in b["installedVersions"]]
            outcome = (
                "installed-reference-unresolved"
                if (any(left_pins) and not all(right_pins))
                or (any(right_pins) and not all(left_pins))
                else "installed-version-mismatch"
            )
        elif known_registries_a and known_registries_b and known_registries_a != known_registries_b:
            outcome = "registry-mismatch"
        elif a["recommendations"] != b["recommendations"]:
            outcome = "different-recommendations"
        elif a["recommendations"]:
            outcome = "same-recommendations"
        elif (
            a["lookupComplete"]
            and b["lookupComplete"]
            and a["policy"] == b["policy"] == ["eligible"]
        ):
            outcome = "no-recommendations"
        else:
            outcome = "not-assessable"
        rows.append(
            {
                "id": hashlib.sha256(dump([ecosystem, package, manifest]).encode()).hexdigest(),
                "ecosystem": ecosystem,
                "package": package,
                "manifestPath": manifest,
                "dependabot": sides["dependabot"],
                "renovate": sides["renovate"],
                "result": outcome,
            }
        )
    provider_names: tuple[Provider, ...] = ("dependabot", "renovate")
    return {
        "schemaVersion": "1.1.0",
        "mode": "scanner-comparison",
        "repository": left.repository,
        "sessionId": left.sessionId,
        "snapshot": left.snapshot.model_dump(),
        "inputHashes": {
            scan.provider: hashlib.sha256(dump(scan.model_dump()).encode()).hexdigest()
            for scan in (left, right)
        },
        "scans": [left.model_dump(), right.model_dump()],
        "dependencies": rows,
        "metrics": {
            "dependencies": len(rows),
            "ecosystems": sorted({row["ecosystem"] for row in rows}),
            "excludedObservations": excluded,
            "providers": {provider: update_counts(rows, provider) for provider in provider_names},
            "byEcosystem": {
                ecosystem: {
                    provider: update_counts(
                        [row for row in rows if row["ecosystem"] == ecosystem], provider
                    )
                    for provider in provider_names
                }
                for ecosystem in sorted({row["ecosystem"] for row in rows})
            },
            "outcomes": {
                outcome: sum(row["result"] == outcome for row in rows)
                for outcome in (
                    "dependabot-only-discovery",
                    "renovate-only-discovery",
                    "installed-version-mismatch",
                    "installed-reference-unresolved",
                    "registry-mismatch",
                    "different-recommendations",
                    "same-recommendations",
                    "no-recommendations",
                    "not-assessable",
                )
            },
        },
        "warnings": sorted(
            set(
                left.warnings
                + right.warnings
                + [
                    "Discovery combines native observations; it is not an independent inventory.",
                    "Only direct dependency findings are compared; native scan evidence "
                    "retains transitive and unclassified observations.",
                    "GitHub Actions are compared by repository and manifest: sub-actions and "
                    "reusable workflow paths share repository releases. Native observations "
                    "retain individual paths; a repository match does not prove identical uses.",
                    "Directness follows scanner manifest metadata, not a complete dependency "
                    "graph; flattened or generated manifests may not identify transitive packages.",
                    "A direct dependency update does not prove which transitive packages change.",
                    "Dependencies or updates missed by both tools cannot be measured here.",
                    "More recommendations do not prove better coverage; inspect policies.",
                    "One-sided discovery may reflect config, unsupported managers or failures.",
                    "Live registry observations are not an atomic historical snapshot.",
                ]
                + [
                    f"{scan.provider.title()}: excluded {excluded[scan.provider]['transitive']} "
                    "transitive and "
                    f"{excluded[scan.provider]['unknown']} unclassified observations from "
                    "findings and counts; rescan older exports to recover directness evidence."
                    for scan in (left, right)
                    if any(excluded[scan.provider].values())
                ]
                + (
                    ["Scanner cache modes differ; observations may have different cache freshness."]
                    if left.cacheMode != right.cacheMode
                    else []
                )
            )
        ),
    }
