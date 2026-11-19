"""Offline Bootstrap view of native scanner discovery and recommendation differences."""

from html import escape
from importlib.resources import files
from typing import Any

from gappy.io import dump
from gappy.report.context import for_html
from gappy.report.filter import filter_attributes, update_filter
from gappy.report.metadata import configuration_badges


def text(value: object) -> str:
    return escape(str(value))


def render(report: dict[str, Any], *, verbose: bool = False) -> str:
    metrics = report["metrics"]
    scan_status = {scan["provider"]: scan["status"] for scan in report["scans"]}
    styles = "\n".join(
        files("gappy.report").joinpath(name).read_text(encoding="utf-8")
        for name in ("bootstrap.min.css", "styles.css")
    )
    cards = []
    for scan in report["scans"]:
        provider = scan["provider"]
        counts = metrics["providers"][provider]
        update_rows = "".join(
            f'<tr><th scope="row"><span class="badge text-bg-secondary">'
            f"{text(ecosystem)}</span></th>"
            f"<td>{values[provider]['discovered']}</td>"
            f"<td>{values[provider]['withRecommendations']}</td>"
            f"<td>{values[provider]['recommendedUpdates']}</td></tr>"
            for ecosystem, values in metrics["byEcosystem"].items()
        )
        warnings = "".join(f"<li>{text(warning)}</li>" for warning in scan["warnings"])
        cards.append(
            '<div class="col-md-6"><section class="card h-100"><div class="card-body">'
            f'<h2 class="h4">{text(provider.title())} '
            f'<span class="badge text-bg-secondary">{text(scan["status"])}</span></h2>'
            f'<p class="small">Cache: {text(scan["cacheMode"])}'
            f" · Volumes: {text(', '.join(scan.get('cacheVolumes', [])) or 'none')}</p>"
            f'<p class="fs-4">{counts["discovered"]} direct dependencies discovered</p>'
            '<p class="small text-body-secondary">'
            + (
                "Native lockfile/transitive entries without a manifest requirement are excluded "
                "from findings and counts."
                if provider == "dependabot"
                else "Native package-file declarations are compared; explicit indirect entries "
                "and unclassified npm overrides are excluded."
            )
            + " No 100-dependency cap is applied."
            + "</p>"
            f"<p><strong>{counts['withRecommendations']} dependencies with recommended updates"
            f"</strong> · {counts['recommendedUpdates']} recommended target versions</p>"
            '<h3 class="h6">Update summary by ecosystem</h3>'
            '<div class="table-responsive"><table class="table table-sm">'
            f"<caption>{text(provider.title())} update summary</caption>"
            '<thead><tr><th scope="col">Ecosystem</th><th scope="col">Discovered</th>'
            '<th scope="col">With updates</th><th scope="col">Target versions</th>'
            f"</tr></thead><tbody>{update_rows}</tbody></table></div>"
            + (
                '<p class="small text-warning-emphasis">Incomplete scan: these are observed '
                "recommendations only; additional updates may be missing.</p>"
                if scan["status"] != "complete"
                else ""
            )
            + '<p class="small text-body-secondary">Counts use dependency/manifest contexts, '
            "not PRs. Multiple target versions for one dependency are counted separately.</p>"
            "<details><summary>Configuration, scan limits and errors</summary>"
            f"<ul>{warnings}</ul></details></div></section></div>"
        )
    sections = []
    total_updates = 0
    statuses: set[str] = set()
    for ecosystem in metrics["ecosystems"]:
        rows = []
        ecosystem_updates = 0
        for row in report["dependencies"]:
            if row["ecosystem"] != ecosystem:
                continue
            has_update = bool(
                row["dependabot"]["recommendations"] or row["renovate"]["recommendations"]
            ) or row["result"] in (
                "dependabot-only-discovery",
                "renovate-only-discovery",
                "installed-version-mismatch",
                "installed-reference-unresolved",
                "registry-mismatch",
                "different-recommendations",
            )
            ecosystem_updates += has_update
            cells = [
                f'<th scope="row">{text(row["package"])}'
                f'<div class="small text-body-secondary">{text(row["manifestPath"])}</div></th>'
            ]
            for provider in ("dependabot", "renovate"):
                item = row[provider]
                label = "Discovered" if item["discovered"] else "Not in direct scan evidence"
                tone = "success" if item["discovered"] else "warning"
                versions = ", ".join(item["installedVersions"]) or "Not reported"
                targets = ", ".join(item["recommendations"]) or "None reported"
                availability = ""
                if item["discovered"] and (
                    not item["recommendations"] or item["policy"] == ["deferred"]
                ):
                    if item["policy"] == ["excluded"]:
                        availability_label, availability_tone = "Excluded by policy", "secondary"
                    elif item["policy"] == ["deferred"]:
                        availability_label, availability_tone = "Update deferred", "secondary"
                    elif (
                        item["lookupComplete"]
                        and item["policy"] == ["eligible"]
                        and scan_status[provider] == "complete"
                    ):
                        availability_label, availability_tone = (
                            "No newer update recommended",
                            "success",
                        )
                    else:
                        availability_label, availability_tone = (
                            "Update availability unknown",
                            "warning",
                        )
                    availability = (
                        f'<div class="mt-1"><span class="badge text-bg-{availability_tone}">'
                        f"{availability_label}</span></div>"
                    )
                cells.append(
                    f'<td><span class="badge text-bg-{tone}">{label}</span>'
                    f'<div class="mt-2">Current: {text(versions)}</div>'
                    f'<div class="fw-semibold">Recommended: {text(targets)}</div>'
                    f"{availability}"
                    f'<div class="small">Policy: {text(", ".join(item["policy"]) or "unknown")}'
                    "</div>"
                    + configuration_badges(item.get("configuration", []))
                    + '<details class="small mt-2"><summary>Reason / evidence</summary>'
                    f"<p>{text(item['reason'])}</p>"
                    f"<p>{text(', '.join(item['evidence']) or 'No recommendation evidence')}</p>"
                    f"<p>Registry: {text(', '.join(item['registries']) or 'unknown')}</p>"
                    "</details></td>"
                )
            result = row["result"]
            status = result.replace("-", " ").capitalize()
            statuses.add(status)
            action = (
                "Check manager scope, configuration and scan failures on the other tool."
                if result.endswith("-only-discovery")
                else "Compare native recommended versions and policy decisions; not a proven miss."
                if result == "different-recommendations"
                else "Installed versions or registries differ; do not rank recommendations."
                if result in {"installed-version-mismatch", "registry-mismatch"}
                else "A digest pin and a release version cannot be linked from this evidence. "
                "Rescan to capture the original pin; do not assume different installed releases."
                if result == "installed-reference-unresolved"
                else "Both completed eligible lookups with no newer update recommended "
                "under the scanned configuration."
                if result == "no-recommendations"
                else "Update availability is unknown; inspect lookup and policy evidence."
                if result == "not-assessable"
                else "Both tools recommend the same target versions."
            )
            cells.append(f"<td><strong>{text(result)}</strong><p>{text(action)}</p></td>")
            rows.append(
                f'<tr data-has-updates="{str(has_update).lower()}" '
                f"{filter_attributes(ecosystem, [status])}>" + "".join(cells) + "</tr>"
            )
        total_updates += ecosystem_updates
        sections.append(
            f'<section class="card mb-4" data-filter-section '
            f'data-has-updates="{str(bool(ecosystem_updates)).lower()}">'
            f'<div class="card-body"><h2 class="h4">'
            f'<span class="badge text-bg-secondary">{text(ecosystem)}</span> '
            f'<span class="badge text-bg-secondary unfiltered-count">{len(rows)}</span>'
            f'<span class="badge text-bg-secondary updates-filter-count">{ecosystem_updates}</span>'
            "</h2>"
            '<div class="table-responsive"><table class="table table-striped align-top">'
            f"<caption>{text(ecosystem)} discovery and recommended updates by dependency</caption>"
            '<thead><tr><th scope="col">Dependency / manifest</th>'
            '<th scope="col">Dependabot</th><th scope="col">Renovate</th>'
            f'<th scope="col">Difference / next action</th></tr></thead><tbody>{"".join(rows)}'
            "</tbody></table></div></div></section>"
        )
    outcomes = "".join(
        f'<li class="list-group-item d-flex justify-content-between">{text(label)}'
        f'<span class="badge text-bg-secondary">{count}</span></li>'
        for label, count in metrics["outcomes"].items()
    )
    limitations = "".join(f"<li>{text(warning)}</li>" for warning in report["warnings"])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Gappy — {text(report['repository'])}</title><style>{styles}</style></head>"
        '<body class="bg-body-tertiary"><main class="container-fluid report-shell py-4">'
        "<h1>Direct dependency discovery and recommended updates</h1>"
        f'<p class="lead">{text(report["repository"])}</p>'
        '<div class="alert alert-info">Scanner comparison — no Gappy-generated inventory. '
        "Each row comes from at least one scanner's direct dependency evidence. Transitive and "
        "unclassified observations are excluded from findings and counts. A direct update does "
        "not prove which transitive packages change. Dependencies and updates missed by both "
        "cannot be measured, so this report does not assign patch coverage or an overall winner."
        "</div>"
        '<p class="small text-body-secondary">“No newer update recommended” means a completed, '
        "eligible lookup returned no update under the scanned configuration. The package may "
        "already be up to date, or no newer eligible version may be available. "
        "This is not proof that the installed version is the latest published release.</p>"
        f"<p>{metrics['dependencies']} distinct direct dependency/manifest contexts across "
        f"{len(metrics['ecosystems'])} ecosystems. Counts are not PR counts.</p>"
        f'<div class="row g-3 mb-4">{"".join(cards)}</div>'
        f'<section class="card mb-4"><div class="card-body"><h2 class="h4">Where results differ'
        f'</h2><ul class="list-group">{outcomes}</ul></div></section>'
        + update_filter(
            "".join(sections)
            or '<p class="alert alert-warning" data-has-updates="false">'
            "No direct discovery evidence received. Check scan status and directness warnings; "
            "this does not prove there are no dependencies.</p>",
            has_updates=bool(total_updates),
            verbose=verbose,
            ecosystems=set(metrics["ecosystems"]),
            statuses=statuses,
        )
        + f'<section class="card mb-4"><div class="card-body"><h2 class="h4">Limitations</h2>'
        f"<ul>{limitations}</ul></div></section>"
        '<details class="card"><summary class="card-header">Comparison evidence context '
        "(full native observations remain in scan JSON)</summary>"
        f'<pre class="card-body">{text(dump(for_html(report)))}</pre></details>'
        '<footer class="small mt-4">Bootstrap 5.3.8 (MIT) · Offline · No scripts or external '
        "assets</footer></main></body></html>\n"
    )
