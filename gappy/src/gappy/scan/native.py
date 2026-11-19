"""Version-specific adapters for native structured scanner observations."""

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from gappy.contracts import Candidate, Dependency, Directness, Observation, Policy, Provider
from gappy.io import GappyError
from gappy.scan.ecosystems import canonical_package
from gappy.scan.ecosystems import ecosystem as canonical_ecosystem
from gappy.scan.metadata import renovate_references
from gappy.versions import pinned_digest

DEPENDABOT_CLI_TIMEOUT = re.compile(
    r"(?m)^[ \t]*cli[ \t]*\|[ \t]*[0-9]{4}/[0-9]{2}/[0-9]{2} "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} update timed out after "
    r"((?:[0-9]{1,4}(?:\.[0-9]{1,9})?(?:h|m|s)){1,3})[ \t]*\r?$"
)
DEPENDABOT_SOURCE_TIMEOUT = (
    "Dependabot dependency source timed out (private_source_timed_out). "
    "This can also represent a native registry-client failure. Check "
    "connectivity and corporate TLS trust; use --source-ca with your "
    "approved CA if HTTPS inspection is enabled. Increasing retries or "
    "supplying a GitHub token does not repair missing CA trust."
)
RENOVATE_SOURCE_TIMEOUT = (
    "Renovate reported a dependency source timeout (ETIMEDOUT); subsequent retries "
    "may succeed. Check the final scan status and source availability."
)


def manifests(config: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for manager, values in config.items():
        if not isinstance(values, list):
            raise GappyError("Renovate manager results must be an array.")
        for manifest in values:
            if not isinstance(manifest, dict) or not isinstance(manifest.get("deps"), list):
                raise GappyError("Renovate dependency extraction format is unsupported.")
            yield manager, manifest


def merge_observations(observations: list[Observation]) -> list[Observation]:
    merged: dict[tuple[tuple[str, str, str, str, str, str | None], Directness], Observation] = {}
    for item in observations:
        key = (item.dependency.key(), item.directness)
        previous = merged.get(key)
        if previous is None:
            merged[key] = item
        else:
            consistent = previous.policy == item.policy
            merged[key] = previous.model_copy(
                update={
                    "candidates": list(
                        {
                            (candidate.targetPackage, candidate.targetVersion): candidate
                            for candidate in previous.candidates + item.candidates
                        }.values()
                    ),
                    "lookupComplete": previous.lookupComplete
                    and item.lookupComplete
                    and consistent,
                    "policy": previous.policy if consistent else "unknown",
                    "reason": "; ".join(sorted({previous.reason, item.reason})),
                    "configuration": list(
                        {
                            (ref.id, ref.basis): ref
                            for ref in previous.configuration + item.configuration
                        }.values()
                    ),
                }
            )
    return list(merged.values())


def renovate_directness(manager: str, item: dict[str, Any]) -> Directness:
    dep_type = item.get("depType")
    if dep_type is not None and not isinstance(dep_type, str):
        raise GappyError("Renovate dependency type is malformed.")
    if dep_type in {"indirect", "transitive"} or item.get("isTransitive") is True:
        return "transitive"
    if manager == "npm" and dep_type in {"overrides", "resolutions", "pnpm.overrides"}:
        return "unknown"
    return "direct"


def registry_identity(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if parsed.port is not None:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def json_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raise GappyError("Native scanner emitted malformed structured JSON.") from None
        if not isinstance(event, dict):
            raise GappyError("Native structured record must be an object.")
        events.append(event)
    return events


def renovate_source_timed_out(event: dict[str, Any]) -> bool:
    error = event.get("err")
    return isinstance(error, dict) and error.get("code") == "ETIMEDOUT"


def renovate_timeout_contexts(text: str) -> list[tuple[str | None, bool]]:
    contexts: set[tuple[str | None, bool]] = set()
    for event in json_events(text):
        if not renovate_source_timed_out(event):
            continue
        path = event.get("packageFile")
        if path is None:
            contexts.add((None, False))
        elif not isinstance(path, str) or path == "." or len(path) > 4096:
            contexts.add((None, True))
        else:
            try:
                contexts.add((Dependency.safe_path(path), False))
            except ValueError:
                contexts.add((None, True))
    return sorted(contexts, key=lambda context: (context[0] is None, context[0] or "", context[1]))


def capture_renovate_line(line: str) -> bool:
    events = json_events(line)
    if not events:
        return False
    event = events[0]
    return (
        event.get("level", 0) >= 40
        or renovate_source_timed_out(event)
        or event.get("msg") in {"Repository finished", "packageFiles with updates", "branches"}
    )


def source_timed_out(text: str) -> bool:
    errors = [
        event.get("data")
        for event in json_events(text)
        if event.get("type") == "record_update_job_error"
    ]
    return bool(errors) and all(
        isinstance(error, dict) and error.get("error-type") == "private_source_timed_out"
        for error in errors
    )


def dependabot_launcher_timeout(text: str) -> str | None:
    timeout = DEPENDABOT_CLI_TIMEOUT.search(text)
    if timeout is None:
        return None
    return (
        f"Dependabot CLI reached its native job timeout after {timeout.group(1)}. "
        "Check dependency source availability, then consider increasing "
        "--timeout-seconds; results remain incomplete."
    )


def diagnostics(provider: Provider, text: str) -> list[str]:
    messages = set()
    if provider == "dependabot":
        timeout = dependabot_launcher_timeout(text)
        if timeout:
            messages.add(timeout)
    if (
        provider == "dependabot"
        and (
            "for security, credentials used in update are not allowed to have write access "
            "to GitHub API"
        )
        in text
    ):
        messages.add(
            "Dependabot CLI rejected a write-capable GitHub token. "
            "Use a least-privilege read-only token; do not broaden its scopes."
        )
    for event in json_events(text):
        if provider == "dependabot" and event.get("type") == "record_update_job_warning":
            data = event.get("data")
            if isinstance(data, dict) and data.get("warn-type") == "cooldown_date_unavailable":
                messages.add(
                    "Dependabot could not enforce cooldown for some dependencies because "
                    "the registry supplied no usable publication date "
                    "(cooldown_date_unavailable). Recommendations may include recent releases."
                )
        if provider == "dependabot" and event.get("type") == "record_update_job_error":
            data = event.get("data")
            error = data.get("error-type") if isinstance(data, dict) else None
            messages.add(
                {
                    "private_source_authentication_failure": (
                        "Dependabot could not authenticate to a dependency source "
                        "(private_source_authentication_failure). "
                        "Check source permissions; for github.com use --github-token-env NAME. "
                        "Other private registries require separate credentials."
                    ),
                    "private_source_timed_out": DEPENDABOT_SOURCE_TIMEOUT,
                    "dependency_file_not_found": (
                        "Dependabot could not find a configured dependency file "
                        "(dependency_file_not_found). Check the configured directory."
                    ),
                    "dependency_file_not_resolvable": (
                        "Dependabot could not resolve a dependency file "
                        "(dependency_file_not_resolvable)."
                    ),
                    "unexpected_external_code": (
                        "Dependabot blocked external code execution (unexpected_external_code). "
                        "The native pip parser requires external-code permission even for "
                        "requirements files; Gappy keeps reject-external-code enabled."
                    ),
                }.get(
                    error if isinstance(error, str) else "",
                    (
                        "Dependabot reported an update job error; "
                        "raw details withheld to protect secrets."
                    ),
                )
            )
        elif provider == "renovate":
            if renovate_source_timed_out(event):
                messages.add(RENOVATE_SOURCE_TIMEOUT)
            if event.get("level", 0) >= 40:
                message = event.get("msg")
                messages.add(
                    {
                        "GitHub token is required for some dependencies": (
                            "Renovate requires a GitHub token for some dependencies. "
                            "Use --github-token-env NAME, or check the selected token's access "
                            "and limits."
                        ),
                        "github.com token 401 unauthorized": (
                            "Renovate's GitHub token was rejected (HTTP 401 Unauthorized). "
                            "Check that the environment variable selected by --github-token-env "
                            "contains a valid, unexpired token. Do not broaden its scopes."
                        ),
                        "Repository has invalid config": (
                            "Renovate could not resolve or validate the repository configuration. "
                            "Check the selected local Renovate configuration and its extends "
                            "presets. A failed GitHub authentication can prevent preset "
                            "resolution; address any accompanying authentication warning first. "
                            "Raw configuration details withheld to protect secrets."
                        ),
                    }.get(
                        message if isinstance(message, str) else "",
                        "Renovate emitted a native warning or error; "
                        "lookup completeness is uncertain. "
                        "Raw details withheld to protect secrets.",
                    )
                )
    return sorted(messages)


def renovate(
    text: str,
    *,
    registry: str | None = None,
    policy_filtered: bool = False,
    require_branches: bool = False,
    configuration: dict[str, Any] | None = None,
) -> tuple[list[Observation], bool]:
    events = json_events(text)
    finished = any(event.get("msg") == "Repository finished" for event in events)
    errors = any(event.get("level", 0) >= 40 for event in events)
    batches = [event for event in events if event.get("msg") == "packageFiles with updates"]
    if len(batches) != 1:
        raise GappyError("Renovate lookup evidence is absent or has an unsupported structure.")
    config = batches[0].get("config")
    if not isinstance(config, dict):
        raise GappyError("Renovate package-file lookup format is unsupported.")
    branch_events = [event for event in events if event.get("msg") == "branches"]
    if require_branches and len(branch_events) != 1:
        raise GappyError("Renovate post-policy branch evidence is missing or ambiguous.")
    actionable: set[tuple[str, str, str]] | None = None
    if branch_events:
        if len(branch_events) != 1 or not isinstance(branch_events[0].get("branches"), list):
            raise GappyError("Renovate post-policy branch format is unsupported.")
        actionable = set()
        for branch in branch_events[0]["branches"]:
            if not isinstance(branch, dict) or not isinstance(branch.get("upgrades"), list):
                raise GappyError("Renovate post-policy upgrades are malformed.")
            for upgrade in branch["upgrades"]:
                if not isinstance(upgrade, dict):
                    raise GappyError("Renovate post-policy upgrade must be an object.")
                path, name, target = (
                    upgrade.get("packageFile"),
                    upgrade.get("depName"),
                    upgrade.get("newVersion")
                    or upgrade.get("newValue")
                    or upgrade.get("newDigest"),
                )
                if isinstance(path, str) and isinstance(name, str) and isinstance(target, str):
                    if not upgrade.get("pendingChecks"):
                        actionable.add((path, name, target))
    observations: list[Observation] = []
    for manager, manifest in manifests(config):
        if not isinstance(manifest, dict) or not isinstance(manifest.get("deps"), list):
            raise GappyError("Renovate dependency extraction format is unsupported.")
        path = manifest.get("packageFile")
        if not isinstance(path, str):
            raise GappyError("Renovate manifest context is missing.")
        for item in manifest["deps"]:
            if not isinstance(item, dict):
                raise GappyError("Renovate dependency result must be an object.")
            version = (
                item.get("currentVersion")
                or item.get("currentValue")
                or item.get("currentDigest")
                or "(not reported)"
            )
            package = item.get("packageName") or item.get("depName")
            updates = item.get("updates", [])
            if not isinstance(package, str) or not isinstance(updates, list):
                raise GappyError("Renovate dependency identity or update list is malformed.")
            if not all(
                isinstance(update, dict)
                and isinstance(
                    update.get("newVersion") or update.get("newValue") or update.get("newDigest"),
                    str,
                )
                for update in updates
            ):
                raise GappyError("Renovate candidate format is unsupported.")
            safe = not item.get("skipReason") and not item.get("warnings") and not errors
            excluded = item.get("skipReason") in {"disabled", "ignored"}
            deferred = bool(updates) and all(
                (update.get("isPending") or update.get("pendingChecks"))
                and not update.get("isSkipped")
                for update in updates
            )
            candidates = []
            for i, update in enumerate(updates):
                target = (
                    update.get("newVersion") or update.get("newValue") or update.get("newDigest")
                )
                if not update.get("isSkipped") and (
                    (deferred and not excluded)
                    or (
                        not update.get("isPending")
                        and not update.get("pendingChecks")
                        and (
                            actionable is None or (path, item.get("depName"), target) in actionable
                        )
                    )
                ):
                    candidates.append(
                        Candidate(
                            targetVersion=target,
                            targetPackage=update.get("newName"),
                            evidenceId=f"renovate-{len(observations)}-{i}",
                        )
                    )
            uncertain = policy_filtered and not candidates and not excluded and not deferred
            policy: Policy = (
                "excluded"
                if excluded
                else "deferred"
                if deferred
                else "eligible"
                if safe and not uncertain
                else "unknown"
            )
            ecosystem = canonical_ecosystem(manager, item.get("datasource"))
            installed = (
                pinned_digest(ecosystem, item.get("currentDigest"))
                or pinned_digest(ecosystem, item.get("currentValue"))
                or version
            )
            observations.append(
                Observation(
                    dependency=Dependency(
                        ecosystem=ecosystem,
                        package=canonical_package(ecosystem, package),
                        manifestPath=path,
                        installedVersion=installed,
                        registry=registry_identity(item.get("registryUrl"))
                        or (registry if ecosystem == "npm" else None),
                        versioning="semver"
                        if ecosystem == "npm"
                        else str(item.get("versioning") or "native"),
                    ),
                    directness=renovate_directness(manager, item),
                    policy=policy,
                    reason=(
                        "Native configuration disabled or ignored this dependency."
                        if excluded
                        else "Native update is pending minimum-release-age checks."
                        if deferred
                        else "Filtered lookup has no actionable proposal; absence is not a miss."
                        if uncertain
                        else "Native lookup after resolved repository configuration and rules."
                    )
                    + (
                        f" Native reported version: {version}; "
                        "installed identity uses its digest pin."
                        if installed != version
                        else ""
                    ),
                    lookupComplete=safe and finished and not uncertain,
                    candidates=candidates,
                    configuration=renovate_references(configuration or {}, manager, item),
                )
            )
    return merge_observations(observations), finished and not errors


DEPENDABOT_COOLDOWN_LOG = re.compile(
    r"Filtered out \d+ versions?(?:\(s\))? due to cooldown"
    r"|All versions filtered by cooldown for "
    r"|Skipping (?:tag|version) \S+ due to cooldown period"
)


def dependabot_cooldown_filtered(text: str) -> bool:
    """Whether native updater logs report releases withheld by cooldown."""
    for line in text.splitlines():
        log = re.fullmatch(
            r"updater\s+\| \d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} INFO (.*)",
            line.strip(),
        )
        if log and DEPENDABOT_COOLDOWN_LOG.search(log[1]):
            return True
    return False


def dependabot_docker_no_updates(text: str) -> set[tuple[str, str]]:
    confirmed: set[tuple[str, str]] = set()
    current: tuple[str, str] | None = None
    latest: str | None = None
    blocked = False
    for line in text.splitlines():
        log = re.fullmatch(
            r"updater\s+\| \d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} (INFO|WARN|ERROR) (.*)",
            line.strip(),
        )
        if not log:
            continue
        level, message = log.groups()
        checking = re.fullmatch(r"Checking if (\S+) (\S+) needs updating", message)
        if level == "INFO" and checking:
            current = (canonical_package("docker", checking[1]), checking[2])
            confirmed.discard(current)
            latest, blocked = None, False
        elif current is not None:
            if (
                level != "INFO"
                or re.fullmatch(r"Skipping tag .+ due to cooldown period", message)
                or message.startswith("All updates for ")
            ):
                blocked = True
                confirmed.discard(current)
            elif message.startswith("Latest version is "):
                latest = message.removeprefix("Latest version is ")
            elif message == f"No update needed for {current[0]} {current[1]}":
                if not blocked and latest == current[1]:
                    confirmed.add(current)
    return confirmed


def dependabot(
    text: str,
    directory: str,
    registry: str | None,
    *,
    evidence_prefix: str = "dependabot",
    ecosystem: str = "npm",
) -> tuple[list[Observation], bool]:
    events = json_events(text)
    complete = any(event.get("type") == "mark_as_processed" for event in events)
    ecosystem = canonical_ecosystem(ecosystem, None)
    failed = any(event.get("type") == "record_update_job_error" for event in events)
    dependencies: list[dict[str, Any]] = []
    proposals: dict[str, list[str]] = {}
    discovered = False
    for event in events:
        if event.get("type") not in {
            "update_dependency_list",
            "create_pull_request",
            "update_pull_request",
            "mark_as_processed",
            "record_update_job_error",
        }:
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            raise GappyError("Dependabot event data has an unsupported format.")
        if event.get("type") == "update_dependency_list":
            extracted = data.get("dependencies", [])
            if not isinstance(extracted, list) or not all(
                isinstance(item, dict) for item in extracted
            ):
                raise GappyError("Dependabot extraction format is unsupported.")
            dependencies.extend(extracted)
            discovered = True
        if event.get("type") in {"create_pull_request", "update_pull_request"}:
            updates = data.get("dependencies", [])
            if not isinstance(updates, list):
                raise GappyError("Dependabot candidate list is malformed.")
            for dependency in updates:
                if not isinstance(dependency, dict) or not isinstance(dependency.get("name"), str):
                    raise GappyError("Dependabot candidate identity is missing.")
                if isinstance(dependency.get("version"), str):
                    proposals.setdefault(dependency["name"], []).append(dependency["version"])
                else:
                    raise GappyError("Dependabot candidate version is unsupported.")
    observations: list[Observation] = []
    for item in dependencies:
        version = item.get("version")
        if not isinstance(version, str):
            version = "(not reported)"
        requirements = item.get("requirements", [])
        if (
            not isinstance(item.get("name"), str)
            or not isinstance(requirements, list)
            or not all(isinstance(entry, dict) for entry in requirements)
        ):
            raise GappyError("Dependabot dependency context is malformed.")
        contexts = set()
        for entry in requirements:
            if not isinstance(entry.get("file"), str):
                continue
            source = entry.get("source")
            ref = (
                source.get("ref" if ecosystem == "github-actions" else "digest")
                if isinstance(source, dict)
                else None
            )
            if (
                ecosystem == "docker"
                and isinstance(ref, str)
                and re.fullmatch(r"[a-fA-F0-9]{64}", ref)
            ):
                ref = "sha256:" + ref
            pin = pinned_digest(ecosystem, ref)
            contexts.add((entry["file"], pin or version))
        directness: Directness = (
            "unknown" if "requirements" not in item else "direct" if requirements else "transitive"
        )
        targets = sorted(set(proposals.get(item["name"], [])))
        for path, installed in sorted(contexts) or [(".", version)]:
            prefix = directory.strip("/")
            path = path.lstrip("/")
            manifest = path if not prefix or path.startswith(prefix + "/") else prefix + "/" + path
            if manifest.endswith("/."):
                manifest = manifest[:-2]
            observations.append(
                Observation(
                    dependency=Dependency(
                        ecosystem=ecosystem,
                        package=canonical_package(ecosystem, item["name"]),
                        manifestPath=manifest,
                        installedVersion=installed,
                        registry=registry,
                        versioning="semver" if ecosystem == "npm" else "native",
                    ),
                    directness=directness,
                    policy="eligible",
                    reason="Native update job after repository policy translation."
                    + (
                        f" Native reported version: {version}; "
                        "installed identity uses its digest pin."
                        if installed != version
                        else ""
                    ),
                    lookupComplete=complete and not failed,
                    candidates=[
                        Candidate(
                            targetVersion=target,
                            evidenceId=f"{evidence_prefix}-{len(observations)}-{i}",
                        )
                        for i, target in enumerate(targets)
                    ],
                )
            )
    if not complete and not failed and not discovered:
        raise GappyError("Dependabot completion evidence is missing.")
    return merge_observations(observations), complete and not failed
