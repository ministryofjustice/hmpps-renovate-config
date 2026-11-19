"""Translate local Dependabot configuration into explicit native updater jobs."""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

from gappy.contracts import Candidate, ConfigurationReference, Observation, Snapshot
from gappy.io import GappyError
from gappy.scan.ecosystems import DEPENDABOT
from gappy.scan.metadata import references


@dataclass(frozen=True)
class Job:
    directory: str
    ecosystem: str
    definition: dict[str, Any]
    ignores: list[dict[str, Any]]
    filtered: bool
    patterns: list[str] | None = None
    exclude_paths: list[str] = field(default_factory=list)
    configuration: list[ConfigurationReference] = field(default_factory=list)
    policy_keys: frozenset[str] = frozenset()

    def policy(
        self,
        observations: list[Observation],
        *,
        docker_no_updates: set[tuple[str, str]] | None = None,
        cooldown_probe: dict[tuple[str, str, str], list[Candidate]] | None = None,
    ) -> list[Observation]:
        """Apply local policy; ``cooldown_probe`` holds proposals from the same job without
        cooldown, keyed by package, manifest and installed version."""
        result = []
        for item in observations:
            if any(
                fnmatchcase(item.dependency.manifestPath, pattern) for pattern in self.exclude_paths
            ):
                continue
            item = item.model_copy(update={"configuration": self.configuration})
            if self.patterns is not None and not any(
                fnmatchcase(item.dependency.package, pattern) for pattern in self.patterns
            ):
                result.append(
                    item.model_copy(
                        update={
                            "policy": "excluded",
                            "candidates": [],
                            "reason": item.reason + " Outside multi-ecosystem dependency patterns.",
                        }
                    )
                )
                continue
            if item.candidates:
                result.append(item)
                continue
            identity = (
                item.dependency.package,
                item.dependency.manifestPath,
                item.dependency.installedVersion,
            )
            deferred = (cooldown_probe or {}).get(identity, [])
            if deferred:
                targets = ", ".join(candidate.targetVersion for candidate in deferred)
                result.append(
                    item.model_copy(
                        update={
                            "policy": "deferred",
                            "candidates": deferred,
                            "reason": item.reason
                            + " Native cooldown withheld newer releases; the same native job "
                            f"without cooldown proposes {targets}. Not yet actionable.",
                        }
                    )
                )
                continue
            rules = [
                rule
                for rule in self.ignores
                if fnmatchcase(item.dependency.package, rule["dependency-name"])
            ]
            excluded = any(
                not rule.get("versions")
                and (
                    not rule.get("update-types")
                    or "version-update:semver-patch" in rule["update-types"]
                )
                for rule in rules
            )
            if excluded:
                item = item.model_copy(
                    update={
                        "policy": "excluded",
                        "reason": item.reason
                        + " Local Dependabot ignore rule excludes patch updates.",
                    }
                )
            elif any(rule.get("versions") for rule in rules):
                item = item.model_copy(
                    update={
                        "policy": "unknown",
                        "lookupComplete": False,
                        "reason": item.reason
                        + " Version-specific ignore needs per-opportunity policy evidence.",
                    }
                )
            elif (
                self.ecosystem in {"docker", "docker-compose"}
                and item.lookupComplete
                and (item.dependency.package, item.dependency.installedVersion)
                in (docker_no_updates or set())
            ):
                item = item.model_copy(
                    update={
                        "reason": item.reason
                        + " Native Docker checker explicitly confirmed no update needed; "
                        "latest eligible tag matches the installed version.",
                    }
                )
            elif (
                cooldown_probe is not None
                and "allow" not in self.policy_keys
                and item.lookupComplete
            ):
                item = item.model_copy(
                    update={
                        "reason": item.reason
                        + " The same native job without cooldown also proposes no update."
                    }
                )
            elif self.filtered and not item.candidates:
                item = item.model_copy(
                    update={
                        "policy": "unknown",
                        "lookupComplete": False,
                        "reason": item.reason
                        + " No proposal; cooldown/allow filtering is not a proven miss.",
                    }
                )
            result.append(item)
        return result


def mapping(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise GappyError(message)
    return value


def string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def allow_rules(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise GappyError("Dependabot allow must be a nonempty array.")
    result = []
    for item in value:
        rule = mapping(item, "Dependabot allow rule must be a mapping.")
        if (
            set(rule) - {"dependency-name", "dependency-type"}
            or not rule
            or any(not isinstance(val, str) for val in rule.values())
            or rule.get("dependency-type", "all")
            not in {"all", "direct", "indirect", "production", "development"}
        ):
            raise GappyError("Unsupported Dependabot allow rule.")
        result.append(rule)
    return result


def grouped_entry(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    if "multi-ecosystem-group" not in entry:
        if "patterns" in entry:
            raise GappyError("Entry patterns require a multi-ecosystem-group.")
        return entry
    name = entry["multi-ecosystem-group"]
    groups = mapping(
        config.get("multi-ecosystem-groups"), "Multi-ecosystem groups must be a mapping."
    )
    if not isinstance(name, str) or not name or name not in groups:
        raise GappyError("Multi-ecosystem group reference must name a defined group.")
    group = mapping(groups[name], "Multi-ecosystem group must be a mapping.")
    if set(group) - {"schedule", "labels", "assignees", "milestone", "open-pull-requests-limit"}:
        raise GappyError("Multi-ecosystem group contains unsupported policy settings.")
    for key in ("labels", "assignees"):
        if key in group and not string_list(group[key]):
            raise GappyError("Multi-ecosystem labels and assignees must be string arrays.")
    if "milestone" in group and (type(group["milestone"]) is not int or group["milestone"] < 1):
        raise GappyError("Multi-ecosystem milestone must be a positive integer.")
    patterns = entry.get("patterns")
    if not string_list(patterns) or not patterns or any(not p for p in patterns):
        raise GappyError("Multi-ecosystem entries require nonempty dependency patterns.")
    resolved = {
        key: value
        for key, value in entry.items()
        if key not in {"multi-ecosystem-group", "patterns"}
    }
    resolved["schedule"] = group.get("schedule")
    if "labels" in group or "labels" in entry:
        entry_labels = entry.get("labels", [])
        if not string_list(entry_labels):
            raise GappyError("Dependabot labels must be a string array.")
        resolved["labels"] = list(dict.fromkeys([*group.get("labels", []), *entry_labels]))
    if "open-pull-requests-limit" in group:
        resolved["open-pull-requests-limit"] = group["open-pull-requests-limit"]
    allow = allow_rules(entry.get("allow", [{"dependency-type": "direct"}]))
    scoped = []
    for rule in allow:
        allowed_name = rule.get("dependency-name", "*")
        for pattern in patterns:
            if pattern == "*" or pattern == allowed_name:
                scoped.append({**rule, "dependency-name": allowed_name})
            elif allowed_name == "*":
                scoped.append({**rule, "dependency-name": pattern})
            else:
                raise GappyError(
                    "Intersecting multi-ecosystem patterns and named allow rules is unsupported."
                )
    resolved["allow"] = scoped
    return resolved


def jobs(config: object, snapshot: Snapshot, repository: str) -> tuple[list[Job], list[str]]:
    config = mapping(config, "Dependabot configuration must be a version 2 mapping.")
    if config.get("version") != 2 or set(config) - {"version", "updates", "multi-ecosystem-groups"}:
        raise GappyError("Unsupported Dependabot top-level settings or config version.")
    if "multi-ecosystem-groups" in config:
        mapping(config["multi-ecosystem-groups"], "Multi-ecosystem groups must be a mapping.")
    entries = config.get("updates")
    if not isinstance(entries, list) or not entries:
        raise GappyError("Dependabot updates must be a nonempty array.")
    result: list[Job] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(entries):
        try:
            entry = mapping(value, "Dependabot update entry must be a mapping.")
            patterns = entry.get("patterns") if "multi-ecosystem-group" in entry else None
            filtered = bool(entry.get("cooldown") or entry.get("allow"))
            entry = grouped_entry(config, entry)
            ecosystem = entry.get("package-ecosystem")
            if not isinstance(ecosystem, str) or ecosystem not in DEPENDABOT:
                raise GappyError("Unknown ecosystem; this entry was not scanned.")
            supported = {
                "package-ecosystem",
                "directory",
                "directories",
                "schedule",
                "groups",
                "exclude-paths",
                "ignore",
                "cooldown",
                "allow",
                "versioning-strategy",
                "open-pull-requests-limit",
                "labels",
                "commit-message",
            }
            if unsupported := set(entry) - supported:
                raise GappyError(
                    "Update entry contains unsupported policy settings: "
                    + ", ".join(sorted(unsupported))
                    + "."
                )
            metadata = references(entry.get("labels"), "dependabot-entry")
            directories = entry.get("directories", [entry.get("directory")])
            if "directories" in entry and "directory" in entry:
                raise GappyError("Use directory or directories, not both.")
            if not string_list(directories) or not directories:
                raise GappyError("Explicit scan directories are required.")
            schedule = mapping(entry.get("schedule"), "Dependabot schedule must be a mapping.")
            if schedule.get("interval") not in {
                "daily",
                "weekly",
                "monthly",
                "quarterly",
                "semiannually",
                "yearly",
                "cron",
            }:
                raise GappyError("Dependabot schedule interval is invalid.")
            definition: dict[str, Any] = {
                "package-manager": DEPENDABOT[ecosystem][0],
                # The native API defaults to all dependencies, unlike GitHub's config API.
                "allowed-updates": [{"update-type": "all", "dependency-type": "direct"}],
                "reject-external-code": True,
            }
            if ecosystem == "npm":
                definition["experiments"] = {"dealias-packages": True}
            if "commit-message" in entry:
                commit = mapping(entry["commit-message"], "Commit message must be a mapping.")
                if (
                    set(commit) - {"prefix", "prefix-development", "include"}
                    or any(
                        not isinstance(commit[key], str)
                        for key in ("prefix", "prefix-development")
                        if key in commit
                    )
                    or ("include" in commit and commit["include"] != "scope")
                ):
                    raise GappyError("Unsupported Dependabot commit-message settings.")
                definition["commit-message-options"] = {
                    key: value for key, value in commit.items() if key != "include"
                }
                if "include" in commit:
                    definition["commit-message-options"]["include-scope"] = True
            if "open-pull-requests-limit" in entry and (
                type(entry["open-pull-requests-limit"]) is not int
                or entry["open-pull-requests-limit"] < 0
            ):
                raise GappyError(
                    "Dependabot open-pull-requests-limit must be a nonnegative integer."
                )
            ignores = entry.get("ignore", [])
            if not isinstance(ignores, list):
                raise GappyError("Dependabot ignore must be an array.")
            exclude_paths = entry.get("exclude-paths", [])
            if not string_list(exclude_paths) or any(not path for path in exclude_paths):
                raise GappyError(
                    "Dependabot exclude-paths must be an array of nonempty strings."
                )
            conditions = []
            for value in ignores:
                rule = mapping(value, "Dependabot ignore rule must be a mapping.")
                if (
                    set(rule) - {"dependency-name", "versions", "update-types"}
                    or not isinstance(rule.get("dependency-name"), str)
                    or not string_list(rule.get("versions", []))
                    or not string_list(rule.get("update-types", []))
                    or any(
                        t
                        not in {
                            "version-update:semver-major",
                            "version-update:semver-minor",
                            "version-update:semver-patch",
                        }
                        for t in rule.get("update-types", [])
                    )
                ):
                    raise GappyError("Unsupported Dependabot ignore rule.")
                base = {"dependency-name": rule["dependency-name"]}
                if rule.get("update-types"):
                    base["update-types"] = rule["update-types"]
                for version in rule.get("versions") or [None]:
                    conditions.append(
                        {**base, **({"version-requirement": version} if version else {})}
                    )
            if conditions:
                definition["ignore-conditions"] = conditions
            groups = mapping(entry.get("groups", {}), "Dependabot groups must be a mapping.")
            translated_groups = []
            for name, value in groups.items():
                rule = mapping(value, "Dependabot group must be a mapping.")
                if (
                    set(rule)
                    - {
                        "patterns",
                        "exclude-patterns",
                        "dependency-type",
                        "update-types",
                        "applies-to",
                    }
                    or any(
                        not string_list(rule[key])
                        for key in ("patterns", "exclude-patterns", "update-types")
                        if key in rule
                    )
                    or any(
                        t not in {"major", "minor", "patch"} for t in rule.get("update-types", [])
                    )
                    or rule.get("dependency-type", "production")
                    not in {"production", "development"}
                    or rule.get("applies-to", "version-updates") != "version-updates"
                ):
                    raise GappyError("Unsupported Dependabot group rule.")
                translated_groups.append(
                    {
                        "name": name,
                        "applies-to": "version-updates",
                        "rules": {key: val for key, val in rule.items() if key != "applies-to"},
                    }
                )
            if translated_groups:
                definition["dependency-groups"] = translated_groups
            if "cooldown" in entry:
                cooldown = mapping(entry["cooldown"], "Cooldown must be a mapping.")
                days = {
                    "default-days",
                    "semver-major-days",
                    "semver-minor-days",
                    "semver-patch-days",
                }
                if (
                    set(cooldown) - days - {"include", "exclude"}
                    or any(
                        type(cooldown[key]) is not int or cooldown[key] < 0
                        for key in days & cooldown.keys()
                    )
                    or any(
                        not string_list(cooldown[key])
                        for key in ("include", "exclude")
                        if key in cooldown
                    )
                ):
                    raise GappyError("Unsupported Dependabot cooldown options.")
                definition["cooldown"] = cooldown
            if "allow" in entry:
                allow = allow_rules(entry["allow"])
                definition["allowed-updates"] = [
                    {"dependency-type": "direct", **rule} for rule in allow
                ]
            if "versioning-strategy" in entry:
                strategy = entry["versioning-strategy"]
                if strategy not in {
                    "auto",
                    "increase",
                    "increase-if-necessary",
                    "lockfile-only",
                    "widen",
                }:
                    raise GappyError("Unsupported Dependabot versioning strategy.")
                definition["requirements-update-strategy"] = strategy
            entry_jobs = []
            for directory in directories:
                path = PurePosixPath(directory)
                if (
                    not directory.startswith("/")
                    or ".." in path.parts
                    or str(path) != directory
                    or any(c in directory for c in "*?[\\")
                    or (ecosystem, directory) in seen
                ):
                    raise GappyError("Scan directories must be unique normalized absolute paths.")
                entry_jobs.append(
                    Job(
                        directory=directory,
                        ecosystem=ecosystem,
                        definition={
                            **definition,
                            "source": {
                                "provider": "github",
                                "repo": repository,
                                "directory": directory,
                                "commit": snapshot.revision,
                            },
                        },
                        ignores=ignores,
                        filtered=filtered,
                        patterns=patterns,
                        exclude_paths=exclude_paths,
                        configuration=metadata,
                        policy_keys=frozenset({"cooldown", "allow"} & entry.keys()),
                    )
                )
            result.extend(entry_jobs)
            seen.update((job.ecosystem, job.directory) for job in entry_jobs)
        except GappyError as exc:
            warnings.append(f"Dependabot entry {index + 1}: {exc}")
    return result, warnings
