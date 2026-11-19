"""Per-scanner update status for configuration comparison cells."""

from typing import Any

from gappy.compare.discovered import installed_version_key


def traffic_lights(
    row: dict[str, Any], variants: list[dict[str, Any]]
) -> dict[str, tuple[str, str]]:
    sides = row["variants"]
    lights = {}
    for variant in variants:
        key = variant["id"]
        side = sides.get(key)
        if (
            side
            and side["discovered"]
            and side["policy"] == ["deferred"]
            and side["recommendations"]
        ):
            lights[key] = ("primary", "Deferred — waiting for policy eligibility")
        elif variant["scan"]["status"] != "complete":
            lights[key] = ("warning", "Incomplete scan — update status unknown")
        elif not side or not side["discovered"]:
            lights[key] = ("warning", "Not discovered — update status unknown")
        elif side["policy"] == ["deferred"] and (side["lookupComplete"] or side["recommendations"]):
            lights[key] = ("primary", "Deferred — waiting for policy eligibility")
        elif not side["lookupComplete"] or side["policy"] != ["eligible"]:
            lights[key] = ("warning", "No applicable policy or update status unknown")
        elif not side["installedVersions"]:
            lights[key] = ("warning", "Current version unknown")
        elif side["recommendations"] and {
            installed_version_key(row["ecosystem"], v) for v in side["installedVersions"]
        } == {installed_version_key(row["ecosystem"], v) for v in side["recommendations"]}:
            lights[key] = ("success", "Current matches this scanner's recommendation")
        elif side["recommendations"]:
            lights[key] = ("danger", "Out of date — update recommended")
        else:
            lights[key] = ("success", "Up to date — no update recommended")
    return lights
