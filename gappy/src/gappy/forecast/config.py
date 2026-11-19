"""Resolve complete config variants without silently replacing repository policy."""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from gappy.contracts import Provider
from gappy.forecast.models import Policy
from gappy.io import GappyError
from gappy.scan.dependabot import grouped_entry

DEPENDABOT_PATHS = (".github/dependabot.yml", ".github/dependabot.yaml")
RENOVATE_PATHS = (
    "renovate.json",
    "renovate.json5",
    ".github/renovate.json",
    ".github/renovate.json5",
    ".gitlab/renovate.json",
    ".gitlab/renovate.json5",
    ".renovaterc",
    ".renovaterc.json",
    ".renovaterc.json5",
    "renovate.config.js",
    "renovate.config.cjs",
    ".renovaterc.js",
    ".renovaterc.cjs",
)


def load_config(path: Path, provider: Provider) -> dict[str, Any]:
    try:
        content = path.read_text()
        value = yaml.safe_load(content) if provider == "dependabot" else json.loads(content)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        raise GappyError(
            "Selected scanner config is unreadable or unsupported; no fallback applied."
        ) from None
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise GappyError("Selected scanner config must be an object.")
    return value


def baseline(root: Path, fallback: Path, provider: Provider) -> tuple[bytes, str]:
    paths = DEPENDABOT_PATHS if provider == "dependabot" else RENOVATE_PATHS
    found = [root / name for name in paths if (root / name).is_file()]
    embedded: object = None
    if provider == "renovate" and (root / "package.json").is_file():
        try:
            package = json.loads((root / "package.json").read_text())
        except (OSError, ValueError):
            raise GappyError(
                "Cannot inspect package.json for embedded Renovate configuration."
            ) from None
        if isinstance(package, dict) and "renovate" in package:
            embedded = package["renovate"]
            if not isinstance(embedded, dict):
                raise GappyError("Embedded Renovate configuration must be an object.")
    if len(found) + int(embedded is not None) > 1:
        raise GappyError(
            "Multiple repository scanner configurations are ambiguous; no fallback applied."
        )
    if embedded is not None:
        return json.dumps(embedded).encode(), "repository:package.json#renovate"
    if found:
        path = found[0]
        if path.suffix in {".js", ".cjs", ".json5"}:
            raise GappyError(
                "Repository JS/JSON5 config is not certified for forecasts; no fallback applied."
            )
        load_config(path, provider)
        return path.read_bytes(), "repository:" + path.relative_to(root).as_posix()
    name = "dependabot.yml" if provider == "dependabot" else "renovate.json"
    path = fallback / name
    load_config(path, provider)
    return path.read_bytes(), "fallback:" + str(path.resolve())


def resolve(
    root: Path, fallback: Path, optimised: Path, destination: Path
) -> dict[tuple[str, Provider], tuple[str, str, dict[str, Any]]]:
    result = {}
    for label in ("baseline", "optimised"):
        directory = destination / label
        directory.mkdir()
        for provider in ("dependabot", "renovate"):
            # Keep provider types explicit at this external-input boundary.
            typed: Provider = "dependabot" if provider == "dependabot" else "renovate"
            name = "dependabot.yml" if typed == "dependabot" else "renovate.json"
            if label == "baseline":
                content, origin = baseline(root, fallback, typed)
                (directory / name).write_bytes(content)
            else:
                path = optimised / name
                load_config(path, typed)
                shutil.copyfile(path, directory / name)
                content, origin = path.read_bytes(), "optimised:" + str(path.resolve())
            config = load_config(directory / name, typed)
            result[label, typed] = (origin, hashlib.sha256(content).hexdigest(), config)
    return result


def policies(provider: Provider, config: dict[str, Any]) -> list[Policy]:
    if provider == "dependabot":
        entries = config.get("updates")
        if not isinstance(entries, list):
            raise GappyError("Dependabot forecast requires an updates array.")
        result = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise GappyError("Dependabot update entry must be an object.")
            multi_ecosystem = "multi-ecosystem-group" in entry
            entry = grouped_entry(config, entry)
            schedule = entry.get("schedule", {})
            if not isinstance(schedule, dict):
                raise GappyError("Dependabot schedule must be an object.")
            directories = entry.get("directories", [entry.get("directory", "/")])
            if not isinstance(directories, list):
                raise GappyError("Dependabot directories must be an array.")
            unknown = []
            if multi_ecosystem:
                unknown.append(
                    "Cross-ecosystem PR consolidation and shared capacity are not certified."
                )
            if "time" not in schedule:
                unknown.append("Native service chooses an unspecified schedule time.")
            if entry.get("cooldown"):
                unknown.append(
                    "Future cooldown transitions are not proven by a current-time lookup."
                )
            if entry.get("target-branch"):
                unknown.append("Custom target-branch forecasts are not certified.")
            if set(schedule) - {"interval", "day", "time", "timezone"}:
                unknown.append("Schedule contains unsupported fields.")
            for directory in directories:
                result.append(
                    Policy(
                        ecosystem=entry.get("package-ecosystem", "unknown"),
                        directory=directory,
                        interval=schedule.get("interval", "unknown"),
                        day=schedule.get("day", "monday"),
                        time=schedule.get("time", "00:00"),
                        timezone=schedule.get("timezone", "UTC"),
                        openLimit=entry.get("open-pull-requests-limit", 5),
                        unknown=unknown,
                    )
                )
        return result
    certified_fields = {
        "$schema",
        "description",
        "enabled",
        "enabledManagers",
        "ignoreDeps",
        "ignorePaths",
        "branchPrefix",
        "rangeStrategy",
        "groupName",
        "groupSlug",
        "prConcurrentLimit",
        "prHourlyLimit",
        "prCreation",
        "timezone",
        "schedule",
        "packageRules",
        "extends",
        "minimumReleaseAge",
        "branchConcurrentLimit",
        "baseBranches",
        "baseBranchPatterns",
        "vulnerabilityAlerts",
        "dependencyDashboardApproval",
        "labels",
        "addLabels",
    }
    unknown = (
        ["Configuration contains PR behavior outside the certified forecast policy subset."]
        if set(config) - certified_fields
        else []
    )
    for key in (
        "extends",
        "minimumReleaseAge",
        "branchConcurrentLimit",
        "baseBranches",
        "baseBranchPatterns",
        "vulnerabilityAlerts",
        "dependencyDashboardApproval",
    ):
        if config.get(key):
            unknown.append(
                f"Resolved future {key} policy is not certified by local lookup evidence."
            )
    rules = config.get("packageRules", [])
    safe_rule_fields = {
        "description",
        "labels",
        "addLabels",
        "matchManagers",
        "matchDatasources",
        "matchPackageNames",
        "matchUpdateTypes",
        "matchDepTypes",
        "matchFileNames",
        "enabled",
        "groupName",
        "groupSlug",
        "rangeStrategy",
    }
    if not isinstance(rules, list) or any(
        not isinstance(rule, dict) or set(rule) - safe_rule_fields for rule in rules
    ):
        unknown.append("Package-rule timing/PR policy is not certified for future replay.")
    if config.get("prCreation", "immediate") != "immediate":
        unknown.append("PR creation depends on future branch/CI state.")
    if config.get("schedule") not in (None, [], ["at any time"]):
        unknown.append("Renovate natural-language/cron schedule replay is not certified.")
    return [
        Policy(
            ecosystem="all-managers",
            directory="/",
            openLimit=config.get("prConcurrentLimit", 10),
            hourlyLimit=config.get("prHourlyLimit", 2),
            timezone=config.get("timezone", "UTC"),
            unknown=unknown,
        )
    ]
