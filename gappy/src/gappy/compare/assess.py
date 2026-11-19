"""Match actionable lookup evidence against independent patch opportunities."""

import hashlib
import re
from typing import Literal, TypedDict

from gappy.contracts import Detection, Inventory, Opportunity, Policy, Scan, timestamp
from gappy.io import GappyError, dump


class Assessment(TypedDict):
    extraction: str
    policy: Policy
    detection: Detection
    reason: str
    evidence: list[str]


class Row(TypedDict):
    id: str
    dependency: dict[str, object]
    candidateVersion: str
    availableAt: str
    dependabot: Assessment
    renovate: Assessment
    result: str
    winner: str | None


def semver(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def assess(scan: Scan, opportunity: Opportunity) -> Assessment:
    result: Assessment = {
        "extraction": "unknown",
        "policy": "unknown",
        "detection": "unknown",
        "reason": "No complete contextual extraction/lookup evidence.",
        "evidence": [],
    }
    matches = [
        item
        for item in scan.observations
        if item.directness == "direct" and item.dependency.key() == opportunity.dependency.key()
    ]
    if not matches:
        return result
    observation = matches[0]
    result["extraction"] = "extracted" if observation.extracted else "not-extracted"
    result["policy"] = observation.policy
    result["reason"] = observation.reason
    if observation.dependency.registry != opportunity.dependency.registry:
        result["reason"] = "Registry identity differs or is unavailable."
        return result
    installed = semver(opportunity.dependency.installedVersion)
    candidate = semver(opportunity.candidateVersion)
    if (
        opportunity.dependency.versioning != "semver"
        or installed is None
        or candidate is None
        or installed[:2] != candidate[:2]
        or installed >= candidate
    ):
        result["reason"] = "Unsupported or non-patch version opportunity."
        return result
    if timestamp(opportunity.availableAt) > timestamp(scan.startedAt):
        result["reason"] = "Patch availability is not proven before this lookup interval."
        return result
    if observation.policy in {"excluded", "deferred"}:
        result["detection"] = "not-detected"
        return result
    for evidence in observation.candidates:
        target = semver(evidence.targetVersion)
        if target is not None and target[:2] == candidate[:2] and target >= candidate:
            result["evidence"].append(evidence.evidenceId)
    if result["evidence"]:
        result["detection"] = "detected"
        result["reason"] = "Native actionable patch lookup, exact or later in the same patch line."
    elif (
        observation.policy == "eligible"
        and observation.lookupComplete
        and scan.status == "complete"
    ):
        # A later minor/major proposal does not prove that the patch was overlooked.
        if any(
            (target := semver(item.targetVersion)) is None or target[:2] != candidate[:2]
            for item in observation.candidates
        ):
            result["reason"] = "Latest-only output cannot establish this patch-line absence."
        else:
            result["detection"] = "not-detected"
            result["reason"] = "No matching actionable update in the supported complete lookup."
    return result


def compare(dependabot: Scan, renovate: Scan, inventory: Inventory) -> dict[str, object]:
    if dependabot.provider != "dependabot" or renovate.provider != "renovate":
        raise GappyError("Provider exports are in the wrong input positions.")
    if (
        dependabot.sessionId != renovate.sessionId
        or dependabot.repository != renovate.repository
        or dependabot.snapshot != renovate.snapshot
        or dependabot.repository != inventory.repository
        or dependabot.snapshot.contentHash != inventory.snapshotHash
    ):
        raise GappyError("Repository, session, snapshot, or configuration identities do not match.")
    rows: list[Row] = []
    seen: set[str] = set()
    direct_contexts = {
        (dep.ecosystem, dep.package, dep.manifestPath)
        for scan in (dependabot, renovate)
        for item in scan.observations
        if item.directness == "direct" and item.extracted
        for dep in [item.dependency]
    }
    excluded = {"transitive": 0, "unknown": 0}
    for opportunity in inventory.opportunities:
        identity = hashlib.sha256(
            (inventory.snapshotHash + dump(opportunity.model_dump(exclude={"directness"}))).encode()
        ).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        dependency = opportunity.dependency
        context = (dependency.ecosystem, dependency.package, dependency.manifestPath)
        if opportunity.directness == "transitive":
            excluded["transitive"] += 1
            continue
        if opportunity.directness == "unknown" and context not in direct_contexts:
            excluded["unknown"] += 1
            continue
        left, right = assess(dependabot, opportunity), assess(renovate, opportunity)
        a, b = left["detection"], right["detection"]
        winner = None
        if "unknown" in (a, b):
            outcome = "not-assessable"
        elif a == b:
            outcome = "both" if a == "detected" else "neither"
        else:
            winner = "dependabot" if a == "detected" else "renovate"
            outcome = f"{winner}-only"
        rows.append(
            {
                "id": identity,
                "dependency": opportunity.dependency.model_dump(),
                "candidateVersion": opportunity.candidateVersion,
                "availableAt": opportunity.availableAt,
                "dependabot": left,
                "renovate": right,
                "result": outcome,
                "winner": winner,
            }
        )
    rows.sort(
        key=lambda row: dump(row["dependency"]) + row["candidateVersion"] + row["availableAt"]
    )
    jointly = [
        row
        for row in rows
        if row["dependabot"]["policy"] == row["renovate"]["policy"] == "eligible"
    ]
    paired = [row for row in jointly if row["result"] != "not-assessable"]
    denominator = len(paired)
    counts = {
        "dependabot": sum(row["dependabot"]["detection"] == "detected" for row in paired),
        "renovate": sum(row["renovate"]["detection"] == "detected" for row in paired),
    }
    summary: Literal["dependabot", "renovate", "tie", "insufficient-evidence"]
    if not denominator:
        summary = "insufficient-evidence"
    elif counts["dependabot"] == counts["renovate"]:
        summary = "tie"
    else:
        summary = "dependabot" if counts["dependabot"] > counts["renovate"] else "renovate"
    warnings = sorted(
        set(
            dependabot.warnings
            + renovate.warnings
            + inventory.provenance.warnings
            + [
                "Conclusion applies only to the supplied inventory and assessed subset.",
                "Only direct dependency opportunities are assessed. Native scan evidence "
                "retains transitive and unclassified observations.",
                "Directness follows available manifest metadata, not a complete dependency graph.",
                "Live registry observations are not an atomic historical snapshot.",
            ]
            + (
                [
                    f"Excluded {excluded['transitive']} transitive and {excluded['unknown']} "
                    "unclassified inventory opportunities. For independent direct dependencies "
                    "missed by both scanners, supply directness=direct in the inventory; "
                    "rescan older exports to recover scanner directness evidence."
                ]
                if any(excluded.values())
                else []
            )
            + (
                []
                if inventory.provenance.complete
                else ["Inventory completeness is not established."]
            )
        )
    )
    return {
        "schemaVersion": "1.1.0",
        "repository": inventory.repository,
        "sessionId": dependabot.sessionId,
        "snapshot": dependabot.snapshot.model_dump(),
        "inputHashes": {
            "dependabot": hashlib.sha256(dump(dependabot.model_dump()).encode()).hexdigest(),
            "renovate": hashlib.sha256(dump(renovate.model_dump()).encode()).hexdigest(),
            "inventory": hashlib.sha256(dump(inventory.model_dump()).encode()).hexdigest(),
        },
        "inventory": inventory.model_dump(),
        "scans": [dependabot.model_dump(), renovate.model_dump()],
        "assessments": rows,
        "metrics": {
            "opportunities": len(rows),
            "excludedOpportunities": excluded,
            "jointlyEligible": len(jointly),
            "excludedFromPaired": len(jointly) - denominator,
            "denominator": denominator,
            "detections": counts,
            "coverage": {
                tool: count / denominator if denominator else None for tool, count in counts.items()
            },
            "winner": summary,
            "eligibility": {
                "dependabotOnly": sum(
                    row["dependabot"]["policy"] == "eligible"
                    and row["renovate"]["policy"] in {"excluded", "deferred"}
                    for row in rows
                ),
                "renovateOnly": sum(
                    row["renovate"]["policy"] == "eligible"
                    and row["dependabot"]["policy"] in {"excluded", "deferred"}
                    for row in rows
                ),
                "unknown": sum(
                    "unknown" in (row["dependabot"]["policy"], row["renovate"]["policy"])
                    for row in rows
                ),
            },
            "extraction": {
                "dependabot": {
                    "extractedOpportunities": sum(
                        row["dependabot"]["extraction"] == "extracted" for row in rows
                    ),
                    "inventoryOpportunities": len(rows),
                },
                "renovate": {
                    "extractedOpportunities": sum(
                        row["renovate"]["extraction"] == "extracted" for row in rows
                    ),
                    "inventoryOpportunities": len(rows),
                },
            },
            "outcomes": {
                outcome: sum(row["result"] == outcome for row in rows)
                for outcome in (
                    "dependabot-only",
                    "renovate-only",
                    "both",
                    "neither",
                    "not-assessable",
                )
            },
        },
        "configurationDifferences": [
            {
                "opportunityId": row["id"],
                "dependabot": row["dependabot"]["policy"],
                "renovate": row["renovate"]["policy"],
                "reason": "Policy eligibility differs; inspect provider evidence and limitations.",
            }
            for row in rows
            if row["dependabot"]["policy"] != row["renovate"]["policy"]
        ],
        "recommendations": [
            {
                "opportunityId": row["id"],
                "rationale": "Investigate the policy and native lookup evidence.",
            }
            for row in rows
            if row["result"] not in {"both", "not-assessable"}
        ],
        "warnings": warnings,
    }
