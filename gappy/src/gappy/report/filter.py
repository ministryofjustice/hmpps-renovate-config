"""Script-free filters for dependency tables."""

import hashlib
from html import escape
from itertools import product


def filter_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def filter_attributes(ecosystem: str, statuses: list[str]) -> str:
    return (
        f'data-filter-row data-filter-ecosystem="{filter_token(ecosystem)}" '
        f'data-filter-status="{" ".join(filter_token(status) for status in sorted(set(statuses)))}"'
    )


def table_filters(ecosystems: set[str], statuses: set[str]) -> str:
    groups = (
        ("ecosystem", "Ecosystem / manager", sorted(ecosystems)),
        ("status", "Result", sorted(statuses)),
    )
    controls = []
    for key, title, values in groups:
        if not values:
            continue
        buttons = []
        for value in [None, *values]:
            token = "all" if value is None else filter_token(value)
            identifier = f"filter-{key}-{token}"
            buttons.append(
                f'<label class="table-filter-choice"><input type="radio" name="filter-{key}" '
                f'id="{identifier}" value="{token}"'
                + (" checked" if value is None else "")
                + f"> {escape('All' if value is None else value)}</label>"
            )
        controls.append(
            f'<fieldset class="table-filter-group"><legend>{title}</legend>{"".join(buttons)}'
            "</fieldset>"
        )
    if not controls:
        return ""
    rules = []
    ecosystem_options = [None, *sorted(ecosystems)] if ecosystems else [None]
    status_options = [None, *sorted(statuses)] if statuses else [None]
    for ecosystem, status, focused in product(ecosystem_options, status_options, (False, True)):
        conditions = []
        if ecosystems:
            token = filter_token(ecosystem) if ecosystem is not None else "all"
            conditions.append(f":has(#filter-ecosystem-{token}:checked)")
        if statuses:
            token = filter_token(status) if status is not None else "all"
            conditions.append(f":has(#filter-status-{token}:checked)")
        conditions.append(
            ":has(#updates-only:checked)" if focused else ":not(:has(#updates-only:checked))"
        )
        base = ".dependency-filter" + "".join(conditions) + " .filtered-dependencies"
        row = "tr[data-filter-row]"
        if ecosystem is not None:
            row += f'[data-filter-ecosystem="{filter_token(ecosystem)}"]'
        if status is not None:
            row += f'[data-filter-status~="{filter_token(status)}"]'
        if focused:
            row += '[data-has-updates="true"]'
        if ecosystem is not None or status is not None:
            rules.append(f"{base} tr[data-filter-row]:not({row}) {{ display: none; }}")
        rules.append(f"{base} [data-filter-section]:not(:has({row})) {{ display: none; }}")
        rules.append(f"{base}:not(:has({row})) .table-filter-empty {{ display: block; }}")
        rules.append(f"{base}:not(:has({row})) .updates-only-empty {{ display: none; }}")
    return (
        '<div class="table-filter-controls">'
        + "".join(controls)
        + '<p class="small text-body-secondary">Result matches any scanner in a row where '
        "multiple results are shown. Filters combine with the updates-only toggle; "
        "summary counts are not filtered.</p></div>" + "<style>" + "\n".join(rules) + "</style>"
    )


def update_filter(
    content: str,
    *,
    has_updates: bool,
    legacy: bool = False,
    verbose: bool = False,
    ecosystems: set[str] | None = None,
    statuses: set[str] | None = None,
) -> str:
    explanation = (
        "Shows patch opportunities identified by either scanner. Opportunities missed by both "
        "scanners are hidden. Summary counts are not filtered."
        if legacy
        else "Shows dependencies with recommended updates or scanner differences in discovery, "
        "installed versions, registries or recommendations. Summary counts are not filtered. "
        "Uncheck to show all dependencies, or generate with --verbose. Incomplete scans may "
        "have missing updates; unknown availability or absence from an incomplete scan alone "
        "is not a detected change."
    )
    empty = (
        '<p class="alert alert-info updates-only-empty">'
        "No updates were identified by either scanner. Missed or unassessed opportunities "
        "may still exist.</p>"
        if legacy and not has_updates
        else '<p class="alert alert-info updates-only-empty">'
        "No recommended updates or scanner differences were reported. Incomplete scans may have "
        "missing updates; this does not prove dependencies are current.</p>"
        if not has_updates
        else ""
    )
    return (
        '<div class="dependency-filter mb-4">'
        '<input class="form-check-input" type="checkbox" id="updates-only" '
        'aria-controls="dependency-results" aria-describedby="updates-filter-help"'
        + ("" if verbose else " checked")
        + ">"
        '<label class="form-check-label ms-2" for="updates-only">'
        + (
            "Only show dependencies with recommended updates"
            if legacy
            else "Only show updates or scanner differences"
        )
        + "</label>"
        '<span class="filter-state filter-state-focused" role="status">Focused view</span>'
        '<span class="filter-state filter-state-all" role="status">All dependencies</span>'
        f'<p class="small text-body-secondary mt-2" id="updates-filter-help">{explanation}</p>'
        + table_filters(ecosystems or set(), statuses or set())
        + f'<div class="filtered-dependencies" id="dependency-results">{content}{empty}'
        '<p class="alert alert-info table-filter-empty" role="status">No dependencies match '
        "the selected filters. This does not prove dependencies are current.</p></div></div>"
    )
