"""Offline four-column configuration comparison."""

from html import escape
from importlib.resources import files
from typing import Any

from gappy.compare.scoring import score_configurations
from gappy.report.filter import filter_attributes, update_filter
from gappy.report.metadata import configuration_badges
from gappy.report.traffic import traffic_lights


def score_summary(report: dict[str, Any]) -> str:
    scoring = report.get("scoring") or score_configurations(
        report["dependencies"], report["variants"]
    )
    names = {v["id"]: f"{v['scan']['provider'].title()} / {v['label']}" for v in report["variants"]}
    if not scoring["dependencies"]:
        headline = "Not assessable: no direct dependency evidence."
    elif scoring["provisional"]:
        headline = "Provisional ranking: incomplete scans prevent declaring a winner."
    elif scoring["winner"]:
        headline = f"Highest observed-evidence score: {names[scoring['winner']]}"
    else:
        headline = "Joint highest observed-evidence score: " + ", ".join(
            names[key] for key in scoring["leaders"]
        )
    rows = []
    for item in scoring["variants"]:
        cells = "".join(
            f"<td>{part['count']}/{part['total']} ({part['points']:.1f} pts)</td>"
            for name in ("discovery", "resolution", "recommendations")
            for part in [item["components"][name]]
        )
        value = f"{item['score']:.2f} / 100" if item["score"] is not None else "N/A"
        rows.append(
            f'<tr><td>{item["rank"] or "—"}</td><th scope="row">'
            f"{escape(names[item['id']])}</th><td><strong>{value}</strong></td>"
            f"<td>{escape(item['status'])}</td>{cells}</tr>"
        )
    weights = scoring["weights"]
    return (
        '<section class="mb-4" aria-labelledby="overall-score">'
        '<h2 id="overall-score">Overall optimisation score</h2>'
        f"<p><strong>{escape(headline)}</strong></p>"
        '<div class="table-responsive"><table class="table">'
        "<caption>Relative evidence quality across scanner configurations</caption>"
        "<thead><tr><th>Rank</th><th>Scanner / configuration</th><th>Overall</th>"
        "<th>Scan status</th>"
        f"<th>Discovery ({weights['discovery']} pts)</th>"
        f"<th>Resolved outcomes ({weights['resolution']} pts)</th>"
        f"<th>Recommendation visibility ({weights['recommendations']} pts)</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        "<details><summary>How the score is calculated</summary>"
        "<p>The denominator is the union of direct dependency rows observed across all "
        "four scans, not an independent inventory. Discovery earns credit for finding a row. "
        "Resolved outcomes require a complete scan, a known installed version and either "
        "a completed eligible lookup or an explicit deferred outcome with a completed lookup "
        "or known recommendation. Excluded, missing and unknown outcomes earn no resolution "
        "credit. Actionable and deferred updates receive equal credit; green rows do not "
        "score higher than correctly reported updates.</p>"
        "<p>Recommendation visibility measures the fraction of rows with recommendations "
        "anywhere that also have a recommendation in this variant. It does not compare target "
        "versions or prove missed updates: policies, manager support, registry context and "
        "timing may differ. If no recommendations exist, discovery and resolution each weigh "
        "50 points. Scores are rounded to two decimals; equal scores share a rank. Any "
        "incomplete scan makes the ranking provisional. Empty evidence is not assessable.</p>"
        "<p>This is a relative evidence-quality heuristic, not a security rating, update "
        "freshness, execution-speed or PR-noise score, nor proof of an objectively optimal "
        "policy. It cannot detect dependencies missed by every scanner.</p></details></section>"
    )


def render(report: dict[str, Any], *, verbose: bool = False) -> str:
    variants = report["variants"]
    styles = "\n".join(
        files("gappy.report").joinpath(name).read_text()
        for name in ("bootstrap.min.css", "styles.css")
    )
    headings = "".join(
        f'<th scope="col">{escape(v["scan"]["provider"].title())} / {v["label"]}</th>'
        for v in variants
    )
    cards = []
    for variant in variants:
        scan = variant["scan"]
        counts = report["comparisons"][variant["label"]]["metrics"]["providers"][scan["provider"]]
        warnings = "".join(f"<li>{escape(w)}</li>" for w in scan["warnings"])
        hashes = escape(str(scan["snapshot"]["configHashes"]))
        cards.append(
            '<section class="col-md-6 col-xl-3"><div class="card h-100"><div class="card-body">'
            f'<h2 class="h5">{escape(scan["provider"].title())} / {variant["label"]}</h2>'
            f"<p>Scan: <strong>{escape(scan['status'])}</strong></p>"
            f"<p>{counts['discovered']} direct dependencies; "
            f"{counts['withRecommendations']} with updates; "
            f"{counts['recommendedUpdates']} target versions.</p>"
            "<details><summary>Configuration and scan evidence</summary>"
            f"<p>Configuration hashes: {hashes}</p>"
            f"<p>Snapshot: {escape(scan['snapshot']['contentHash'])}</p>"
            f"<ul>{warnings}</ul></details></div></div></section>"
        )
    rows = []
    ecosystems: set[str] = set()
    statuses: set[str] = set()
    for row in report["dependencies"]:
        cells = []
        lights = traffic_lights(row, variants)
        row_statuses = sorted(
            {
                "success": "Up to date",
                "primary": "Deferred",
                "danger": "Out of date",
                "warning": "Unknown",
            }[tone]
            for tone, _ in lights.values()
        )
        ecosystems.add(row["ecosystem"])
        statuses.update(row_statuses)
        for variant in variants:
            side = row["variants"].get(variant["id"])
            tone, label = lights[variant["id"]]
            indicator = f'<span class="badge text-bg-{tone}">{escape(label)}</span><br>'
            if not side or not side["discovered"]:
                cells.append(
                    f'<td class="table-{tone}">{indicator}Not in direct scan evidence</td>'
                )
                continue
            cells.append(
                f'<td class="table-{tone}">{indicator}'
                f"Current: {escape(', '.join(side['installedVersions']))}<br>"
                "<strong>Recommended: "
                f"{escape(', '.join(side['recommendations']) or 'None reported')}"
                "</strong><br>"
                + ("Completed lookup" if side["lookupComplete"] else "Update availability unknown")
                + (
                    "<br>Incomplete scan — additional updates may be missing"
                    if variant["scan"]["status"] != "complete"
                    else ""
                )
                + f"<br>Policy: {escape(', '.join(side['policy']))}"
                + configuration_badges(side.get("configuration", []))
                + f"<details><summary>Reason / evidence</summary><p>{escape(side['reason'])}</p>"
                f"<p>Registry: {escape(', '.join(side['registries']))}</p>"
                f"<p>{escape(', '.join(side['evidence']))}</p></details></td>"
            )
        outcomes = "<br>".join(
            f"{label}: {escape(outcome)}" for label, outcome in row["outcomes"].items()
        )
        rows.append(
            f'<tr data-has-updates="{str(row["hasChanges"]).lower()}" '
            f"{filter_attributes(row['ecosystem'], row_statuses)}>"
            f'<th scope="row">{escape(row["package"])}'
            f'<div class="small"><span class="badge text-bg-secondary">'
            f"{escape(row['ecosystem'])}</span> · {escape(row['manifestPath'])}</div>"
            f"</th>{''.join(cells)}<td>{outcomes}</td></tr>"
        )
    table = (
        '<div class="table-responsive"><table class="table table-striped align-top">'
        "<caption>Direct dependency recommendations across baseline and optimised configs</caption>"
        f"<thead><tr><th>Dependency</th>{headings}<th>Scanner comparison</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Gappy — configuration comparison</title><style>{styles}</style></head>"
        '<body><main class="container-fluid report-shell py-4">'
        "<h1>Baseline and optimised dependency comparison</h1>"
        f"<p>{escape(report['repository'])} · {escape(report['revision'])}</p>"
        '<div class="alert alert-info">'
        + " ".join(escape(w) for w in report["warnings"])
        + '</div><div class="row g-3 mb-4">'
        + "".join(cards)
        + "</div>"
        + score_summary(report)
        + '<p class="traffic-legend"><strong>Dependency update status:</strong> '
        '<span class="badge text-bg-success">Green: up to date</span> '
        '<span class="badge text-bg-primary">Blue: deferred by policy</span> '
        '<span class="badge text-bg-warning">'
        "Yellow: no applicable policy or unknown result</span> "
        '<span class="badge text-bg-danger">Red: out of date; update recommended</span>. '
        "Each scanner is assessed independently. A completed eligible lookup is green "
        "when no update is recommended or current matches its recommendation after "
        "release v-prefix normalization. A different recommended version or digest is red. "
        "Explicitly deferred results are blue when the lookup completed or a target version "
        "is known, even if cooldown or release-age eligibility is still unknown. "
        "Known target versions remain under Recommended but are not yet actionable. "
        "Known explicitly deferred targets remain blue even in incomplete scans; "
        "the scan warning still applies. Other incomplete results, missing evidence and "
        "other policy-filtered results remain yellow. "
        "Up to date means according to that scanner and configuration, not necessarily "
        "the latest published release; colours do not rank scanners or security.</p>"
        + update_filter(
            table,
            has_updates=any(r["hasChanges"] for r in report["dependencies"]),
            verbose=verbose,
            ecosystems=ecosystems,
            statuses=statuses,
        )
        + "<p>Summary counts and JSON evidence are not filtered. No recommendation in an "
        "incomplete scan is not an up-to-date claim.</p>"
        "<footer>Bootstrap 5.3.8 (MIT) · Offline · No scripts or external assets</footer>"
        "</main></body></html>\n"
    )
