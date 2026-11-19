"""Configuration provenance badges shared by dependency reports."""

from html import escape
from typing import Any


def configuration_badges(references: list[dict[str, Any]]) -> str:
    return "".join(
        '<br><span class="badge text-bg-secondary">'
        + escape(ref["id"])
        + "</span> "
        + escape(", ".join(ref["labels"]))
        + " ("
        + escape(ref["basis"])
        + ")"
        for ref in references
    )
