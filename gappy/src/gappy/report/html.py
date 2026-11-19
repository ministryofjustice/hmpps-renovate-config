"""Render offline Bootstrap reports, distinguishing confirmed gaps from missing evidence."""

from html import escape
from importlib.resources import files

from gappy.io import dump
from gappy.report.context import for_html
from gappy.report.filter import filter_attributes, update_filter


def text(value: object) -> str:
    return escape(str(value))


def badge(label: str, tone: str) -> str:
    return f'<span class="badge text-bg-{tone}">{text(label)}</span>'


def render(report: dict[str, object], *, verbose: bool = False) -> str:
    if report.get("mode") == "configuration-comparison":
        from gappy.report.configurations import render as render_configurations

        return render_configurations(report, verbose=verbose)
    if report.get("mode") == "scanner-comparison":
        from gappy.report.discovered import render as render_discovered

        return render_discovered(report, verbose=verbose)
    assessments, metrics, scans, warnings = (
        report["assessments"],
        report["metrics"],
        report["scans"],
        report["warnings"],
    )
    if not isinstance(assessments, list) or not isinstance(metrics, dict):
        raise TypeError("Expected opportunity assessments and metrics.")
    if not isinstance(scans, list) or not isinstance(warnings, list):
        raise TypeError("Expected scan provenance and limitations.")
    assets = files("gappy.report")
    styles = "\n".join(
        assets.joinpath(name).read_text(encoding="utf-8")
        for name in ("bootstrap.min.css", "styles.css")
    )
    total = len(assessments)
    denominator = metrics["denominator"]
    unknown = sum(row["result"] == "not-assessable" for row in assessments)
    confirmed = sum(
        any(
            row[provider]["policy"] == "eligible" and row[provider]["detection"] == "not-detected"
            for provider in ("dependabot", "renovate")
        )
        for row in assessments
    )
    policy_gaps = sum(
        any(row[p]["policy"] in {"excluded", "deferred"} for p in ("dependabot", "renovate"))
        for row in assessments
    )
    conclusion = (
        "No fair winner can be determined"
        if not denominator
        else "Tie on the assessed subset"
        if metrics["winner"] == "tie"
        else f"{str(metrics['winner']).title()} leads on the assessed subset"
    )
    explanation = (
        f"None of the {total} inventory patch opportunities has sufficient jointly eligible "
        "evidence for paired coverage. Unavailable is not 0%, and unknown is not a missed patch."
        if not denominator
        else f"Paired coverage uses {denominator} of {total} inventory patch opportunities. "
        "Only jointly eligible, assessable evidence contributes to the winner."
    )
    cards = []
    summary = []
    for scan in scans:
        provider = scan["provider"]
        if provider not in ("dependabot", "renovate"):
            raise ValueError("Unexpected scan provider.")
        name = provider.title()
        detected = sum(row[provider]["detection"] == "detected" for row in assessments)
        missed = sum(
            row[provider]["policy"] == "eligible" and row[provider]["detection"] == "not-detected"
            for row in assessments
        )
        unresolved = sum(row[provider]["detection"] == "unknown" for row in assessments)
        extracted = sum(row[provider]["extraction"] == "extracted" for row in assessments)
        limitations = "".join(f"<li>{text(warning)}</li>" for warning in scan["warnings"])
        state = badge(scan["status"], "success" if scan["status"] == "complete" else "warning")
        cards.append(
            '<div class="col-lg-6"><section class="card h-100 shadow-sm">'
            f'<div class="card-header d-flex justify-content-between"><h2 class="h5 mb-0">'
            f'{name}</h2>{state}</div><div class="card-body">'
            '<dl class="row mb-0">'
            f'<dt class="col-8">Extracted inventory opportunities</dt><dd class="col-4">'
            f"{extracted} / {total}</dd>"
            f'<dt class="col-8">Patches identified</dt><dd class="col-4">{detected} / {total}</dd>'
            f'<dt class="col-8">Confirmed eligible misses</dt><dd class="col-4">{missed}</dd>'
            f'<dt class="col-8">Detection unknown</dt><dd class="col-4">{unresolved}</dd></dl>'
            '<p class="small text-body-secondary mt-3">Identification counts are independent '
            "evidence, not a fair paired score; policy may remain unverified.</p>"
            f'<details><summary>Scan limitations and blockers</summary><ul class="mt-2">'
            f"{limitations or '<li>No scan warnings.</li>'}</ul>"
            f'<p class="small">{text(scan["startedAt"])} to {text(scan["finishedAt"])}</p>'
            "</details></div></section></div>"
        )
        rate = metrics["coverage"][provider]
        percentage = "Unavailable" if rate is None else f"{rate * 100:.1f}%"
        count = (
            "Not assessable"
            if not denominator
            else (f"{metrics['detections'][provider]} / {denominator}")
        )
        summary.append(f'<tr><th scope="row">{name}</th><td>{count}</td><td>{percentage}</td></tr>')
    rows = []
    has_updates = False
    ecosystems: set[str] = set()
    statuses: set[str] = set()
    for row in assessments:
        has_update = any(
            row[provider]["detection"] == "detected" for provider in ("dependabot", "renovate")
        )
        has_updates = has_updates or has_update
        dep = row["dependency"]
        ecosystems.add(dep["ecosystem"])
        status = row["result"].replace("-", " ").capitalize()
        statuses.add(status)
        cells = [
            f'<th scope="row"><span class="fw-semibold">{text(dep["package"])}</span>'
            f'<div class="small text-body-secondary">{text(dep["manifestPath"])}</div>'
            f'<div class="small"><span class="badge text-bg-secondary">'
            f"{text(dep['ecosystem'])}</span></div></th>",
            f'<td><span class="text-body-secondary">{text(dep["installedVersion"])}</span>'
            f" &rarr; <strong>{text(row['candidateVersion'])}</strong>"
            f'<div class="small text-body-secondary">{text(row["availableAt"])}</div></td>',
        ]
        misses = []
        for provider in ("dependabot", "renovate"):
            item = row[provider]
            if item["detection"] == "detected":
                label, tone = "Patch identified", "success"
            elif item["policy"] in {"excluded", "deferred"}:
                label, tone = "Policy: " + item["policy"], "secondary"
            elif item["detection"] == "not-detected" and item["policy"] == "eligible":
                label, tone = "Confirmed miss", "danger"
                misses.append(provider.title())
            else:
                label, tone = "Unknown — evidence gap", "warning"
            cells.append(
                f"<td>{badge(label, tone)}"
                f'<div class="small mt-2">Extraction: {text(item["extraction"])}'
                f"<br>Policy: {text(item['policy'])}</div>"
                f'<details class="small mt-2"><summary>Why / evidence</summary>'
                f'<p class="mt-2">{text(item["reason"])}</p>'
                f"<p>Evidence: {text(', '.join(item['evidence']) or 'unavailable')}</p>"
                "</details></td>"
            )
        if misses:
            action = f"Investigate missed patch: {', '.join(misses)}."
        elif row["result"] == "not-assessable":
            action = "Resolve scan / policy evidence gaps before ranking tools."
        elif any(row[p]["policy"] in {"excluded", "deferred"} for p in ("dependabot", "renovate")):
            action = "Review policy exclusions or release-age deferral; not a detection failure."
        else:
            action = "Both tools identified this patch; no confirmed detection gap."
        cells.append(
            f'<td><strong>{text(row["result"])}</strong><p class="small mt-2">{text(action)}</p>'
            f'<span class="small">Winner: {text(row["winner"] or "not established")}</span></td>'
        )
        rows.append(
            f'<tr data-has-updates="{str(has_update).lower()}" '
            f"{filter_attributes(dep['ecosystem'], [status])}>" + "".join(cells) + "</tr>"
        )
    if not rows:
        rows.append(
            '<tr><td colspan="5">No direct patch opportunities could be assessed. '
            "Check the directness limitations; this does not prove that the repository "
            "is up to date.</td></tr>"
        )
    limitations = "".join(f"<li>{text(warning)}</li>" for warning in warnings)
    tiles = "".join(
        '<div class="col-6 col-lg-3"><div class="card h-100"><div class="card-body">'
        f'<div class="display-6 fw-semibold">{value}</div><div>{label}</div>'
        "</div></div></div>"
        for value, label in (
            (total, "Inventory patch opportunities"),
            (confirmed, "Opportunities with confirmed eligible misses"),
            (unknown, "Opportunities blocked by evidence gaps"),
            (policy_gaps, "Opportunities affected by policy"),
        )
    )
    headings = (
        "Dependency / context",
        "Installed → patch / released",
        "Dependabot",
        "Renovate",
        "Gap / next action",
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Gappy — {text(report['repository'])}</title><style>{styles}</style></head>"
        '<body class="bg-body-tertiary"><main class="container-fluid report-shell py-4">'
        '<header class="mb-4"><div class="text-uppercase text-body-secondary fw-semibold">'
        'Gappy · dependency detection</div><h1 class="display-6 fw-bold">Where are the gaps?</h1>'
        f'<p class="lead mb-1">{text(report["repository"])}</p>'
        '<p class="text-body-secondary">Patch opportunities, not PR counts. '
        "A later patch can cover earlier patches on the same release line.</p></header>"
        f'<section class="alert alert-{"warning" if not denominator else "primary"}" '
        f'aria-labelledby="conclusion"><h2 id="conclusion" class="h4">{text(conclusion)}</h2>'
        f'<p class="mb-0">{text(explanation)}</p></section>'
        f'<section class="row g-3 mb-4" aria-label="Gap overview">{tiles}</section>'
        '<p class="small text-body-secondary">Counts are patch opportunities, not unique '
        "dependencies; categories may overlap.</p>"
        f'<section class="row g-4 mb-4" aria-label="Scanner evidence">{"".join(cards)}</section>'
        '<section class="card mb-4"><div class="card-body"><h2 class="h4">'
        "Fair paired coverage</h2><p>Equal denominator for both tools. Identification evidence "
        "above is retained even when a paired comparison cannot be scored.</p>"
        '<div class="table-responsive"><table class="table table-striped align-middle">'
        "<caption>Jointly eligible, assessable patch coverage</caption><thead><tr>"
        '<th scope="col">Tool</th><th scope="col">Detected / shared denominator</th>'
        f'<th scope="col">Coverage</th></tr></thead><tbody>{"".join(summary)}</tbody></table></div>'
        f'<p class="small mb-0">Jointly eligible: {metrics["jointlyEligible"]}; '
        f"eligible but unassessable: {metrics['excludedFromPaired']}. "
        "This is not a universal tool ranking.</p></div></section>"
        + update_filter(
            f'<section class="card mb-4" data-filter-section '
            f'data-has-updates="{str(has_updates).lower()}">'
            '<div class="card-body"><h2 class="h4">'
            "Patch-by-patch gap map</h2><p>Green: identified. Red: confirmed eligible miss. "
            "Amber: missing evidence. Grey: excluded or deferred by policy.</p>"
            '<div class="table-responsive">'
            '<table class="table table-striped table-hover align-top">'
            "<caption>Direct independent inventory opportunities and evidence behind their outcomes"
            '</caption><thead class="table-light"><tr>'
            + "".join(f'<th scope="col">{text(heading)}</th>' for heading in headings)
            + f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div></section>",
            has_updates=has_updates,
            legacy=True,
            verbose=verbose,
            ecosystems=ecosystems,
            statuses=statuses,
        )
        + '<section class="card mb-4"><div class="card-body"><h2 class="h4">'
        f'Limitations and scope</h2><ul class="mb-0">{limitations}</ul></div></section>'
        '<details class="card"><summary class="card-header">Comparison evidence and policy '
        "context (full observations remain in scan JSON)"
        f'</summary><pre class="card-body mb-0">{text(dump(for_html(report)))}</pre></details>'
        '<footer class="small text-body-secondary mt-4">Self-contained report · '
        "Bootstrap 5.3.8 (MIT) · No scripts or external requests</footer></main></body></html>\n"
    )
