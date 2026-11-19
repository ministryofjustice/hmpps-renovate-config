# Planned Quickstart and Validation

## Current default workflow (supersedes inventory preparation below)

```sh
uv run gappy full --repository ministryofjustice/hmpps-developer-portal
uv run gappy compare --repository owner/name
```

`full` writes both native scan exports and comparison JSON/Bootstrap HTML; neither
command requires inventory and neither generates it. Default reports compare every
discovered dependency and recommended target set across ecosystems, with no overall
winner or independent patch coverage. Use explicit scan paths to regenerate reports
without running Docker. `--inventory` selects legacy patch assessment only.

Validation: 160 default tests and ten controlled npm Docker policy scenarios passed;
a separate real Helm Docker scan-to-report scenario passed. Broader ecosystem mappings
are wired but not all certified end-to-end.
Twenty-three sanitized native policy/Helm captures are replayed in the default suite.
Strict mypy, Ruff lint/format and wheel/sdist packaging checks passed. Browser checks
confirmed bundled Bootstrap, native Helm recommendations, mobile table scrolling,
no page overflow and no external assets/scripts. The source checkout was not scanned
against live private registry data.

The package, fixtures, root npm Docker integrations, and offline reports are implemented. Broader configuration/manager support remains incomplete; see `README.md` for current limits and actual usage.

## Prerequisites

- Python 3.12+ and uv for package development; an installed wheel requires Python and its package dependencies, not uv or Node.js.
- Docker and supported pinned scanner toolchains for `scan` only.
- An explicitly selected source revision containing both tool configurations.
- An independent inventory: generated for direct npm dependencies by `full`, or supplied explicitly.
- Explicit authorization and narrowly scoped credentials for private registry/preset access.

## Build and Check

Once implemented, from the package root:

```sh
uv sync --locked --extra dev
uv run --no-sync mypy src/gappy
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest
uv build --python .venv/bin/python
uv run --no-sync gappy --help
```

Declare the development extra and Click console entry point in `pyproject.toml`. Test the current Python source with pytest and Click's `CliRunner`, then install the built wheel in a separate temporary virtual environment to verify the real `gappy` executable and bundled CSS/toolchain resources independently of the source checkout.

## Full Workflow

```sh
uv run gappy full --repository ministryofjustice/hmpps-developer-portal
```

Generates independent `inventory.json` before scanning, then writes both scan exports
and JSON/HTML reports in the same run directory. `--inventory PATH` overrides generation.
Automatic scope is direct npm dependencies with stable installed versions and patch
releases; warnings disclose exclusions and incomplete scope. This does not expand
the adapters' supported portal policies. Registry responses support gzip and are
bounded to 64 MiB decoded; `--registry-ca` applies to inventory collection too.

## Run Both Scanners

After verifying supported pinned integrations:

```sh
uv run gappy scan --repository ministryofjustice/hmpps-developer-portal
```

This clones a temporary checkout, scans default-branch `HEAD`, and prints unique
JSON paths under `./gappy-results/ministryofjustice/hmpps-developer-portal/`.
The portal's unsupported-policy limitations still apply. Override defaults to use
a local checkout, specific revision, or explicit destinations:

```sh
uv run --no-sync gappy scan \
  --repository ministryofjustice/hmpps-developer-portal \
  --source /absolute/path/to/portal-checkout \
  --ref <commit-sha> \
  --dependabot-json /absolute/path/to/results/dependabot.scan.json \
  --renovate-json /absolute/path/to/results/renovate.scan.json
```

Use real values for placeholders and new, distinct output paths. The source checkout must remain unchanged. Review run statuses, manager support, policy translation, image digests, snapshot identity, and registry observation differences before interpreting coverage.

## Compare Offline

To use the latest repository scan with default report destinations:

```sh
uv run gappy compare \
  --repository ministryofjustice/hmpps-developer-portal \
  --inventory /absolute/path/to/inventory.json
```

The inventory remains required. Reports are written beside the selected scan exports
as `comparison.json` and `comparison.html`; existing files are never overwritten.
Explicit inputs and destinations remain supported:

```sh
uv run --no-sync gappy compare \
  --dependabot-json /absolute/path/to/results/dependabot.scan.json \
  --renovate-json /absolute/path/to/results/renovate.scan.json \
  --inventory /absolute/path/to/inventory.json \
  --json /absolute/path/to/results/comparison.json \
  --html /absolute/path/to/results/comparison.html
```

For an installed package, invoke `gappy` directly without the `uv run --no-sync` prefix. This step needs neither Docker nor registry/GitHub credentials. Open the HTML locally. Check the per-opportunity winner table, shared-denominator coverage, unknowns, exclusions, and inventory completeness.

## Required Automated Acceptance Runs

1. Synthetic native fixtures prove each pinned adapter's extraction, policy, actionable patches, groups, and failure handling.
2. Fake process execution verifies Docker arguments, shared source, isolated mounts/cache, timeout/cancellation, failure exports, and resource ownership.
3. Offline CLI fixtures produce both outputs with exact metric/table assertions and no Docker dependency.
4. Four jointly eligible assessable opportunities yield Dependabot `3/4` and Renovate `2/4`; adding policy-excluded or unknown opportunities does not inflate the paired denominator.
5. An empty assessed set produces nullable coverage and insufficient evidence; equal nonempty counts produce a tie.
6. Later-patch evidence covers earlier available patches on the same supported line, but not future releases or other minor/major lines.
7. Markup, unsafe URLs, secret canaries, malformed data, incompatible sessions, and aliased output/input paths are handled safely.
8. Repeated comparison of identical inputs produces byte-identical JSON and HTML.

Manual live scanner verification is separate and optional for routine tests. Record tool versions/digests and supported capabilities; never treat an untested image or successful empty result as integration proof.

## Recorded Validation (2026-11-11)

- Runtime: Python 3.14.8 on macOS ARM64. The declared Python 3.12 minimum has not yet been runtime-tested here.
- Ruff lint/format checks and strict mypy passed.
- Default pytest suite: 50 passed; the opt-in Docker test is excluded by default.
- Real pinned Docker integration: passed against the controlled local HTTPS npm registry. It collected both exports and produced offline reports with two detections per tool, denominator 2, and a tie.
- Wheel and sdist built successfully. A fresh temporary environment installed the wheel and verified the console entry point and CSS/toolchain resources outside the checkout.
- Initial tooling issues were resolved using system TLS certificates and the existing Python interpreter for builds. A clean offline installation lacked cached dependency metadata, so package-install verification fetched public dependencies over trusted TLS. Report comparison itself remains offline.
- This validates the root npm baseline only. Full portal-policy translation, other managers, private credentials, exhaustive failure/cancellation coverage, and broader feasibility scenarios remain open. No private repository or live public-registry scan was performed.
