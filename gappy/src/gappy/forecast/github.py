"""Read-only, paginated GitHub PR snapshot with no durable token or PR body."""

import json

from gappy.forecast.models import PlatformState, PullRequest
from gappy.io import GappyError
from gappy.scan.process import run
from gappy.scan.session import environment, now


def capture(repository: str, token: str | None) -> PlatformState:
    captured = now()
    env = environment()
    if token is None:
        return PlatformState(
            repository=repository,
            capturedAt=captured,
            baseBranch="",
            complete=False,
            pullRequests=[],
            warnings=[
                "GitHub state capture requires explicit --github-token-env NAME. "
                "Creation and queue totals are unknown without captured PR state."
            ],
        )
    env["GH_TOKEN"] = token
    base = ""
    try:
        code, output = run(["gh", "api", f"repos/{repository}", "--jq", ".default_branch"], env=env)
        if code or not output.strip():
            raise GappyError("Cannot read repository default branch.")
        base = output.strip()
        code, output = run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repository}/pulls?state=all&per_page=100",
                "--jq",
                '[.[][] | select(.user.login == "dependabot[bot]" '
                'or .user.login == "renovate[bot]")'
                ' | {number, provider: (if .user.login == "dependabot[bot]" then "dependabot"'
                ' else "renovate" end), branch: .head.ref, base: .base.ref, '
                "state, createdAt: .created_at}]",
            ],
            timeout=180,
            env=env,
        )
        if code:
            raise GappyError("Cannot capture complete paginated bot PR state.")
        records = json.loads(output)
        if not isinstance(records, list):
            raise GappyError("GitHub PR state is not an array.")
        prs = [PullRequest.model_validate(p) for p in records]
    except (GappyError, ValueError):
        return PlatformState(
            repository=repository,
            capturedAt=captured,
            baseBranch=base,
            complete=False,
            pullRequests=[],
            warnings=[
                "GitHub state capture failed or was incomplete. Check gh authentication and "
                "read permissions. PR creation and queue totals are unknown; raw errors withheld."
            ],
        )
    return PlatformState(
        repository=repository,
        capturedAt=captured,
        baseBranch=base,
        complete=True,
        pullRequests=prs,
        warnings=[
            "PR identity capture recognizes dependabot[bot] and renovate[bot] only. Custom bot "
            "identities and non-PR branch history are not certified."
        ],
    )
