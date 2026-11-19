"""Pure bounded forecast over frozen demand; never predicts merges or releases."""

import re
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gappy.contracts import Contract, timestamp
from gappy.forecast.models import ForecastInput, Policy, Proposal, Variant, Window

State = Literal["predicted-new", "queued", "deferred", "existing", "unknown"]
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class Decision(Contract):
    proposal: Proposal
    state: State
    reason: str
    at: str | None = None


class Result(Contract):
    provider: str
    label: str
    observedPotential: int
    potentialPRs: int | None
    predictedNew: int | None
    queued: int | None
    deferred: int | None
    existing: int
    unknown: int
    decisions: list[Decision]
    byEcosystem: dict[str, dict[str, int | None]]
    warnings: list[str]


def schedule_error(policy: Policy) -> str | None:
    try:
        ZoneInfo(policy.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return "Unknown schedule timezone."
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", policy.time):
        return "Unsupported schedule time."
    if policy.day not in DAYS:
        return "Unsupported schedule weekday."
    if policy.interval not in {
        "always",
        "daily",
        "weekly",
        "monthly",
        "quarterly",
        "semiannually",
        "yearly",
    }:
        return "Schedule interval is not certified for replay."
    return None


def due(at: datetime, policy: Policy) -> bool:
    local = at.astimezone(ZoneInfo(policy.timezone))
    if policy.interval == "always":
        return True
    if local.strftime("%H:%M") < policy.time:
        return False
    if policy.interval == "daily":
        return local.weekday() < 5
    if policy.interval == "weekly":
        return DAYS[local.weekday()] == policy.day
    if local.day != 1:
        return False
    return (
        policy.interval == "monthly"
        or policy.interval == "quarterly"
        and local.month in {1, 4, 7, 10}
        or policy.interval == "semiannually"
        and local.month in {1, 7}
        or policy.interval == "yearly"
        and local.month == 1
    )


def invocation_times(window: Window) -> list[datetime]:
    at, end = timestamp(window.start), timestamp(window.end)
    times = []
    while at < end:
        times.append(at)
        at += timedelta(minutes=window.invocationMinutes)
    return times


def forecast_variant(inputs: ForecastInput, variant: Variant) -> Result:
    prs = [
        p
        for p in inputs.platform.pullRequests
        if p.provider == variant.provider and p.base == inputs.platform.baseBranch
    ]
    open_prs = [p for p in prs if p.state == "open"]
    closed_prs = [p for p in prs if p.state == "closed"]
    policies = {(p.ecosystem, p.directory): p for p in variant.policies}
    complete = variant.proposalEvidenceComplete and variant.scan.status == "complete"
    decisions: dict[str, Decision] = {}
    pending: dict[str, tuple[Proposal, Policy]] = {}
    for proposal in variant.proposals:
        policy = policies.get((proposal.ecosystem, proposal.directory))
        reasons = []
        if not complete:
            reasons.append("Native proposal evidence is incomplete.")
        if not inputs.platform.complete:
            reasons.append("GitHub state is incomplete.")
        matched = [
            p for p in open_prs if proposal.branch is not None and p.branch == proposal.branch
        ]
        if len(matched) == 1 and not reasons:
            decisions[proposal.id] = Decision(
                proposal=proposal, state="existing", reason="Native branch matches an open bot PR."
            )
            continue
        if policy is None:
            reasons.append("No unique policy context.")
        else:
            reasons.extend(policy.unknown)
            error = schedule_error(policy)
            if error:
                reasons.append(error)
        if proposal.existingUpdate:
            reasons.append("Native event updates an existing PR, but its identity is not proven.")
        if len(matched) > 1:
            reasons.append("Ambiguous existing PR branch match.")
        if open_prs and not proposal.branch:
            reasons.append(
                "Cannot match native proposal to existing bot PRs without branch identity."
            )
        if variant.provider == "dependabot" and open_prs:
            reasons.append(
                "Existing Dependabot PRs cannot be allocated to job limit scopes safely."
            )
        if closed_prs:
            reasons.append("Closed/merged bot PR suppression history is not certified.")
        if reasons:
            decisions[proposal.id] = Decision(
                proposal=proposal, state="unknown", reason=" ".join(sorted(set(reasons)))
            )
        elif policy is not None:
            pending[proposal.id] = (proposal, policy)

    occupied: dict[tuple[str, str], int] = {}
    created: list[datetime] = []
    ever_due: set[str] = set()
    for at in invocation_times(inputs.window):
        for identity, (proposal, policy) in list(pending.items()):
            if not due(at, policy):
                continue
            ever_due.add(identity)
            scope = (policy.ecosystem, policy.directory)
            used = occupied.get(scope, len(open_prs) if variant.provider == "renovate" else 0)
            capped = used >= policy.openLimit
            # Renovate zero means unlimited; Dependabot zero disables version PR creation.
            if variant.provider == "renovate" and policy.openLimit == 0:
                capped = False
            hour_start = at.replace(minute=0, second=0, microsecond=0)
            recent = sum(t >= hour_start for t in created)
            recent += sum(hour_start <= timestamp(p.createdAt) <= at for p in prs)
            rate_capped = (
                policy.hourlyLimit is not None
                and policy.hourlyLimit != 0
                and recent >= policy.hourlyLimit
            )
            if capped or rate_capped:
                continue
            decisions[identity] = Decision(
                proposal=proposal,
                state="predicted-new",
                at=at.isoformat(),
                reason="Eligible in the fixed-state scenario under supported schedule/PR limits.",
            )
            occupied[scope] = used + 1
            created.append(at)
            del pending[identity]
    for identity, (proposal, _) in pending.items():
        decisions[identity] = Decision(
            proposal=proposal,
            state="queued" if identity in ever_due else "deferred",
            reason="Eligible proposal exceeds modeled open/rate capacity."
            if identity in ever_due
            else "No modeled eligible invocation inside the requested window.",
        )
    ordered = [decisions[p.id] for p in variant.proposals]
    unknown = sum(d.state == "unknown" for d in ordered)
    totals_known = (
        complete
        and inputs.platform.complete
        and not unknown
        and all(not p.unknown and schedule_error(p) is None for p in variant.policies)
    )
    counts = {
        state: sum(d.state == state for d in ordered)
        for state in ("predicted-new", "queued", "deferred", "existing")
    }
    ecosystem_decisions: dict[str, list[Decision]] = {}
    for decision in ordered:
        proposal = decision.proposal
        ecosystems = {proposal.ecosystem}
        if variant.provider == "renovate":
            ecosystems = {
                observation.dependency.ecosystem
                for observation in variant.scan.observations
                if any(
                    member.package == observation.dependency.package
                    and member.manifest == observation.dependency.manifestPath
                    for member in proposal.members
                )
            } or {"unknown"}
        for ecosystem in ecosystems:
            ecosystem_decisions.setdefault(ecosystem, []).append(decision)
    by_ecosystem = {
        ecosystem: {
            "observedPotential": len(items),
            "predictedNew": sum(d.state == "predicted-new" for d in items)
            if totals_known
            else None,
            "queued": sum(d.state == "queued" for d in items) if totals_known else None,
            "unknown": sum(d.state == "unknown" for d in items),
        }
        for ecosystem, items in sorted(ecosystem_decisions.items())
    }
    return Result(
        provider=variant.provider,
        label=variant.label,
        observedPotential=len(variant.proposals),
        potentialPRs=len(variant.proposals) if complete else None,
        predictedNew=counts["predicted-new"] if totals_known else None,
        queued=counts["queued"] if totals_known else None,
        deferred=counts["deferred"] if totals_known else None,
        existing=counts["existing"],
        unknown=unknown,
        decisions=ordered,
        byEcosystem=by_ecosystem,
        warnings=variant.warnings
        + inputs.platform.warnings
        + [
            "Known version-update demand only; security PR forecasts and future releases "
            "are unknown.",
            "Predicted creations remain open; no merge/closure/CI outcomes are invented.",
            "Invocation cadence is a declared scenario, not GitHub's guaranteed bot "
            "execution time.",
            "Local lookups cannot prove future newly eligible candidates suppressed at capture.",
            "Per-ecosystem proposal memberships can overlap for cross-ecosystem groups; "
            "do not sum them to obtain total PR demand.",
        ],
    )


def forecast(inputs: ForecastInput) -> dict[str, object]:
    results = [forecast_variant(inputs, v) for v in inputs.variants]
    deltas = {}
    comparison_warnings = []
    for provider in ("dependabot", "renovate"):
        baseline = next(r for r in results if r.provider == provider and r.label == "baseline")
        optimised = next(r for r in results if r.provider == provider and r.label == "optimised")
        base_scan = next(
            v.scan for v in inputs.variants if v.provider == provider and v.label == "baseline"
        )
        opt_scan = next(
            v.scan for v in inputs.variants if v.provider == provider and v.label == "optimised"
        )
        transports = [
            {
                key: value
                for key, value in scan.snapshot.configHashes.items()
                if key.startswith("gappy:")
            }
            for scan in (base_scan, opt_scan)
        ]
        comparable = (
            base_scan.toolchain == opt_scan.toolchain
            and base_scan.cacheMode == opt_scan.cacheMode
            and transports[0] == transports[1]
        )
        if not comparable:
            comparison_warnings.append(
                f"{provider.title()} baseline and optimised executions are not comparable: "
                "toolchain, cache mode or registry transport differs. Deltas are unknown."
            )
        deltas[provider] = {
            "potentialPRs": None
            if not comparable or baseline.potentialPRs is None or optimised.potentialPRs is None
            else optimised.potentialPRs - baseline.potentialPRs,
            "predictedNew": None
            if not comparable or baseline.predictedNew is None or optimised.predictedNew is None
            else optimised.predictedNew - baseline.predictedNew,
        }
    direct_dependencies: dict[tuple[str, str, str], dict[str, object]] = {}
    for variant in inputs.variants:
        column = f"{variant.provider}.{variant.label}"
        for observation in variant.scan.observations:
            if observation.directness != "direct":
                continue
            dep = observation.dependency
            key = dep.ecosystem, dep.package, dep.manifestPath
            row = direct_dependencies.setdefault(
                key,
                {
                    "ecosystem": dep.ecosystem,
                    "package": dep.package,
                    "manifest": dep.manifestPath,
                    "variants": {},
                },
            )
            sides = row["variants"]
            if not isinstance(sides, dict):
                raise TypeError("Expected configuration comparison columns.")
            previous = sides.setdefault(column, {"installed": [], "recommended": []})
            previous["installed"] = sorted(set(previous["installed"] + [dep.installedVersion]))
            previous["recommended"] = sorted(
                set(
                    previous["recommended"]
                    + [
                        f"{c.targetPackage}:{c.targetVersion}"
                        if c.targetPackage
                        else c.targetVersion
                        for c in observation.candidates
                    ]
                )
            )
            previous["configuration"] = [
                value
                for _, value in sorted(
                    {
                        (ref["id"], ref["basis"]): ref
                        for ref in previous.get("configuration", [])
                        + [ref.model_dump() for ref in observation.configuration]
                    }.items()
                )
            ]
    return {
        "schemaVersion": "1.0.0",
        "mode": "configuration-forecast",
        "repository": inputs.repository,
        "revision": inputs.revision,
        "sourceHash": inputs.sourceHash,
        "window": inputs.window.model_dump(),
        "capturedAt": inputs.platform.capturedAt,
        "results": [r.model_dump() for r in results],
        "dependencies": [direct_dependencies[k] for k in sorted(direct_dependencies)],
        "deltas": deltas,
        "comparisonWarnings": comparison_warnings,
        "scope": "Conditional forecast of currently observed native version-update PR demand; "
        "not actual GitHub PR creation or a forecast of unseen future updates.",
    }
