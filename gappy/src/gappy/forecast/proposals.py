"""Allowlisted proposal extraction; one grouped proposal is one unit of PR demand."""

import hashlib
from typing import Any

from gappy.forecast.models import Member, Proposal
from gappy.io import GappyError, dump
from gappy.scan.native import json_events


def extract(
    provider: str, ecosystem: str, directory: str, output: str
) -> tuple[list[Proposal], bool]:
    events = json_events(output)
    groups: list[tuple[list[Any], str | None, bool]] = []
    if provider == "dependabot":
        complete = any(e.get("type") == "mark_as_processed" for e in events) and not any(
            e.get("type") == "record_update_job_error" for e in events
        )
        for event in events:
            if event.get("type") in {"create_pull_request", "update_pull_request"}:
                data = event.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("dependencies"), list):
                    raise GappyError("Native PR proposal has unsupported membership evidence.")
                groups.append((data["dependencies"], None, event["type"] == "update_pull_request"))
    else:
        branches = [e for e in events if e.get("msg") == "branches"]
        complete = len(branches) == 1 and any(e.get("msg") == "Repository finished" for e in events)
        if branches:
            if len(branches) != 1 or not isinstance(branches[0].get("branches"), list):
                raise GappyError("Native branch proposal evidence is ambiguous.")
            for branch in branches[0]["branches"]:
                if not isinstance(branch, dict) or not isinstance(branch.get("upgrades"), list):
                    raise GappyError("Native branch membership is unsupported.")
                name = branch.get("branchName")
                groups.append((branch["upgrades"], name if isinstance(name, str) else None, False))
        complete = complete and not any(e.get("level", 0) >= 40 for e in events)
    proposals = {}
    for upgrades, branch, existing in groups:
        members = []
        for upgrade in upgrades:
            if not isinstance(upgrade, dict):
                raise GappyError("Native proposal member must be an object.")
            package = upgrade.get("name" if provider == "dependabot" else "depName")
            target = (
                upgrade.get("version")
                if provider == "dependabot"
                else (
                    upgrade.get("newVersion") or upgrade.get("newValue") or upgrade.get("newDigest")
                )
            )
            if not isinstance(package, str) or not isinstance(target, str):
                raise GappyError("Native proposal package/target evidence is missing.")
            manifest = upgrade.get("packageFile", directory)
            if not isinstance(manifest, str):
                raise GappyError("Native proposal manifest evidence is unsupported.")
            members.append(Member(package=package, target=target, manifest=manifest))
        if not members:
            continue
        members.sort(key=lambda m: (m.package, m.manifest, m.target))
        identity = hashlib.sha256(
            dump(
                [provider, ecosystem, directory, branch, [m.model_dump() for m in members]]
            ).encode()
        ).hexdigest()
        proposals[identity] = Proposal(
            id=identity,
            ecosystem=ecosystem,
            directory=directory,
            branch=branch,
            members=members,
            existingUpdate=existing,
        )
    return list(proposals.values()), complete
