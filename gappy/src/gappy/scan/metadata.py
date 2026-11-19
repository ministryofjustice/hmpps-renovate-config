"""Native-compatible configuration IDs encoded as config-id: labels."""

from typing import Any

from gappy.contracts import ConfigurationReference
from gappy.io import GappyError

PREFIX = "config-id:"


def references(labels: object, basis: str) -> list[ConfigurationReference]:
    if labels is None:
        return []
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise GappyError("Configuration labels must be an array of strings.")
    return [
        ConfigurationReference.model_validate(
            {"id": label[len(PREFIX) :], "labels": labels, "basis": basis}
        )
        for label in labels
        if label.startswith(PREFIX) and label != PREFIX
    ]


def renovate_references(
    config: dict[str, Any], manager: str, item: dict[str, Any]
) -> list[ConfigurationReference]:
    resolved = references(item.get("labels"), "renovate-resolved")
    scoped = []
    for rule in config.get("packageRules", []):
        if not isinstance(rule, dict):
            continue
        selectors = {key for key in rule if key.startswith("match")}
        if not selectors or selectors - {"matchManagers", "matchDatasources", "matchPackageNames"}:
            continue
        values = {
            "matchManagers": manager,
            "matchDatasources": item.get("datasource"),
            "matchPackageNames": item.get("packageName") or item.get("depName"),
        }
        # Only exact configured scopes are inferred; native policy remains authoritative.
        if all(isinstance(rule[key], list) and values[key] in rule[key] for key in selectors):
            scoped.extend(references(rule.get("addLabels", rule.get("labels")), "configured-scope"))
    return sorted({ref.id: ref for ref in [*scoped, *resolved]}.values(), key=lambda ref: ref.id)
