"""Compact HTML evidence; complete native observations remain in scan exports."""

from typing import Any


def for_html(report: dict[str, Any]) -> dict[str, Any]:
    context = dict(report)
    context["scans"] = [
        {key: value for key, value in scan.items() if key != "observations"}
        for scan in report["scans"]
    ]
    if "inventory" in report:
        context["inventory"] = {
            key: value for key, value in report["inventory"].items() if key != "opportunities"
        }
    return context
