"""Conservative identities for native installed references."""

import re


def pinned_digest(ecosystem: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if ecosystem == "github-actions" and re.fullmatch(r"[a-fA-F0-9]{40}", value):
        return value.lower()
    if ecosystem == "docker":
        match = re.fullmatch(r"(?:[^@\s]+@)?sha256:([a-fA-F0-9]{64})", value)
        if match:
            return "sha256:" + match[1].lower()
    return None
