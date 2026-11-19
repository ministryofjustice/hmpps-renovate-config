from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest
from click.testing import CliRunner

from gappy.cli import main
from gappy.compare.assess import compare as compare_inventory
from gappy.compare.configurations import compare_configurations
from gappy.compare.discovered import compare
from gappy.report.filter import filter_attributes, filter_token, table_filters
from gappy.report.html import render
from tests.test_compare import inventory, scan


class FilterMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.checkbox: dict[str, str | None] | None = None
        self.rows: list[str] = []
        self.sections: list[str] = []
        self.filter_rows: list[dict[str, str | None]] = []
        self.radio_buttons: list[dict[str, str | None]] = []
        self.filter_sections: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "input" and values.get("id") == "updates-only":
            self.checkbox = values
        if tag == "input" and values.get("type") == "radio":
            self.radio_buttons.append(values)
        if tag == "tr" and "data-has-updates" in values:
            self.rows.append(str(values["data-has-updates"]))
        if tag == "tr" and "data-filter-row" in values:
            self.filter_rows.append(values)
        if tag == "section" and "data-has-updates" in values:
            self.sections.append(str(values["data-has-updates"]))
        if tag == "section" and "data-filter-section" in values:
            self.filter_sections.append(values)


@pytest.mark.parametrize("mode", ["scanner", "configuration", "inventory"])
def test_table_filters_tag_rows_and_expose_results(mode: str) -> None:
    left, right = scan("dependabot", ["1.2.1", None]), scan("renovate", [None, None])
    if mode == "configuration":
        report = compare_configurations((left, right), (left, right))
        statuses = [{"Out of date", "Up to date"}, {"Up to date"}]
    elif mode == "inventory":
        report = compare_inventory(left, right, inventory(2))
        statuses = [{row["result"].replace("-", " ").capitalize()} for row in report["assessments"]]
    else:
        report = compare(left, right)
        statuses = [
            {row["result"].replace("-", " ").capitalize()} for row in report["dependencies"]
        ]

    output = render(report)
    parsed = FilterMarkup()
    parsed.feed(output)
    assert len(parsed.filter_rows) == 2
    for row, expected in zip(parsed.filter_rows, statuses, strict=True):
        assert row["data-filter-ecosystem"] == filter_token("npm")
        assert set(str(row["data-filter-status"]).split()) == {
            filter_token(status) for status in expected
        }
    radio_ids = {button["id"] for button in parsed.radio_buttons}
    assert "filter-ecosystem-all" in radio_ids
    assert "filter-status-all" in radio_ids
    assert f"filter-ecosystem-{filter_token('npm')}" in radio_ids
    for status in set().union(*statuses):
        assert f"filter-status-{filter_token(status)}" in radio_ids
        assert f"> {status}</label>" in output
    assert sum("checked" in button for button in parsed.radio_buttons) == 2
    assert len(parsed.filter_sections) == (0 if mode == "configuration" else 1)
    assert "No dependencies match the selected filters." in output
    assert "<script" not in output


def test_filter_values_are_escaped_and_selectors_are_safe() -> None:
    ecosystem = 'npm"><script>alert(1)</script>'
    status = '</style><script>alert("status")</script>'
    output = table_filters({ecosystem}, {status})
    assert "&lt;script&gt;" in output
    assert "&lt;/style&gt;" in output
    assert "<script>" not in output
    assert output.count("</style>") == 1
    assert ecosystem not in output and status not in output
    assert filter_attributes(ecosystem, [status, status]) == (
        f'data-filter-row data-filter-ecosystem="{filter_token(ecosystem)}" '
        f'data-filter-status="{filter_token(status)}"'
    )
    assert table_filters(set(), set()) == ""


def test_generated_selectors_combine_all_filters_and_hide_empty_sections() -> None:
    ecosystem = filter_token("npm")
    status = filter_token("Out of date")
    css = table_filters({"npm"}, {"Out of date"})
    base = (
        f".dependency-filter:has(#filter-ecosystem-{ecosystem}:checked)"
        f":has(#filter-status-{status}:checked):has(#updates-only:checked) "
        ".filtered-dependencies"
    )
    matching = (
        f'tr[data-filter-row][data-filter-ecosystem="{ecosystem}"]'
        f'[data-filter-status~="{status}"][data-has-updates="true"]'
    )
    assert f"{base} tr[data-filter-row]:not({matching}) {{ display: none; }}" in css
    assert f"{base} [data-filter-section]:not(:has({matching}))" in css
    assert f"{base}:not(:has({matching})) .table-filter-empty" in css
    assert f"{base}:not(:has({matching})) .updates-only-empty" in css


@pytest.mark.parametrize("mode", ["scanner", "configuration", "inventory"])
def test_dependency_ecosystem_has_secondary_badge(mode: str) -> None:
    left, right = scan("dependabot", ["1.2.1"]), scan("renovate", ["1.2.1"])
    if mode == "configuration":
        report = compare_configurations((left, right), (left, right))
    elif mode == "inventory":
        report = compare_inventory(left, right, inventory(1))
    else:
        report = compare(left, right)
    output = render(report)
    assert '<span class="badge text-bg-secondary">npm</span>' in output


@pytest.mark.parametrize("verbose", [False, True])
def test_scanner_report_filter_includes_either_provider(verbose: bool) -> None:
    left = scan("dependabot", ["1.2.1", None, None, None])
    right = scan("renovate", [None, "1.2.2", None, None])
    for model in (left, right):
        item = model.observations[3]
        model.observations[3] = item.model_copy(
            update={"dependency": item.dependency.model_copy(update={"ecosystem": "docker"})}
        )
    report = compare(left, right)
    original_metrics = deepcopy(report["metrics"])
    output = render(report, verbose=verbose)
    parsed = FilterMarkup()
    parsed.feed(output)

    assert parsed.checkbox is not None
    assert parsed.checkbox["type"] == "checkbox"
    assert ("checked" in parsed.checkbox) is not verbose
    assert parsed.checkbox["aria-controls"] == "dependency-results"
    assert parsed.sections == ["false", "true"]
    assert parsed.rows == ["false", "true", "true", "false"]
    assert '<span class="badge text-bg-secondary unfiltered-count">3</span>' in output
    assert '<span class="badge text-bg-secondary updates-filter-count">2</span>' in output
    assert (
        '#updates-only:checked ~ .filtered-dependencies [data-has-updates="false"],\n'
        "#updates-only:checked ~ .filtered-dependencies .unfiltered-count { display: none; }"
    ) in output
    assert (
        "#updates-only:checked ~ .filtered-dependencies .updates-filter-count "
        "{ display: inline-block; }"
    ) in output
    assert (
        "#updates-only:checked ~ .filtered-dependencies .updates-only-empty { display: block; }"
    ) in output
    assert "scanner differences" in output
    assert "Summary counts are not filtered" in output
    assert report["metrics"] == original_metrics
    assert "<script" not in output


def test_filter_has_explicit_empty_state_for_incomplete_scans() -> None:
    left = scan("dependabot", [None]).model_copy(
        update={"status": "partial", "warnings": ["Synthetic timeout."]}
    )
    output = render(compare(left, scan("renovate", [None])))
    parsed = FilterMarkup()
    parsed.feed(output)
    assert parsed.rows == ["false"]
    assert parsed.sections == ["false"]
    assert "No recommended updates or scanner differences were reported" in output
    assert "Incomplete scans may have missing updates" in output


def test_legacy_report_filter_tracks_identification_by_either_scanner() -> None:
    report = compare_inventory(
        scan("dependabot", ["1.2.1", None, None]),
        scan("renovate", [None, "1.2.1", None]),
        inventory(3),
    )
    output = render(report)
    parsed = FilterMarkup()
    parsed.feed(output)
    assert parsed.checkbox is not None
    assert "checked" in parsed.checkbox
    assert parsed.rows == ["true", "true", "false"]
    assert parsed.sections == ["true"]
    assert "missed by both scanners are hidden" in output
    assert report["metrics"]["opportunities"] == 3


def test_legacy_filter_explains_when_no_opportunities_were_identified() -> None:
    report = compare_inventory(scan("dependabot", [None]), scan("renovate", [None]), inventory(1))
    output = render(report)
    parsed = FilterMarkup()
    parsed.feed(output)
    assert parsed.rows == ["false"]
    assert parsed.sections == ["false"]
    assert "No updates were identified by either scanner" in output
    assert "Missed or unassessed opportunities may still exist" in output


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("discovery", "true"),
        ("installed", "true"),
        ("registry", "true"),
        ("unknown", "false"),
        ("none", "false"),
    ],
)
def test_default_filter_keeps_scanner_differences_without_recommendations(
    change: str,
    expected: str,
) -> None:
    left = scan("dependabot", [None])
    right = scan("renovate", [] if change == "discovery" else [None])
    if change in {"installed", "registry"}:
        dep = right.observations[0].dependency
        right.observations[0] = right.observations[0].model_copy(
            update={
                "dependency": dep.model_copy(
                    update={"installedVersion": "2.0.0"}
                    if change == "installed"
                    else {"registry": "https://other.example"}
                )
            }
        )
    if change == "unknown":
        right.observations[0] = right.observations[0].model_copy(update={"lookupComplete": False})
    report = compare(left, right)
    parsed = FilterMarkup()
    parsed.feed(render(report))
    assert parsed.rows == [expected]
    assert parsed.sections == [expected]
    assert parsed.checkbox is not None and "checked" in parsed.checkbox


@pytest.mark.parametrize("verbose", [False, True])
def test_compare_cli_verbose_changes_only_html(tmp_path: Path, verbose: bool) -> None:
    from gappy.io import dump

    left, right = scan("dependabot", [None]), scan("renovate", [None])
    for model in (left, right):
        (tmp_path / f"{model.provider}.scan.json").write_text(dump(model.model_dump()))
    result = CliRunner().invoke(
        main,
        [
            "compare",
            "--dependabot-json",
            str(tmp_path / "dependabot.scan.json"),
            "--renovate-json",
            str(tmp_path / "renovate.scan.json"),
            *(["--verbose"] if verbose else []),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = FilterMarkup()
    parsed.feed((tmp_path / "comparison.html").read_text())
    assert parsed.checkbox is not None
    assert ("checked" in parsed.checkbox) is not verbose
    assert (tmp_path / "comparison.json").read_text() == dump(compare(left, right))
