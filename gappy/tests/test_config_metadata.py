import json
from pathlib import Path

import pytest
import yaml

from gappy.compare.configurations import compare_configurations
from gappy.compare.discovered import compare
from gappy.contracts import ConfigurationReference
from gappy.report.html import render
from gappy.scan.dependabot import jobs
from gappy.scan.metadata import references, renovate_references
from gappy.scan.native import renovate
from tests.test_compare import scan
from tests.test_dependabot import snapshot
from tests.test_scan import FIXTURES

ROOT = Path(__file__).parents[1]


def test_optimised_entries_have_unique_native_label_ids() -> None:
    config = yaml.safe_load((ROOT / "optimised/dependabot.yml").read_text())
    entries = [*config.get("multi-ecosystem-groups", {}).values(), *config["updates"]]
    ids = []
    for entry in entries:
        refs = references(entry["labels"], "dependabot-entry")
        assert len(refs) == 1
        ids.append(refs[0].id)
    assert len(set(ids)) == len(ids)
    planned, warnings = jobs(config, snapshot(), "gappy/fixture")
    assert not warnings
    assert {job.ecosystem for job in planned} == {
        "npm",
        "github-actions",
        "docker",
        "docker-compose",
    }
    for job in planned:
        assert job.configuration
        assert "labels" not in job.definition
        item = scan("dependabot", [None]).observations[0]
        assert job.policy([item])[0].configuration == job.configuration
    actions = next(job for job in planned if job.ecosystem == "github-actions")
    assert actions.patterns is None
    assert {ref.id for ref in actions.configuration} == {"optimised-actions"}
    config = json.loads((ROOT / "optimised/renovate.json").read_text())
    ids = []
    for rule in config["packageRules"]:
        refs = references(rule["addLabels"], "configured-scope")
        assert len(refs) == 1
        assert rule["description"].startswith(refs[0].id + ":")
        ids.append(refs[0].id)
    assert len(set(ids)) == len(ids)


def test_native_renovate_evidence_and_scoped_metadata_survive_offline_report() -> None:
    labels = ["docker", "config-id:ghcr"]
    config = {"packageRules": [{"matchManagers": ["npm"], "addLabels": labels}]}
    observations, complete = renovate(
        (FIXTURES / "renovate-patch.jsonl").read_text(), configuration=config
    )
    assert complete
    assert observations[0].configuration[0].basis == "configured-scope"
    right = scan("renovate", [None]).model_copy(update={"observations": observations})
    left = scan("dependabot", [None])
    for report in (compare(left, right), compare_configurations((left, right), (left, right))):
        output = render(report)
        assert "config-id:ghcr" in output
        assert "configured-scope" in output
        assert '<span class="badge text-bg-secondary">ghcr</span>' in output
    # Old exports without metadata remain valid.
    assert not left.observations[0].configuration


@pytest.mark.parametrize(
    "selector",
    [
        {"matchPackageNames": ["other"]},
        {"matchPackageNames": ["org/**"]},
        {"matchUpdateTypes": ["major"]},
        {"matchFileNames": ["**/*.yml"]},
    ],
)
def test_metadata_does_not_claim_unverified_rule_matches(selector: dict[str, object]) -> None:
    config = {
        "packageRules": [
            {
                "matchManagers": ["github-actions"],
                **selector,
                "addLabels": ["config-id:scoped"],
            }
        ]
    }
    assert not renovate_references(config, "github-actions", {"depName": "org/repo"})


def test_resolved_metadata_is_authoritative_over_local_scope() -> None:
    refs = renovate_references(
        {"packageRules": [{"matchManagers": ["npm"], "addLabels": ["config-id:npm"]}]},
        "npm",
        {"labels": ["native", "config-id:npm"]},
    )
    assert refs == [
        ConfigurationReference(
            id="npm", labels=["native", "config-id:npm"], basis="renovate-resolved"
        )
    ]


def test_ghcr_rules_cover_updates_and_preserve_migration_tags() -> None:
    config = json.loads((ROOT / "optimised/renovate.json").read_text())
    existing = next(
        rule
        for rule in config["packageRules"]
        if "config-id:optimised-hmpps-node-ghcr" in rule["addLabels"]
    )
    assert existing["matchDatasources"] == ["docker"]
    assert existing["matchPackageNames"] == ["ghcr.io/ministryofjustice/hmpps-node"]
    assert existing["enabled"] and existing["pinDigests"]
    migration = next(rule for rule in config["packageRules"] if "replacementName" in rule)
    assert migration["replacementName"] == "ghcr.io/ministryofjustice/hmpps-node"
    assert (
        not {"replacementVersion", "overridePackageName", "overrideDatasource"} & migration.keys()
    )
    assert migration["autoReplaceGlobalMatch"] is False
    assert migration["versioning"] == "docker"
    assert migration["versionCompatibility"] is None
    assert "ministryofjustice/hmpps-node" in migration["matchPackageNames"]
    assert "hmpps-node" in migration["matchPackageNames"]


def test_native_replacement_destination_is_preserved_in_recommendations() -> None:
    events = [
        json.loads(line) for line in (FIXTURES / "renovate-patch.jsonl").read_text().splitlines()
    ]
    dependency = events[0]["config"]["npm"][0]["deps"][0]
    dependency["updates"] = [
        {
            "newName": "ghcr.io/ministryofjustice/hmpps-node",
            "newValue": "24-alpine",
            "updateType": "replacement",
        }
    ]
    observations, _ = renovate("\n".join(map(json.dumps, events)))
    assert observations[0].candidates[0].targetPackage == "ghcr.io/ministryofjustice/hmpps-node"
    left, right = scan("dependabot", [None]), scan("renovate", [None])
    right = right.model_copy(update={"observations": observations})
    report = compare(left, right)
    recommendations = [
        value for row in report["dependencies"] for value in row["renovate"]["recommendations"]
    ]
    assert recommendations == ["ghcr.io/ministryofjustice/hmpps-node:24-alpine"]
