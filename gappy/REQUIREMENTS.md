# Gappy Requirements

## Configuration comparison and conditional PR-demand forecasting

Add a one-repository `forecast` workflow with four independent evaluations: baseline
and optimised for each of Dependabot and Renovate. Baselines MUST select tracked
repository configuration independently per scanner and use Gappy fallback only when
absent. Invalid, conflicting or uncertified existing configurations MUST NOT silently
fall back. Optimised configurations are complete replacements from a separate Gappy
directory, never implicit merges. Keep existing two-scan commands compatible.

Preserve native grouped proposal evidence separately from discovered dependencies and
recommended target counts. Compare all four proposal demands and direct dependency
recommendations side by side in JSON and offline Bootstrap HTML, including same-scanner
deltas, config provenance, statuses, grouped membership and uncertainty.

Require timezone-aware future start/end timestamps and a declared invocation cadence.
Capture paginated GitHub PR state read-only, with explicit token opt-in, once per run.
Model repeated eligible invocations, supported schedule semantics, current PR matching
and concurrency/rate limits against the same captured state independently per scenario.
Separate known proposal demand, conditional predicted new PRs, queued/deferred proposals,
existing matches and unknowns. Never multiply one group by its dependency count or
count the same unchanged proposal on every invocation.

Do not predict merge throughput, future releases, future CI success or unseen security
updates. Predicted creations consume capacity in later invocations without assumed
merges. Missing native/platform evidence, unproven matching/history, unresolved timing
policy and unsupported schedules MUST make affected totals unknown, not zero.
Known demand counts are not guarantees of actual GitHub PR creation. Record native
observation times and mutable external preset/registry limitations.

Persist only allowlisted normalized evidence, never credentials/raw logs/PR bodies,
with versioned contracts and deterministic credential-free offline replay. Original
source checkouts and remote repositories MUST remain unchanged. Preserve partial
scenario evidence and certify grouping/counting with controlled real scanner fixtures.
Forward scanner concurrency and evidence-budget controls through every forecast
scenario. Cancellation MUST stop remaining configurations, preserve diagnostic exports,
and exit 130. Unmodelled PR policies MUST NOT permit exact creation/queue claims.
Validate unique policy scopes and PR identities during offline replay. Baseline/optimised
deltas require matching scanner toolchains, cache modes and registry transport identities;
otherwise show explicit unknown deltas while retaining individual results.

## Current scope amendment: scanner discovery comparison

Timeout reporting amendment: emit scanner timeout diagnostics to stderr with the
provider and known repository-relative job directory. Distinguish the job scope from
an identified dependency or manifest, preserve sanitized warnings in scan exports, and
never print raw scanner output, credentials or registry URLs.
Scanner subprocesses MUST not inherit piped or interactive stdin.

Report filter amendment: offline HTML MUST offer an accessible, initially disabled
toggle that shows only dependencies with a recommendation from either scanner, without
changing JSON or summary metrics. Hide empty ecosystem sections, update section counts,
and show an explicit no-results message without claiming incomplete scans are up to date.
Legacy inventory reports filter to opportunities identified by either scanner and
explain that missed opportunities are hidden. The toggle MUST work without scripts.

Directness amendment: comparison findings and counts MUST exclude transitive and
unclassified dependencies, including in the legacy inventory mode. Keep native scan
observations, annotate directness from scanner evidence, and never infer that a direct
dependency update changes or fixes a particular transitive package. Distinguish unknown
directness with explicit exclusion counts and warnings. Older exports lacking that evidence
remain readable but require a new scan to recover it. Independent legacy inventory
opportunities can explicitly assert directness; otherwise scanner manifest evidence is needed.
Report the limitation that generated/flattened manifests may not distinguish transitives.

Update-summary amendment: JSON and HTML MUST summarize each tool's dependencies with
recommendations and distinct target-version counts per dependency/manifest context, both
overall and by ecosystem. Deduplicate identical recommendations; do not describe counts
as PRs or a complete update inventory when scans are incomplete.

Source TLS amendment: accept `--source-ca PATH` on `scan` and `full`, or an approved
public CA installed at `.gappy-mirror/source-ca.crt` under the config directory.
Validate the PEM, reject keys/invalid inputs, combine it with the selected registry CA
outside source snapshots, pass it to both scanners, record additional trust in warnings,
and clean up the temporary bundle. TLS verification MUST remain enabled. Do not read
credentials/private keys or trust unverified server certificates. A Docker
`private_source_timed_out` diagnostic MUST mention native registry errors and corporate
trust as possible causes, not promise that retry/authentication fixes missing CA trust.

Scanner output-budget amendment: `scan` and `full` have no fixed scanner-evidence
byte ceiling by default. `--renovate-output-limit-mib` optionally sets a 1–256 MiB
ceiling on retained Renovate evidence and individual lines; the selected budget or
its absence MUST be recorded, and an exceeded budget MUST fail without truncation.
Streaming filtering, timeouts, cancellation and no raw-log persistence MUST remain
intact. Explain the memory risk. There MUST NOT be a dependency-count cap of 100.

Source-timeout amendment: native Dependabot `private_source_timed_out` errors trigger
bounded retries only when all reported job errors are source timeouts. Pause 5s then
10s, at most three attempts, with immediate sanitized stderr progress and report history.
Pauses MUST be cancellable. Reuse scanner settings/cache safely, reset retry source to
the original frozen contents, and assess only the final attempt's evidence. Exhaustion
MUST remain incomplete; do not retry authentication/policy failures or confuse execution
timeouts with source timeouts. Per-attempt timeout limits still apply.

Authentication amendment: `scan` and `full` accept `--github-token-env NAME` as explicit
permission to read that environment variable and pass github.com credentials to both
native scanners. Never discover/use host tokens automatically. Missing/invalid selected
variables MUST fail explicitly. Tokens MUST NOT appear in command arguments, generated job
YAML, snapshots, cache configuration, reports or diagnostics; pass via runtime environment
and native credential-proxy mechanisms. Record auth-enabled provenance without credentials,
redact selected tokens/common encodings before parsing output, and preserve incomplete
status for unresolved timeouts or permissions failures.

Performance amendment: Dependabot ecosystems run with bounded concurrency, two workers
by default and an explicit `--dependabot-workers` range of 1–8 on `scan` and `full`.
Jobs sharing an ecosystem/cache namespace MUST remain serialized; independent ecosystems
MUST use isolated temporary source copies. Preserve policies, original job evidence IDs,
deterministic merge order, image provenance and partial/cancelled status. Cancellation
MUST signal active native processes, stop queued work and wait for cleanup before removing
snapshots. Record per-job elapsed times including image/cache preparation. Do not select
an unverified native-architecture image to avoid emulation.

Report browser amendment: `full` and `compare` accept opt-in `--open-chrome`, opening
the HTML only after successful publication. Diagnostic partial reports may open without
changing the non-success scan exit status. Missing Chrome/launch failures MUST be explicit
without deleting the report.

Registry mirror amendment: provide persistent public npm, PyPI, Docker Hub and an explicit
Helm HTTP upstream via Nexus. Mirror routing MUST default to the bundled npm, PyPI,
Docker Hub and HMPPS Helm proxies, with `--no-mirror` disabling defaults while preserving
explicit per-ecosystem overrides. Auto-select the gateway CA from the config directory;
missing CA/setup errors MUST be actionable without silent upstream fallback. Other
ecosystems/private registries retain native routing, not invented mirror URLs.
Mirror routing MUST be fingerprinted and applied only
to disposable inputs/native jobs. Never silently redirect private or unrelated registries,
disable TLS checks or weaken external-code execution safety to obtain a successful scan.
Nexus admin credentials MUST NOT be persisted in source, reports or logs.

Configuration amendment: CLI scans use `dependabot.yml` and `renovate.json` from
Gappy's root (the current directory, or explicit `--config-dir`) by default.
Copy them into the disposable target snapshot at the native paths before hashing
and copying it to both scanners. Never mutate the source checkout or supplied
configs. `--repository-config` explicitly restores target-revision configuration.
Missing supplied configurations MUST fail rather than fall back silently.
Reports MUST distinguish completed eligible no-update lookups from policy
exclusions, deferrals and incomplete/unknown availability, without certifying a
latest published version that the scanner evidence does not establish.

Persistent cache amendment: reuse repository/scanner/image-isolated Docker download
cache volumes by default, separating Dependabot ecosystems. `--no-cache` retains
fresh-cache behavior. Record actual cache mode and volume identities and warn about
metadata freshness. Native cache support/expiry limits apply; do not claim universal
download coverage or offline operation. Never delete persistent caches during normal
run cleanup, and surface initialization/synchronization failures.

This amendment supersedes inventory/patch-coverage requirements below for the
default workflow. Gappy MUST NOT generate its own inventory. Both Docker scanners
MUST use the same frozen local repository configuration and emit every discovered
dependency, including dependencies with no recommended update, across configured
ecosystems rather than npm only. Compare direct native recommended target sets by canonical
ecosystem/package and manifest, retaining installed versions, registry identity,
policy, evidence and scan failures. Recommendations include all update types and
native version schemes, not patches alone.

`full --repository owner/name` and `compare --repository owner/name` MUST need no
inventory. JSON and Bootstrap HTML MUST expose discovery-only differences, different
recommendations, installed-version mismatches, same recommendations, empty results
and unknown evidence. Neither union discovery nor more recommendations establishes
independent coverage, updates missed by both, or an overall winner. Optional supplied
`--inventory` retains legacy patch assessment only. Unsupported managers/rules MUST
remain explicit without preventing other supported entries from executing.

## Goal and Scope

`gappy` is a standalone Python CLI using Click, comparing dependency patch detection by Dependabot and Renovate in `ministryofjustice/hmpps-developer-portal`. Gappy runs both tools through Docker on the same immutable repository snapshot, using their existing configuration, and exports normalized JSON for offline comparison.

The primary output is a clear table showing which patches each tool identified and which detected more. Final PR creation, closure, merge rate, and time-to-merge are not detection performance measures.

## Workflow

`gappy full --repository owner/name [--inventory PATH]` orchestrates the entire
workflow, passing the exact run's exports into comparison without latest-run
discovery. By default it independently generates `inventory.json` from direct npm
manifests/lockfiles and registries on the same frozen revision before scanning.
Supplied inventory bypasses collection. It validates all output destinations before scanning,
retains non-success scan status when producing diagnostic reports, and does not
compare cancelled runs. Inventory must still match the selected source snapshot.

1. `gappy scan` prepares one immutable repository snapshot and orchestrates Docker-backed Dependabot and Renovate scans against isolated copies.
2. Both scans may access registries and required preset sources. They must not create remote PRs, branches, commits, issues, or other remote mutations, nor alter the source checkout.
3. Gappy adapts each tool's native structured results into a versioned JSON scan export. Do not assume both tools emit the same native JSON format.
4. `gappy compare` loads both scan exports and an independently supplied patch inventory, then writes JSON and Bootstrap-styled HTML without network access, Docker, or credentials.

## Scan Inputs and Consistency

- An explicit repository identity and immutable revision or content-addressed snapshot, including manifests, lockfiles, `.github/dependabot.yml`, and `renovate.json`.
- Optional output paths for each provider's JSON export. When omitted, create a unique run directory under `./gappy-results/<owner>/<repository>/` and print both destinations; never overwrite existing files.
- `gappy scan --repository owner/name` must work without other arguments: default to a temporary GitHub checkout and its `HEAD`, with explicit `--source`, `--ref`, and output overrides retained. Acquisition is network-enabled; temporary source content is removed after scanning.
- Pinned scanner/updater/proxy image versions and digests, as applicable, plus adapter and launcher versions. Mutable `latest` references alone are insufficient.
- Existing configuration must be preserved. Required job translation or safety overrides must be recorded; unsupported rules must not be silently ignored.
- Resolved Renovate policy, if available, and preset provenance must be recorded without exposing secrets. Unresolved inherited behavior remains unknown.
- Credentials may be supplied explicitly for private registries or required preset access, but must never appear in exports, reports, persisted logs, or diagnostics.
- Scan metadata must record the shared snapshot/config hashes, tool/image identities, actual scan start/end times, registry observation times where available, and completeness/errors by manager and dependency.
- Scans must use equivalent fresh-cache conditions. Live registries can change between scans: disclose that limitation and mark known availability mismatches not assessable rather than asserting identical historical inputs.
- Arbitrary historical registry replay is not supported. Both exports must belong to the same Gappy scan session; historical PR windows are not the comparison basis.

## Normalized Scan JSON

Each export must contain:

- Schema version, provider, shared scan-session ID, repository/snapshot identity, config hashes, tool/image provenance, timestamps, and completion state.
- Extracted dependencies with ecosystem, package identity, manifest/directory context, installed version, and versioning scheme where known.
- Per-dependency candidate updates with target version, patch classification where supported, policy decision (eligible, excluded, deferred, or unknown), reason/evidence references, and detection status.
- Coverage information, unresolved policy, unsupported capabilities, and sanitized failures. Empty successful results must be distinguishable from failed, cancelled, partial, or unsupported scans.

Native output must be normalized through tested, version-aware adapters. Dependabot job events and Renovate lookup results are not interchangeable. Console text scraping must not be treated as a stable universal API. If a pinned version cannot supply required evidence, report unsupported capability rather than a successful empty result.

## Offline Comparison Inputs

- Both normalized scan JSON exports from the same repository snapshot and scan session.
- A local independent inventory identifying repository/snapshot, observation/as-of metadata, ecosystem/package, manifest/directory context, installed version, available patch version, and release/availability date.
- Optional HTML and JSON output paths, defaulting to `comparison.html` and `comparison.json` beside the selected scan exports without overwriting existing files.
- `compare --repository owner/name --inventory PATH` selects the latest timestamped run under `./gappy-results/<owner>/<repository>/`. Missing/incomplete latest runs are explicit errors; older results must not be silently selected. Explicit export/report path overrides remain supported; inventory is always required.

The inventory remains the reference set, including patches detected by neither tool. Its source and completeness must be disclosed. The union of scanner detections alone cannot establish that neither tool missed a patch.

## Patch Detection and Winner Rules

- Match opportunities using repository/snapshot, ecosystem/package, directory/manifest context, installed version, and candidate patch version. Do not conflate multiple installations of the same dependency.
- Compare extracted dependency coverage separately from patch detection. A dependency not extracted is not a successful “no updates” result.
- “Detected” means a supported native lookup result identified the candidate as an actionable patch under that tool's existing policy; it does not require PR creation. Retain separately identified but excluded/deferred candidates as policy context.
- Configuration alone does not prove detection. Unsupported version semantics, unresolvable rules, failed lookups, and missing evidence produce unknown/not-assessable outcomes.
- A later actionable patch in the same patch line counts for earlier inventory patches available by its detection/lookup time. Do not extend this rule across minor/major lines or unsupported versioning schemes.
- A patch is “not detected” only when relevant extraction/lookup completed and the adapter establishes that the result covers the opportunity. No match in a partial export is unknown.
- For each opportunity show dependency/context, installed and available version, release date, each provider's extraction status, eligibility, detection, evidence, and result: Dependabot only, Renovate only, both, neither, or not assessable.
- If exactly one tool detects a patch and the other is assessable, name that tool the per-opportunity detection winner, showing whether policy exclusion or deferral affected the other tool.
- Summarize all inventory opportunities, one-tool-only eligibility, policy exclusions/deferrals, and unknown results. Compare overall coverage on the shared, jointly eligible and assessable set with explicit equal denominators. Show total jointly eligible opportunities and exclusions from the scored subset.
- Name the tool detecting more on that shared set, a tie, or insufficient evidence when the denominator is zero or evidence cannot support comparison. Qualify any conclusion as applying only to the assessed set.
- No opaque overall quality score, unsupported claims of universal coverage, or security-finding severities.

## Outputs and Safety

- Versioned JSON: metadata, inventory opportunities, normalized scan evidence, extraction and patch metrics, policy differences, recommendations, and warnings/limitations.
- Single self-contained HTML: embedded Bootstrap-compatible styling, accessible static comparisons, clear opportunity/winner table, summary counts and denominators, evidence references, configuration differences, and recommendation rationale.
- Recommendations cite concrete scan/inventory evidence or explicit policy. Source credentials and raw secret-bearing configuration must not be emitted.
- Escape every imported HTML string and validate any rendered link targets. No remote assets or browser-side API calls.
- Never overwrite input or existing output files. HTML and JSON paths must be distinct; automatically chosen report destinations must be printed.
- Validate malformed input, dates, incompatible schemas/snapshots/sessions, missing options, Docker availability, pull/start failures, and output permissions with actionable sanitized errors.
- Scan subprocesses need bounded execution, explicit failure status, and cleanup restricted to containers and temporary files Gappy created. Do not grant scanners a Docker socket or mount the user's home or credentials directories.
- Package-manager execution required by a scanner must remain container-isolated; document any remaining risk and required privileges. Do not enable repository scripts or custom commands silently.

## Current Policy Context

The recorded Dependabot snapshot configures weekly npm, GitHub Actions, Helm, and Docker updates with seven-day cooldowns; groups npm minor/patch updates; and ignores selected updates including `@types/node` majors, HMPPS shared-action minor/patch updates, and Helm patches.

The recorded Renovate snapshot extends a shared Node preset, bumps npm ranges, groups npm minor/patch updates with a seven-day minimum release age, excludes selected major/action updates, groups pre-commit updates, and removes age delay for vulnerability alerts. These descriptions are context, not hardcoded scan results. The actual supplied configuration and supported scanner behavior govern the comparison.

## Verification and Acceptance

1. Docker orchestration supplies identical repository content and preserves provider policy, records pinned provenance, and produces two valid normalized exports without remote writes.
2. Synthetic native-result fixtures verify each adapter, grouped/multi-dependency updates, extraction, filtering, later-patch matching, directory identity, and unsupported version semantics.
3. Comparison fixtures verify only-one/both/neither/unknown outcomes, policy exclusions/deferrals, equal denominators, zero-denominator insufficient evidence, and incomplete scan handling.
4. Scan failures, cancellation, timeouts, missing Docker, authentication errors, and malformed native outputs cannot look like successful empty scans.
5. CLI smoke tests verify JSON/HTML tables, metrics, evidence references, escaping, path protection, and offline operation without credentials or Docker.
6. Orchestration tests use a controlled container or fake process runner; no live registry or GitHub credentials are required by the automated suite.
7. Typecheck, lint, and tests pass. Optional manual live scans validate pinned integrations; document tested capabilities and limitations.

## Out of Scope Initially

- Organization-wide collection/ranking, periodic polling, scanner-database integration, portal routes, or CI integration.
- Historical PR-outcome analytics or requiring PR creation as evidence.
- Generating inventories beyond the limited direct-npm `full` workflow, or replaying historical registries.
- Gappy implementing full remote preset-chain resolution or claiming exact hosted-service equivalence.
- Interactive charts, external JavaScript, or remote report assets.
