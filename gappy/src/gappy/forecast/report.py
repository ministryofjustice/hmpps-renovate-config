"""Offline Bootstrap report with four comparable configuration columns."""

from html import escape
from importlib.resources import files

from gappy.compare.discovered import installed_version_key
from gappy.forecast.engine import forecast
from gappy.forecast.models import ForecastInput
from gappy.report.filter import filter_attributes, update_filter
from gappy.report.metadata import configuration_badges


def render(inputs: ForecastInput, *, verbose: bool = False) -> str:
    report = forecast(inputs)
    comparison_warnings = report["comparisonWarnings"]
    if not isinstance(comparison_warnings, list):
        raise TypeError("Expected configuration comparison limitations.")
    comparison_alerts = "".join(
        f"<p class='alert alert-warning'>{escape(warning)}</p>" for warning in comparison_warnings
    )
    styles = "\n".join(
        files("gappy.report").joinpath(name).read_text()
        for name in ("bootstrap.min.css", "styles.css")
    )
    headings = ""
    rows = {
        name: ""
        for name in (
            "Config origin",
            "Scan",
            "Observed proposal groups",
            "Known potential PRs",
            "Predicted new PRs",
            "Queued at window end",
            "Schedule-deferred",
            "Existing matches",
            "Unknown observed proposals",
        )
    }
    details = []
    for variant in inputs.variants:
        from gappy.forecast.engine import forecast_variant

        result = forecast_variant(inputs, variant)
        name = f"{variant.provider.title()} / {variant.label}"
        headings += f"<th>{escape(name)}</th>"
        values = (
            variant.configOrigin,
            variant.scan.status,
            result.observedPotential,
            result.potentialPRs,
            result.predictedNew,
            result.queued,
            result.deferred,
            result.existing,
            result.unknown,
        )
        for key, value in zip(rows, values, strict=True):
            rows[key] += "<td>" + ("Unknown" if value is None else escape(str(value))) + "</td>"
        proposals = "".join(
            "<tr><td>"
            + escape(", ".join(f"{m.package} -> {m.target}" for m in d.proposal.members))
            + "</td><td>"
            + escape(d.state)
            + "</td><td>"
            + escape(d.reason)
            + "</td></tr>"
            for d in result.decisions
        )
        warnings = (
            result.warnings
            + variant.scan.warnings
            + [reason for p in variant.policies for reason in p.unknown]
        )
        ecosystems = "".join(
            '<tr><th><span class="badge text-bg-secondary">'
            + escape(ecosystem)
            + "</span></th>"
            + "".join(
                "<td>" + ("Unknown" if count is None else str(count)) + "</td>"
                for count in (counts["observedPotential"], counts["predictedNew"], counts["queued"])
            )
            + "</tr>"
            for ecosystem, counts in result.byEcosystem.items()
        )
        details.append(
            f"<section class='card my-3'><div class='card-body'><h2 class='h5'>{escape(name)}</h2>"
            f"<p>Config SHA-256: <code>{escape(variant.configHash)}</code></p>"
            "<div class='table-responsive'><table class='table table-sm'><thead><tr>"
            "<th>Native proposal members</th><th>Forecast</th><th>Reason</th></tr></thead>"
            f"<tbody>{proposals}</tbody></table></div><details><summary>Evidence and limitations"
            "</summary><ul>"
            + "".join(f"<li>{escape(w)}</li>" for w in warnings)
            + "</ul></details><div class='table-responsive'><table class='table table-sm'>"
            "<caption>Group memberships by ecosystem (may overlap)</caption><thead><tr>"
            "<th>Ecosystem</th><th>Observed groups</th><th>Predicted new</th><th>Queued</th>"
            f"</tr></thead><tbody>{ecosystems}</tbody></table></div></div></section>"
        )
    deltas = report["deltas"]
    if not isinstance(deltas, dict):
        raise TypeError("Expected scanner configuration deltas.")
    delta_rows = []
    for provider, counts in deltas.items():
        delta_rows.append(
            "<tr><th>"
            + escape(provider.title())
            + "</th>"
            + "".join(
                "<td>" + ("Unknown" if count is None else str(count)) + "</td>"
                for count in (counts["potentialPRs"], counts["predictedNew"])
            )
            + "</tr>"
        )
    dependencies = report["dependencies"]
    if not isinstance(dependencies, list):
        raise TypeError("Expected direct dependency comparisons.")
    dependency_rows = []
    has_updates = False
    filter_ecosystems: set[str] = set()
    statuses: set[str] = set()
    for row in dependencies:
        cells = ""
        versions = set()
        recommended = False
        for variant in inputs.variants:
            side = row["variants"].get(f"{variant.provider}.{variant.label}")
            versions.add(
                tuple(
                    sorted({installed_version_key(row["ecosystem"], v) for v in side["installed"]})
                )
                if side is not None
                else None
            )
            recommended = recommended or bool(side is not None and side["recommended"])
            cells += (
                "<td>"
                + (
                    "Not observed as direct"
                    if side is None
                    else escape(", ".join(side["installed"]))
                    + "<br>Recommended: "
                    + escape(", ".join(side["recommended"]) or "None observed")
                    + configuration_badges(side.get("configuration", []))
                )
                + "</td>"
            )
        has_change = recommended or len(versions) > 1
        ecosystem = row["ecosystem"]
        status = "Recommendation observed" if recommended else "No recommendation observed"
        filter_ecosystems.add(ecosystem)
        statuses.add(status)
        has_updates = has_updates or has_change
        dependency_rows.append(
            f'<tr data-has-updates="{str(has_change).lower()}" '
            f"{filter_attributes(ecosystem, [status])}><th>"
            + '<span class="badge text-bg-secondary">'
            + escape(row["ecosystem"])
            + "</span> "
            + escape(f"{row['package']} ({row['manifest']})")
            + "</th>"
            + cells
            + "</tr>"
        )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Gappy configuration forecast</title><style>{styles}</style></head>"
        "<body><main class='container-fluid p-4'><h1>Configuration PR-demand forecast</h1>"
        f"<p>{escape(inputs.repository)} / revision <code>{escape(inputs.revision)}</code></p>"
        f"<p>{escape(inputs.window.start)} to {escape(inputs.window.end)} (end exclusive); "
        f"invocation every {inputs.window.invocationMinutes} minutes.</p>"
        "<div class='alert alert-warning'>Predictions, not actual PRs. Known version-update "
        "demand only. No future releases, merges or successful CI are assumed. "
        "Unknown is not zero. "
        "Native lookup cannot prove all future age-deferred or security demand.</div>"
        "<div class='table-responsive'><table class='table table-bordered align-middle'>"
        f"<thead><tr><th>Measure</th>{headings}</tr></thead><tbody>"
        + "".join(f"<tr><th>{escape(name)}</th>{cells}</tr>" for name, cells in rows.items())
        + "</tbody></table></div><h2 class='h5'>Optimised minus baseline</h2>"
        + comparison_alerts
        + "<table class='table table-sm'><thead><tr><th>Scanner</th><th>Known potential PR delta"
        "</th><th>Predicted new PR delta</th></tr></thead><tbody>"
        + "".join(delta_rows)
        + "</tbody></table>"
        "<h2 class='h5'>Direct dependency recommendations</h2>"
        "<p>Transitive and unclassified observations are excluded from this table, not from "
        "native proposal membership. No recommendation in an incomplete scan is not an "
        "up-to-date claim.</p>"
        + update_filter(
            "<div class='table-responsive'><table class='table table-sm'>"
            f"<thead><tr><th>Dependency</th>{headings}</tr></thead><tbody>"
            + "".join(dependency_rows)
            + "</tbody></table></div>",
            has_updates=has_updates,
            verbose=verbose,
            ecosystems=filter_ecosystems,
            statuses=statuses,
        )
        + "".join(details)
        + "</main></body></html>"
    )
