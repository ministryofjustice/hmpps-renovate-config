"""Transparent relative evidence scores, without treating green rows as wins."""

from typing import Any


def score_configurations(
    dependencies: list[dict[str, Any]], variants: list[dict[str, Any]]
) -> dict[str, Any]:
    total = len(dependencies)
    target_rows = [
        row
        for row in dependencies
        if any(side["recommendations"] for side in row["variants"].values())
    ]
    weights = (
        {"discovery": 40, "resolution": 40, "recommendations": 20}
        if target_rows
        else {"discovery": 50, "resolution": 50, "recommendations": 0}
    )
    results = []
    for variant in variants:
        key = variant["id"]
        complete = variant["scan"]["status"] == "complete"
        sides = [row["variants"].get(key) for row in dependencies]
        discovered = sum(bool(side and side["discovered"]) for side in sides)
        resolved = sum(
            bool(
                complete
                and side
                and side["discovered"]
                and side["installedVersions"]
                and (
                    (side["policy"] == ["eligible"] and side["lookupComplete"])
                    or (
                        side["policy"] == ["deferred"]
                        and (side["lookupComplete"] or side["recommendations"])
                    )
                )
            )
            for side in sides
        )
        recommendations = sum(
            bool(
                (side := row["variants"].get(key))
                and side["discovered"]
                and side["recommendations"]
            )
            for row in target_rows
        )
        components = {
            "discovery": {
                "count": discovered,
                "total": total,
                "points": weights["discovery"] * discovered / total if total else 0,
            },
            "resolution": {
                "count": resolved,
                "total": total,
                "points": weights["resolution"] * resolved / total if total else 0,
            },
            "recommendations": {
                "count": recommendations,
                "total": len(target_rows),
                "points": (
                    weights["recommendations"] * recommendations / len(target_rows)
                    if target_rows
                    else 0
                ),
            },
        }
        results.append(
            {
                "id": key,
                "score": round(sum(part["points"] for part in components.values()), 2)
                if total
                else None,
                "status": variant["scan"]["status"],
                "components": components,
            }
        )
    ranked = sorted(results, key=lambda item: -(item["score"] or 0))
    for item in ranked:
        item["rank"] = (
            1 + sum(other["score"] > item["score"] for other in ranked)
            if item["score"] is not None
            else None
        )
    provisional = any(item["status"] != "complete" for item in results)
    leaders = [item["id"] for item in ranked if item["rank"] == 1]
    return {
        "method": "observed-evidence-v1",
        "weights": weights,
        "dependencies": total,
        "provisional": provisional,
        "winner": leaders[0] if not provisional and len(leaders) == 1 else None,
        "leaders": leaders,
        "variants": ranked,
    }
