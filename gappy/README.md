# Gappy

## Baseline versus optimised configuration forecasts

`forecast` evaluates **four independent scenarios** for one repository: Dependabot
baseline/optimised and Renovate baseline/optimised. Results appear together in an
offline Bootstrap report, including native proposal-group counts, conditional new-PR
predictions, queues, schedule deferrals, existing matches and unknowns. A grouped
proposal counts once, regardless of how many dependencies it contains.

```bash
gappy forecast --repository ministryofjustice/hmpps-developer-portal \
  --fallback-config-dir /absolute/path/to/gappy \
  --window-start 2026-12-01T00:00:00Z \
  --window-end 2027-01-01T00:00:00Z \
  --github-token-env GAPPY_GITHUB_TOKEN
```

Choose a future window that starts after all four scans can finish. Both endpoints
must include a timezone; the end is exclusive. `--invocation-minutes` declares the
scenario's invocation cadence (default 60), not GitHub's actual service timing.
Windows are bounded to 10,000 invocations; larger simulations fail validation explicitly.
Pass `--source` for an existing checkout, otherwise Gappy clones temporarily.
Mirror, CA, fresh/persistent cache and timeout options are available in command help.
`--dependabot-workers` and `--renovate-output-limit-mib` forward the same concurrency
and optional evidence-budget controls as `scan`. Use `--open-chrome` to view the
published report. Cancelling a native evaluation stops remaining configurations,
publishes diagnostic outputs with unknown totals for unrun scenarios, and exits 130.

Configuration precedence is **per scanner**:

1. Baseline uses its tracked repository config, when present.
2. Only an absent repository config falls back to `dependabot.yml` or `renovate.json`
   in the explicit `--fallback-config-dir`.
3. Optimised always uses a complete second set in `FALLBACK/optimised/`, or
   `--optimised-config-dir`. It does not merge settings into the baseline.

The explicit fallback path avoids depending on an undeclared working directory.
Repository Dependabot YAML variants, Renovate JSON locations and embedded
`package.json#renovate` are recognized. Conflicting configs, invalid configs and
uncertified JS/JSON5 configs fail explicitly rather than silently using fallback.
Configs are normalized only in disposable snapshots. Original checkouts are unchanged.
Config origins, selected hashes, effective scan hashes and shared revision are recorded.
Existing `scan`/`full --config-dir` replacement behavior is unchanged.

The supplied `optimised/` configs are **editable starting policies**, based on the
existing Gappy defaults, with explicit schedule/limit settings. They are not a claim
of universally better policies; review the Helm directory and organisation-specific
preset before applying them to another repository.

### Forecast confidence and limits

These are **read-only predictions, not actual raised PRs**. Gappy captures paginated
GitHub PR state once with an explicitly selected token and reuses it across independent
scenarios. It never writes PRs, branches or commits remotely. Selected token values
and GitHub PR bodies are not saved. Redacted native scanner logs are saved separately
under `logs/` (see [Raw scanner logs](#raw-scanner-logs)).
Missing access/capture failures produce unknown totals,
not an empty successful PR list.

The model considers currently observed native **version-update** proposal groups,
explicit Dependabot daily/weekly/monthly/quarterly/semiannual/yearly schedules, timezones,
open-PR capacity and Renovate hourly/concurrent limits. Daily Dependabot schedules
use weekdays. Unchanged proposals are not counted again at every invocation.
Predicted new PRs remain open in subsequent invocation accounting; there are no
invented merges, closures, CI outcomes, security updates or future package releases.
`potentialPRs` is known native proposal demand, **not all possible future updates**.
Per-ecosystem group memberships may overlap and must not be summed as total PRs.

Creation/queue totals are unknown for incomplete scanner evidence, ambiguous existing
PR matching, uncertified closed-PR history, unresolved presets, future cooldown/age
transitions, Renovate natural-language/cron schedules and unsupported branch/CI policy.
Current-time local lookups do not establish which hidden candidates become eligible
later. Security-PR forecasting and custom bot identities are not certified.
Native runs observe registries at their own collection times; mutable presets and
registry responses are not a transactionally frozen upstream universe.
Known proposal counts remain visible even where creation predictions are unknown.
Unrecognised top-level Renovate PR policies also make creation/queue totals unknown;
Gappy never assumes an unmodelled approval or maintenance rule permits PR creation.
GitHub PR matching recognizes `dependabot[bot]` and `renovate[bot]`; it never guesses
matches from titles. An exact native Renovate branch can match an existing open PR.
That proven existing match remains visible even if future schedule policy is unknown.
Dependabot proposals without branch identities cannot be matched safely when PRs exist.

Outputs include four native scan exports plus `forecast-input.json`, `forecast.json`
and `forecast.html`. The input file contains only allowlisted normalized proposal,
policy and GitHub-state evidence needed for deterministic offline replay:

```bash
mkdir replay-output
gappy forecast-replay --input /path/to/forecast-input.json --output-dir replay-output
```

Replay needs neither Docker, network nor credentials. Outputs never overwrite existing
files. Non-success scanner/state collection produces diagnostic reports and exit status 1;
unsupported prediction policies are explicitly shown as unknown, even when scans complete.
Replay rejects duplicate policy scopes and PR identities. Same-scanner deltas are unknown
if toolchains, cache modes or recorded registry transport overrides differ between the
baseline and optimised executions; a configuration change alone must not receive credit
for an incomparable run.

Python/Click CLI comparing the dependencies **discovered** and updates **recommended**
by Dependabot and Renovate using Docker and Gappy's root-level scanner configurations.

## Run

```sh
uv sync --locked --extra dev
uv run gappy full --repository ministryofjustice/hmpps-developer-portal
```

Requires Python 3.12+, Git, Docker with Buildx, and Dependabot CLI 1.94.0 on PATH.
Without `--source`, Gappy clones a temporary GitHub checkout and selects `HEAD`.
Use an existing checkout and tracked revision with:

```sh
uv run gappy full --repository owner/name --source /path/to/checkout --ref COMMIT
```

For the baseline, Gappy freezes the target's tracked revision, then copies `dependabot.yml` and
`renovate.json` from the current directory (run from Gappy's root) into that disposable
snapshot as `.github/dependabot.yml` and `renovate.json`. Both scanners receive identical
effective snapshots and configuration hashes. Edits to those supplied configs take effect
immediately; target-repository untracked files and uncommitted changes are not scanned.
The original source and supplied configuration files are never modified.

`full` also scans **both tools with optimised configurations** and puts all four results
in one HTML comparison: Dependabot baseline/optimised and Renovate baseline/optimised.
By default the second set is `optimised/dependabot.yml` and `optimised/renovate.json`
under `--config-dir` (or the current directory). Override it with:

```sh
uv run gappy full --repository ministryofjustice/hmpps-developer-portal \
  --config-dir ./ --optimised-config-dir ./optimised \
  --timeout-seconds 900 --github-token-env GAPPY_GITHUB_TOKEN
```

Both optimised files are required when that variant is scanned; missing or malformed
configs fail before scanning.
`--repository-config` changes only the baseline source; it does not change the optimised
directory. The optimised scans use the baseline's exact Git revision and CA settings.
Alternate and embedded repository bot configs are removed only from the optimised
disposable snapshot, so they cannot override the selected second config set.
Baseline and optimised scans run concurrently in independent Docker containers and
disposable snapshots pinned to one Git revision resolved before either starts. Each
variant retains its own native caches; within a variant, Dependabot runs before
Renovate and its configured ecosystem worker limit still applies. Docker resource
usage and registry request rates can roughly double. Ctrl+C cancels both variants
and retains exports already written. Differences may also reflect registry timing, caches or incomplete
lookups. Partial/failed scans remain visible and retain a nonzero exit status. Cancellation
stops further scans and preserves exports already written. This is a dependency comparison,
not the time-window PR forecast provided by `forecast`.

To rerun only one configuration variant while reusing the other variant's saved results:

```sh
uv run gappy full --repository owner/name --optimised
uv run gappy full --repository owner/name --baseline
```

The selected variant always runs both scanners. The other pair is taken from the latest
run under `gappy-results/owner/name/` only if both exports are complete, mutually consistent,
and match the repository and currently requested Git revision (`--ref`, default `HEAD`).
If no previous run exists, exports are missing/incomplete, or the revision differs, Gappy
logs why and scans both variants concurrently instead. Invalid or inconsistent exports
fail explicitly; older runs are not silently substituted. To reuse an explicitly selected
run (including a custom output directory), add `--reuse-from /path/to/previous/run`.
`--baseline` and `--optimised` are mutually exclusive and unavailable with `--inventory`.
Omitting both flags retains the normal concurrent full scan.

Each invocation writes all four exports and the comparison to a new run directory (or
a fresh `--output-dir`), without overwriting prior evidence. Reused exports retain
their original timestamps and configuration hashes and include a provenance warning;
the current other-variant config is not reapplied, so reuse can include older policy
or registry results. Only variants being scanned require configuration files.

The bundled `optimised/` configs cover root npm dependencies, GitHub Actions,
Dockerfiles, Compose files and the `helm_deploy/hmpps-developer-portal` chart.
Renovate also covers Helm values images and `.nvmrc`; Dependabot has no native
`.nvmrc` updater. Renovate uses built-in presets rather than experimental remote
presets, with explicit managers and no automerge or image replacement rules.
Both configs keep a seven-day cooldown/release age (except npm `typescript` and
`govuk-frontend`) and exclude `@types/node` major updates. Other major updates
remain eligible. Coverage does not guarantee a recommendation: private registry
access, templated image names and cooldown-filtered evidence can remain unknown.
Adjust the explicit Helm directory when scanning a different repository.

Optimised update entries and Renovate package rules carry stable IDs as native
`config-id:<id>` labels. Dependabot entries use `labels`; Renovate rules use
`addLabels` plus a descriptive `description`. These are valid bot configuration
fields, not custom keys that hosted scanners would reject. Dependabot group and
entry labels are retained as dependency provenance; PR label application and
cross-ecosystem PR creation are still not simulated.

New scans record `observations[].configuration` with the configuration ID, labels,
and matching basis. Dependency reports show these as secondary badges:
`dependabot-entry` identifies the actual job entry; `renovate-resolved` identifies
labels present in native resolved dependency evidence; `configured-scope` identifies
only an exact local manager/datasource/package scope, **not proof that every rule
condition or action applied**. Complex filename, version, or update-type selectors
are not guessed. Old exports remain readable but cannot reconstruct missing labels.

### GHCR hmpps-node coverage and migration

The optimised Renovate config explicitly enables Docker lookups and digest pinning
for `ghcr.io/ministryofjustice/hmpps-node` across enabled Docker managers, including
Dockerfiles, Compose, workflow images and Helm values. A replacement rule proposes
moving Docker Hub `hmpps-node` and `ministryofjustice/hmpps-node` references (including
explicit Docker Hub prefixes) to GHCR using Renovate's
native replacement handling. Tags are preserved; no `latest` substitution or Node
downgrade is configured. The scanner never edits the original repository or creates
PRs. Native hosted Renovate performs the actual migration; Dependabot continues
updating existing references but does not implement image-name replacements.

Public GHCR images work anonymously. Private GHCR authentication is explicit and
separate from `--github-token-env`, using a GitHub account and an environment variable
containing a token with package-read access (and organisation SSO authorisation where
required). Do not put tokens in configuration files or command arguments:

```sh
uv run gappy full --repository owner/name --optimised \
  --ghcr-username YOUR_GITHUB_ACCOUNT --ghcr-token-env GHCR_READ_TOKEN
```

These options also work with `scan` and `forecast`. Both fields are required together;
missing/invalid selected credentials fail explicitly. Renovate receives an environment-only
Docker host rule scoped to `https://ghcr.io`. Dependabot receives a `ghcr.io` Docker
credential only for Docker/Compose/Helm jobs, via a mode-0600 temporary job file outside
the repository snapshot; the native CLI sends credential values to its proxy, not the
updater. Temporary job files are removed after the batch. Neither token is written to
scan exports, reports, source configs or snapshot hashes. Existing Docker Hub mirror
credentials and GitHub source/API authentication remain separate.

Investigation of the saved hmpps-node finding found a **missing destination tag**, not
a proven GHCR authentication failure: anonymous tag listing returned HTTP 200,
`24-alpine` and `latest` manifests returned HTTP 200, but `:26` returned HTTP 404.
Credentials do not create an unpublished tag. Preserve Node-version compatibility:
publish the required destination tag or deliberately choose a supported image in the
target repository before migrating. A missing tag or filtered absence remains
inconclusive rather than being reported as up to date. Native replacement proposals
retain `targetPackage` in JSON and show the destination image with its tag under
Recommended. Docker Hub source lookup must also succeed for Renovate to propose
a replacement; destination access alone is insufficient.
The replacement rule explicitly uses Docker versioning without an inherited
`versionCompatibility` transform, preventing suffix duplication such as
`24-alpine-alpine` in the pinned native Renovate release.

GitHub Actions comparison rows use **repository and manifest** identity. Dependabot
includes sub-action/reusable-workflow paths in dependency names, while Renovate reports
the parent repository. These findings are aggregated to avoid false "Not discovered"
cells; installed versions, policy states and targets are combined conservatively.
A repository match does not assert identical individual `uses` entries. Original
names and paths remain in scan JSON. Workflow Docker images are classified as Docker,
not Actions, in newly collected scans.

The four-column table uses traffic-light colours with text labels:
- **Green — up to date:** a completed eligible lookup recommends no update, or its
  recommendations match the installed versions after release `v`-prefix normalization.
- **Blue — deferred:** a completed lookup explicitly postponed by policy, such as
  Renovate minimum release age or Dependabot cooldown, or a known target explicitly
  deferred while policy eligibility is still unknown. Known explicitly deferred
  targets remain blue even in partial or failed scans; an incomplete-scan warning remains visible and additional updates may
  be missing. Unrelated lookup failures and unknown/mixed policy remain yellow.
  Known target versions remain under Recommended
  (and in scan JSON), but are not yet actionable. Summary target counts include these
  deferred versions.
- **Yellow — no applicable policy or unknown:** no eligible policy, other policy-filtered
  result, an incomplete scan/lookup, missing discovery or missing current version.
- **Red — out of date:** a completed eligible lookup recommends a different version or digest.

Each scanner's status is evaluated independently of peer versions and registries; the
colours do not rank scanners. Existing conservative version normalization is preserved:
release `v` prefixes are equivalent for npm, Helm and GitHub Actions, not Docker tags.
Native recommendations also support digests, prereleases and partial versions without
trying to order them. Up to date means according to that scanner and configuration, not
necessarily the latest published release. Incomplete results cannot earn green or red.
These colours are not security ratings.

For Docker/Compose, a completed native check with explicit `No update needed`
evidence and a latest eligible tag matching the installed version can establish
up-to-date status even when cooldown/allow rules are configured. Missing or
ambiguous evidence remains unknown.

When a Dependabot entry has `cooldown` and its native updater log reports cooldown
filtering (`Filtered out N versions due to cooldown`, `All versions filtered by
cooldown`, or `Skipping tag/version … due to cooldown period`) with no proposal,
Gappy reruns the same native job once without `cooldown`. Ignores, allow rules,
groups and versioning are unchanged, and proposals from the comparison run are not
recorded as Dependabot proposals. A version proposed only by the comparison run is
recorded as `policy: deferred` and shown blue under Recommended, matching Renovate's
minimum-release-age handling. If the comparison run also proposes nothing (and no
`allow` rule applies), the row is up to date. If the comparison run is incomplete,
the row stays unknown. The comparison doubles the runtime of affected jobs. npm's
own `.npmrc` `min-release-age` is not Dependabot cooldown, is not logged and is
not detected.
If the native updater reports `cooldown_date_unavailable`, Gappy surfaces a warning:
that registry did not provide a usable publication date, so the configured cooldown
was not enforced. Gappy does not invent release dates or silently remove cooldown.

In the disposable snapshot, Gappy adds `"private": true` to root and nested
`package.json` files that have no `private` property. Explicit values (including
`false`) are preserved. This avoids Dependabot's published-library lookup for
those packages and makes it treat them as applications, which can affect its
version-range update strategy. Both scanners use the modified snapshot; its
content hash includes these changes. Malformed manifests remain unchanged for
native scanner diagnostics.

Use `--config-dir /path/to/gappy` when invoking from elsewhere, or `--repository-config`
to explicitly use the target revision's own bot configurations instead. These options
cannot be combined. Missing supplied configs are an error, not a silent fallback.

Each normal `full` run writes six files under
`gappy-results/<owner>/<repository>/<UTC-timestamp>-<run-id>/`:

- `dependabot.scan.json`: native discovered dependencies and actionable proposals, including
  transitive evidence.
- `renovate.scan.json`: native discovered dependencies and post-policy recommendations,
  including any transitive evidence.
- `dependabot.optimised.scan.json` and `renovate.optimised.scan.json`: the second config set.
- `comparison.json`: four-variant direct dependency comparison, with native evidence in
  `variants[].scan` and paired baseline/optimised reports under `comparisons`.
- `comparison.html`: offline Bootstrap 5.3.8 report with four scanner/config columns.

`scan` still exports one scanner pair. `compare` automatically includes optimised exports
beside the selected baseline exports when present. For separately located files, supply
both `--optimised-dependabot-json PATH` and `--optimised-renovate-json PATH`. Existing
output files are never overwritten; use new `--json` and `--html` paths to regenerate a report.
The legacy `full --inventory PATH` mode retains its single-pair assessment and four output files.

**No Gappy-generated inventory or independent registry inventory lookup is used.**
The scanners themselves query registries. Findings include all native recommended versions
for direct dependencies, not just patches. Direct dependencies with no recommendation
remain in the report; transitive and unclassified observations are excluded from findings
and counts, not from the native scan exports.
Installed-version comparison ignores a single leading `v` on full release versions
(for example, `v8.0.2` and `8.0.2`) for npm, Helm and GitHub Actions. Original scanner
values remain visible in reports and exports. Different releases, SHA references and
Docker tags remain distinct; recommendation versions are not rewritten.
For digest-pinned GitHub Actions and Docker images, new scans use the native original
SHA/digest as the installed identity, even if a scanner also resolves a release version.
The resolved value is retained in the observation reason. Matching pins compare equally;
different pins remain mismatches. Older exports containing only a release on one side
and a pin on the other are `installed-reference-unresolved`, not a proven version
mismatch or agreement. Rescan to capture the original pin; comparison stays offline.
Dependabot update jobs default to direct dependencies, matching GitHub's normal
Dependabot configuration semantics. Explicit `allow` dependency types are preserved;
name-only allow rules also default to direct dependencies. Transitive packages can still
be resolved or changed as part of a direct dependency's lockfile update, but are not
independently checked for updates unless explicitly allowed.
Open the printed HTML path locally; there is no CDN, JavaScript or browser-side network
access. Bundled Bootstrap includes its MIT license.

HTML dependency tables default to **updates or scanner differences only**: a recommended
update from any scanner, one-sided direct discovery, or differing installed versions,
registries or recommendations. Unknown update availability alone is not a detected change.
Absence from a failed or incomplete scan and an unknown registry alone are not detected
dependency differences either; they remain visible in scan-status summaries and full results.
Uncheck **Only show updates or scanner differences** in the HTML to see every dependency,
or add `--verbose` to `full`, `compare`, `forecast` or `forecast-replay` to start with
the full results visible. Forecast tables compare discovery and installed versions across
all four variants and include any recommended update. Summary counts, JSON evidence,
warnings and scan statuses are never filtered. Legacy inventory reports default to
scanner-identified patch opportunities and also support `--verbose`.

Dependency tables also provide **Ecosystem / manager** and **Result** filters. They
combine with the updates-only toggle; choose **All** in either group to clear that
filter. In four-column comparisons, a result matches when any scanner/configuration
has that status. Scanner and inventory comparisons filter by their row outcomes;
forecast results distinguish whether a recommendation was observed. Summary counts
remain unfiltered; ecosystem section counts reflect the updates-only toggle but not
the additional selections. These controls use CSS `:has()` and require a modern browser
(Chrome/Edge 105+, Safari 15.4+, Firefox 121+); the updates-only toggle continues to
work in older browsers. Reports remain offline and script-free. Regenerate existing
HTML reports to add these controls.

`--output-dir` selects an existing directory. All destinations are preflighted,
must be distinct from inputs, and cannot overwrite existing files.
`--timeout-seconds` defaults to 180 and applies per native job (1–3600).
Dependabot jobs include lockfile resolution and update metadata generation, not
just registry lookups. Large npm jobs may require a longer explicit limit such as
`--timeout-seconds 900`, even with a warm mirror and direct-only update selection.
Use `--github-token-env NAME` for authenticated GitHub metadata requests when
needed; raising the timeout does not fix authentication failures or rate limits.
Timed-out jobs remain partial rather than being reported as successful.
`--registry-ca` supplies an explicit trusted CA; TLS verification is never disabled.

### Overall optimisation score

Four-column configuration reports include a relative score out of 100 and tied
rankings for each scanner/configuration. JSON exports contain the same breakdown
under `scoring` (`observed-evidence-v1`):

- **40 points: discovery** — fraction of the union of observed direct dependency
  rows discovered by this variant.
- **40 points: resolved outcomes** — fraction with a known installed version and
  either a completed eligible lookup or explicit deferral with a completed lookup
  or a known recommendation. The scan must be complete. Excluded/unknown rows
  receive no resolution credit.
- **20 points: recommendation visibility** — fraction of rows with recommendations
  anywhere that also have a recommendation in this variant, including deferrals.

If no recommendations exist, discovery and resolution each weigh 50 points.
Scores are rounded to two decimals; ties share a rank. Any incomplete scan makes
the ranking provisional and prevents declaring a winner. Empty evidence has no
score. Green rows do not score higher than correctly reported actionable/deferred
updates, and simply excluding dependencies does not improve coverage.

This is an evidence-quality heuristic, not a security, freshness, execution-speed
or PR-noise score, or proof of the objectively best policy. Recommendation
visibility does not compare target versions or establish proven misses; policies,
manager support and registry contexts/timing can differ. The shared denominator
is scanner-observed evidence, not an independent inventory, and cannot detect
dependencies missed by all four scans. Summary scores are unaffected by table
filters. Regenerate reports offline from saved scans to add scores.

Dependabot `commit-message` options (`prefix`, `prefix-development`, and
`include: scope`) are translated to native `commit-message-options`; presentation
settings do not prevent dependency discovery. Unsupported entry settings are
named explicitly in diagnostics.

The default Docker scanner mirror is `https://host.docker.internal:8444`, the
TLS gateway for Docker Hub lookups. Port 8085 serves plain HTTP for explicit host
pulls; using `https://host.docker.internal:8085` causes TLS protocol errors.
If you explicitly pass `--docker-mirror`, ensure it selects the correct TLS endpoint.

### Raw scanner logs

`scan`, `full` and `forecast` automatically save native scanner stdout and stderr
(combined) under `logs/` in the results directory. With explicit scan JSON paths,
logs are saved beside the Dependabot scan JSON. The CLI prints the log location.
Output is written and flushed as it arrives, before evidence parsing, filtering or
retained-output limits, so diagnostic lines remain available when a scanner fails
or times out. These are native process logs, not the normalized scan JSON.

`full` and `forecast` separate logs into `logs/baseline/` and `logs/optimised/`.
Each directory contains `renovate.log` and one log per Dependabot job, such as
`dependabot-01-npm.log`. Cooldown comparisons have their own
`dependabot-01-npm-without-cooldown.log`; retries are marked in the job log.
Selective `full` reruns copy the reused variant's existing logs when available.

Selected GitHub and GHCR credentials (including their supported encoded forms)
are redacted before writing, and new log files are owner-readable/writable only.
Logs may still contain private repository details or other sensitive scanner
output: review them before sharing. Raw logs have no size limit, so ensure enough
disk space. Existing results cannot acquire historical logs; rerun the scanners.

### Corporate HTTPS inspection

Containers do not inherit the Mac/Windows trust store. Dependabot's Docker client can
report `private_source_timed_out` for a registry-client failure caused by missing TLS
trust, even when the host can reach the same registry. Repeating the lookup or adding a
GitHub token does not fix that.

Supply your organization's approved **public CA certificate** with `--source-ca PATH`.
Gappy validates the PEM bundle and combines it with the selected Nexus/registry CA in a
temporary file outside the source snapshot. Both scanners use the combined trust;
verification stays enabled, and no private keys or credentials are needed.
Alternatively, place the approved CA at `.gappy-mirror/source-ca.crt` under the config
directory (current directory by default) for automatic use. This optional local file is
ignored by version control and is separate from the gateway CA, so re-exporting the
gateway certificate does not remove corporate trust. Do not copy a remote server's
unverified certificate and trust it as a root.

On a managed MoJ Mac with the root already installed by the organization:

```sh
mkdir -p .gappy-mirror
security find-certificate -c 'MoJ Production RootCA' -p \
  /Library/Keychains/System.keychain > .gappy-mirror/source-ca.crt
```

Only public certificates are exported; no keychain tokens or private keys are read.
With a different organization, obtain the approved CA from its administrator instead.
Additional source trust is recorded in scan warnings, and temporary bundles are cleaned up.

Add `--open-chrome` to `full` or `compare` to open the saved HTML report in Google
Chrome, including diagnostic reports from partial scans. The default does not launch a
browser. Chrome must be installed (on Linux/Windows its executable must be on PATH).
Launch failures are explicit errors; generated reports remain saved.

## GitHub authentication

Authentication is **opt-in** on `scan` and `full` with `--github-token-env NAME`.
The argument is an environment variable **name**, never the token itself. For example,
enter a token without echoing it or putting its value in shell history:

```sh
read -s GAPPY_GITHUB_TOKEN
export GAPPY_GITHUB_TOKEN
uv run gappy full --repository ministryofjustice/hmpps-developer-portal \
  --github-token-env GAPPY_GITHUB_TOKEN --open-chrome
unset GAPPY_GITHUB_TOKEN
```

Add your usual mirror and CA flags to that command if required. Alternatively, if
you already provide `GITHUB_TOKEN` securely, use `--github-token-env GITHUB_TOKEN`.
Gappy never automatically reads tokens from GitHub CLI, a keychain or other environment
variables. A missing, empty or malformed selected variable is an error before scanning.
Do not paste tokens into chat, configuration files, reports or command arguments.

Use a least-privilege **read-only** token. Public lookups need access to public GitHub
metadata; private dependencies/presets need read access to the relevant repositories
(for example, Contents: read and Metadata: read for a fine-grained PAT), plus organization
approval/SSO authorization when required. Dependabot CLI rejects some classic PATs with
write-capable scopes; do not broaden permissions simply to bypass an authentication error.
GitHub Packages access is separate and is not configured by this option.

The selected token is passed only to the Dependabot launcher as
`LOCAL_GITHUB_ACCESS_TOKEN` (the pinned CLI sends GitHub credentials to its proxy, not the
updater) and to Renovate as `RENOVATE_GITHUB_COM_TOKEN`. It is not written into generated
job YAML, source snapshots, command-line arguments, cache configuration or scan exports.
Reports disclose only that authentication was enabled. Raw scanner logs remain in memory,
and the selected token/common encoded forms are redacted before parsing retained output.
Trusted local administrators and Docker access can inspect process/container environments;
use short-lived tokens and trusted inputs. Authentication does not expand scan coverage
silently, disable TLS verification or permit external code execution.

This option covers **github.com Git/API sources**, including Renovate's GitHub-backed
presets. It does not configure npm.pkg.github.com, Docker Hub credentials, GitHub Enterprise,
Nexus administration or arbitrary private registries. A source timeout may still need
registry connectivity investigation; a token is not proof that all partial results are fixed.

When a Dependabot job reports only `private_source_timed_out` errors, Gappy pauses
**5 seconds**, retries, then pauses **10 seconds** before one final retry (three
attempts maximum). The console logs the timeout, pause, retry and final outcome to
stderr as they happen, without printing source URLs or raw errors. The same history
is retained in report warnings. Other ecosystem workers may continue during the wait.
Ctrl+C cancels the pause and scan; it never waits indefinitely for input.

Each retry starts from the original frozen source and retains the selected authentication,
mirror and cache settings. Only the final attempt supplies dependency/update evidence.
A recovered lookup can be complete; exhausted retries remain partial. Authentication,
policy and mixed failures are not retried. The per-job execution timeout still applies
to **each attempt**, so retries can increase total scan duration. A subprocess execution
timeout is not automatically retried as if it were a registry-source timeout.

Dependabot's launcher can also reach its own job timeout without emitting a structured
error. Gappy recognizes that timeout separately and reports it without exposing raw logs.
When the launcher returns its output, discovery emitted before a missing completion
event is retained, but its lookups are marked incomplete; a nonzero scanner exit cannot
turn those lookups into completed results. Gappy's process-timeout message includes the
configured number of seconds.

Timeout diagnostics are also printed to stderr with the scanner and the repository
directory being scanned. Dependabot includes the ecosystem and job number; `/` means
the repository root, not the machine's root directory. Renovate `ETIMEDOUT` errors
are printed as they are captured, once per manifest or unknown scope, and include a
safe scanner-reported manifest path when available; otherwise Renovate timeouts
identify the repository scan root. A directory identifies the affected scan scope,
not necessarily the exact dependency or manifest: whole-job timeouts may not
identify one, and transient source timeouts can recover.
Raw source URLs and scanner logs remain withheld, and the same diagnostics are retained
in the scan warnings. Scanner subprocesses do not inherit stdin, so running Gappy with
piped input cannot accidentally provide an extra Dependabot job or leak that input.

Identical source snapshots do not guarantee identical results: registry availability,
authentication, rate limits, native caches and changing upstream metadata also matter.
The Nexus container health check only confirms that Nexus is running; a proxy can still
be auto-blocked after an upstream failure. Check its upstream connectivity, approved CA
trust and locally retained Nexus logs before changing timeouts. Logs can contain sensitive
registry or package details and should not be shared. For a slow but otherwise healthy
system, try `--timeout-seconds 600 --dependabot-workers 1` on `full`, retaining any
authentication and mirror options you already use. This is not a fix for missing
credentials or an unavailable registry.

For an explicit authenticated GitHub Actions smoke test, securely set
`GAPPY_TEST_GITHUB_TOKEN` and run
`uv run pytest -o addopts= -m integration tests/test_auth_integration.py -q`.
It skips when that variable is unset; it never obtains a token from GitHub CLI automatically.

## Scan performance

Dependabot runs up to **two ecosystems concurrently** by default. Each ecosystem has
an isolated temporary source copy, and its directories/jobs remain sequential so
restore/save operations never overlap the same cache namespace within a run.
Results are merged in configured ecosystem order, not thread completion order.
Renovate still runs after Dependabot; cancellation stops running updater processes,
waits for cleanup and prevents queued jobs from starting.

Tune concurrency on `scan` or `full` with `--dependabot-workers 1` (sequential)
through `--dependabot-workers 16` per variant. More workers use more Docker CPU and memory and
are not necessarily faster. Per-job elapsed times, including image/cache preparation,
are recorded in scan warnings and the report's configuration/limits section.

A controlled two-ecosystem npm + Helm Docker benchmark measured 38.20 seconds with
one worker versus 21.36 seconds with two workers, with identical observations.
This is not a performance guarantee for production repositories or cold image downloads.
Run the reproducible check with:

```sh
uv run pytest -o addopts= -m integration \
  'tests/test_scan_integration.py::test_real_docker_collection[parallel]' -q -s
```

Keep caches and the default Nexus mirrors enabled to reduce repeat downloads.
Reuse an existing local checkout with `--source /path/to/checkout` to avoid cloning on
every scan; update that checkout yourself when newer source is needed. Fix missing source
credentials rather than increasing timeouts on repeatedly failing jobs.

The tested Dependabot npm image is AMD64 and uses emulation on ARM64 Docker hosts.
The official npm `latest` manifest checked during this change was also AMD64-only;
no native ARM64 image was verified, so Gappy retains its tested digest rather than
guessing an image or silently upgrading the scanner.

## Persistent download caches

`scan` and `full` reuse Docker named cache volumes **by default**. No extra argument
is needed. For isolated fresh-cache runs:

```sh
uv run gappy full --repository owner/name --no-cache
```

All repositories and both scanners use the common Docker volume `gappy-download-cache-v2`.
Internal namespaces separate scanners, ecosystems and image digests to avoid incompatible
formats and ownership conflicts. `full` and `forecast` additionally isolate all
native cache namespaces by repository and baseline/optimised variant, including
Dependabot updater downloads, proxy HTTP caches and Renovate caches. Each variant
reuses its own cache across runs, even if the two configurations are identical;
neither variant writes to the other's namespace. Existing unscoped caches are left
untouched, so the first variant-scoped run starts cold. Standalone `scan` retains
its existing cache namespace behavior.
Renovate and Dependabot do not interchange their native cache formats.
Old `gappy-cache-v1-*` volumes are left untouched and are not automatically migrated.
Package-manager download paths are configured for npm/Yarn, pip/uv, Go, Gradle,
Cargo, Composer, NuGet, Helm, Maven and Bundler where their native tools honor them.
Preseeded Dependabot toolchain caches are left intact.

Renovate mounts its volume directly. Dependabot CLI 1.94.0 supports only bind-path
mounts, so Gappy restores a private temporary staging directory from its named volume,
binds that directory into the updater, then saves it back through Docker's copy API.
Staging and transfer containers are cleaned up; persistent volumes are not deleted.
Cache initialization or synchronization failures are explicit scan errors.

Dependabot's proxy also persists its HTTP cache in `gappy-download-cache-v2`.
The pinned CLI's `--cache` option bind-mounts a separately staged namespace at
`/cache` in the proxy; this is not an updater `--volume` mount. Proxy namespaces
are isolated by repository, ecosystem and proxy/updater image identities, and are
never mounted into updater containers. `--no-cache` omits this persistent mount.
Proxy caching follows native eligibility/expiry rules and can retain private
dependency response data: keep the volume local and do not share it between users.

The scanners decide what to cache and when to expire it. This is **not** an offline
mirror or a guarantee that every dependency's download/registry request is cached.
Some dependencies require no archive download during lookup; non-cacheable responses
can still be fetched on every run. Do not run overlapping cached scans using the same Dependabot
toolchain and cache scope: staging is not a concurrent merge protocol. Separate
variant namespaces remove baseline/optimised cache write collisions; `full`
runs the two variants concurrently. The Nexus registry mirror remains shared and
is not isolated by these native cache namespaces.

Exports and reports record `cacheMode: "persistent"` and the exact `cacheVolumes`;
`--no-cache` records `"fresh"` with no persistent volumes. Cached metadata can have
different freshness from clean scans, so reports warn accordingly. Volumes contain
downloaded data and may grow; keep them local and do not use secret-bearing inputs.
Inspect with `docker volume ls --filter label=io.gappy.cache=true`; remove only an
exact, unused cache volume with `docker volume rm VOLUME_NAME`. No automatic pruning
or broad Docker cleanup is performed.

### Shared registry proxy with Nexus

For caching public package metadata and downloads across scanners, Gappy defaults to Nexus
proxies, not just their incompatible native download caches. This setup targets Docker
Desktop on macOS/Windows (`host.docker.internal`). Provisioned proxies are:

| Repository | Upstream | Scanner URL |
|---|---|---|
| `npmjs` | npmjs.org | `https://host.docker.internal:8443/repository/npm-all/` |
| `pypi` | pypi.org | `https://host.docker.internal:8443/repository/pypi/simple/` |
| `dockerhub` | Docker Hub | `https://host.docker.internal:8444` |
| `helm` | HMPPS Helm charts | `https://host.docker.internal:8443/repository/helm-group/` |

Helm HTTP repositories have separate upstreams, not one universal registry. The initializer's
`--helm-upstream URL` selects the `helm` proxy upstream (default:
`https://ministryofjustice.github.io/hmpps-helm-charts`). It refuses to overwrite an existing
proxy with a different upstream. Create additional Helm proxies in Nexus for other sources,
and supply a separate `--helm-mirror UPSTREAM=PROXY` mapping for each.

`scan` and `full` enable these four routes by default. Per-ecosystem mirror options
override their respective defaults; a supplied Helm mapping list replaces the default
HMPPS mapping. The gateway CA defaults to `.gappy-mirror/ca.crt` under `--config-dir`
(the current directory when omitted); `--registry-ca` overrides it. Provision the services
and export the CA before scanning. Gappy does not auto-create infrastructure, reset Nexus,
disable TLS checks or silently fall back to upstream if a mirror fails.

Use `--no-mirror` for direct native upstream routing. Explicit mirror options still apply
with `--no-mirror`, so individual ecosystems can be mirrored independently. `--no-cache`
does not disable mirrors; use both flags for a direct, fresh native-cache run.
GitHub Actions, nvm, private registries, unmapped Helm sources and other ecosystems do
not have a bundled Nexus proxy and retain native routing/download caches. This is not a
universal mirror of every ecosystem or of the GitHub API.

Start the services (allow several minutes and roughly 3 GiB of available Docker memory).
From the Gappy directory, define this helper in each shell. It automatically includes the
Nexus trust overlay whenever an approved source CA file is present:

```sh
mkdir -p .gappy-mirror
mirror_compose() {
  if [ -e .gappy-mirror/source-ca.crt ] || [ -L .gappy-mirror/source-ca.crt ]; then
    docker compose -f compose.mirror.yml -f compose.mirror-trust.yml "$@"
  else
    docker compose -f compose.mirror.yml "$@"
  fi
}
mirror_compose up -d --wait --wait-timeout 600
# First initialization only: bootstrap password stays in the pipe, not in command history.
mirror_compose exec -T nexus cat /nexus-data/admin.password |
  uv run python -m gappy.scan.mirror --password-stdin
mirror_compose cp \
  gateway:/data/caddy/pki/authorities/local/root.crt .gappy-mirror/ca.crt
```

The helper selects the files on every invocation, so it also picks up a CA installed
after the initial setup. An empty, malformed or broken CA file fails explicitly rather
than falling back to the base configuration. Redefine the helper in a new shell, or
specify the appropriate Compose files manually.

The setup creates all four proxies, enables the Docker token realm while preserving
existing realms, and enables anonymous read access. Admin requests stay on loopback;
credentials are not persisted
by Gappy. The initializer can be rerun; after changing the Nexus admin password, invoke
`uv run python -m gappy.scan.mirror` to enter the current password interactively instead.
For an existing npm-only setup, rerun that initializer to add the extra repositories and
rerun `mirror_compose up -d --wait --wait-timeout 600` for the
additional Docker TLS endpoint. If the bootstrap password has been removed, use the
interactive initializer with the current admin password; do not reset Nexus or delete data.
Nexus administration is at `http://localhost:8081`. The TLS gateway is required for the
Dependabot proxy: simply pointing both scanners at Nexus's plaintext port is insufficient.

#### Nexus outbound TLS behind HTTPS inspection

Scanner `--source-ca` trust does not configure Nexus's separate JVM. If Nexus logs
`PKIX path building failed`, obtain your organization's approved public CA bundle
and save it as `.gappy-mirror/source-ca.crt`. Do not trust an unverified certificate
downloaded from the failing server or disable TLS verification.

Enable the optional persistent JVM trust configuration. If the `mirror_compose` helper
above is defined, it now selects both files automatically:

```sh
mirror_compose up -d --wait --wait-timeout 600
```

Alternatively, specify both files explicitly:

```sh
docker compose -f compose.mirror.yml -f compose.mirror-trust.yml \
  up -d --wait --wait-timeout 600
```

This restarts Nexus without deleting its data. A network-isolated initializer copies
the image's standard JVM trusted roots and adds the supplied, unexpired CA certificates
to a dedicated volume. Nexus mounts that store read-only. The `changeit` password is
the standard, non-secret password for this public-certificate-only store, not a Nexus
admin credential. Missing files, malformed bundles, private keys and non-CA certificates
fail initialization explicitly. Use the helper or both Compose files for subsequent
lifecycle commands. Running `up` with only `compose.mirror.yml` recreates Nexus without
the trust overlay, even if the CA and trust volume still exist. After rotating the CA or
upgrading Nexus, keep both image versions aligned and add `--force-recreate` to the
command above so the JVM restarts and reloads trust.

`SonatypeOutreach - Could not download page bundle` concerns optional Welcome-page
content, not a package lookup. Its TLS error alone does not establish that registry
proxying failed, although the same missing CA can affect both. Check the affected
proxy's status after fixing trust; do not reset Nexus or delete its cache.

```sh
uv run gappy full --repository ministryofjustice/hmpps-developer-portal --open-chrome   --github-token-env GAPPY_GITHUB_TOKEN   --dependabot-workers 12 --timeout-seconds 900 --optimised
```

`--npm-mirror` overrides the default and applies the same public-npm transport override to the disposable
snapshots used by both scanners. It does not edit the source checkout or update policy.
The effective snapshot hash and scan warnings record the override. Existing private/scoped
`.npmrc` registries and explicit Renovate `npmrc`/`registryUrls` are rejected rather than
silently redirected. Use this option only for public-npm repositories; inherited presets,
direct tarball/Git dependencies and lockfile URLs may require additional registry-specific
configuration. This is a pull-through proxy, not a pre-populated or guaranteed offline mirror.

Additional mirror options are independent and also available on `scan`:

- `--docker-mirror` replaces Docker Hub's base registry in Dependabot jobs and matches public
  Docker Hub image names in Renovate. The TLS gateway expands official image requests to
  `library/NAME` where necessary. Explicit registries such as GHCR/private registries are not
  universally redirected. This does **not** configure the host Docker daemon or mirror the
  pinned scanner images pulled from GHCR. Docker Hub rate limits still apply upstream.
- `--pypi-mirror` supplies an anonymous base Python index to pip jobs and overrides Renovate's
  PyPI datasource. Explicit Python index/source settings are rejected rather than silently
  redirected. Direct URL/Git dependencies and uv-specific index behavior are not universally
  mirrored. **Dependabot's current pip parser rejects all parsing when Gappy's
  `reject-external-code` protection is enabled**, even for plain requirements files. Such jobs
  remain partial with `unexpected_external_code`; Renovate can still use the PyPI proxy.
  Gappy does not weaken this safety setting to make a scan appear successful.
- `--helm-mirror UPSTREAM_URL=PROXY_URL` rewrites only matching dependency repository URLs
  in disposable Helm chart/lock files. Both scanners then query the selected proxy.
  Unmapped sources, OCI registries, aliases and embedded URLs are not automatically mirrored.

Effective snapshot hashes, mirror fingerprints and warnings record these overrides. Keep
using the gateway CA (auto-selected or `--registry-ca`); TLS verification is never disabled. Other ecosystems retain their native
caches and need separately configured Nexus repositories.

#### Direct Docker image pulls through Nexus

If Nexus has a `docker-group` repository (Docker Hub plus GHCR proxies) with HTTP
connector port 8085, the gateway exposes it for explicit host pulls:

```sh
docker pull localhost:8085/library/alpine:3.20
docker pull localhost:8085/dependabot/dependabot-updater-npm:latest
```

`localhost:8085` is loopback-only plain HTTP, which Docker permits for `127.0.0.0/8`
without an insecure-registry exception. Containers can use
`https://host.docker.internal:8084` after the Docker daemon trusts `.gappy-mirror/ca.crt`.
Pulled images keep the `localhost:8085/...` name; retag them if a tool expects the
original reference. This does not change Docker's default pull routing.

Package blobs and metadata persist in the named volume `gappy-nexus-data`; the local TLS CA
persists in the gateway's data volume. All published ports are bound to `127.0.0.1`; scanner containers
reach them through Docker Desktop. Do not expose this anonymous proxy to untrusted networks.
`mirror_compose down` stops it without deleting cached data.
Do **not** add `--volumes` if you want to retain the cache and CA.
`--no-cache` bypasses Gappy's native caches, not an explicitly selected Nexus proxy.
Nexus caches metadata for 60 minutes and missing artifacts for 5 minutes, so results can
differ in freshness from direct registry scans.

To test both real Docker scanners against a running proxy:

```sh
GAPPY_TEST_NEXUS_URL=https://host.docker.internal:8443/repository/npm-all/ \
GAPPY_TEST_NEXUS_CA="$PWD/.gappy-mirror/ca.crt" \
  uv run pytest -o addopts= -m integration tests/test_mirror_integration.py -q
```

For the additional Nexus scenarios, also set `GAPPY_TEST_NEXUS_DOCKER_URL`,
`GAPPY_TEST_NEXUS_PYPI_URL` and `GAPPY_TEST_NEXUS_HELM_URL` to their URLs in the table.
Unset scenarios skip explicitly. The pip scenario verifies Renovate lookup and Dependabot's
external-code safety diagnostic, not a successful Dependabot pip update.

## What the report tells you

The comparison population is the union of **direct native scanner discoveries**, grouped
by ecosystem, canonical package name and manifest. Each side retains reported installed
versions, registry identities, recommendations, policy state and evidence references.
Transitive dependencies are not separate findings, even when a direct dependency has an
update: a newer parent version does not prove which transitive packages it changes.

| Result | Meaning |
|---|---|
| Dependabot/Renovate-only discovery | Only that scan supplied this dependency/manifest |
| Different recommendations | Both discovered it but recommend different target versions |
| Installed-version mismatch | Tools report different starting versions; do not rank targets |
| Registry mismatch | Tools report different known registries; do not rank targets |
| Same recommendations | Both supplied identical nonempty target sets |
| No recommendations | Both completed eligible lookups with no recommendations |
| Not assessable | No recommendations and insufficient complete policy/lookup evidence |

One-sided discovery is not automatically a failure: manager scope, configuration,
unresolved presets and scan failures can explain it. More recommendations do not prove
better performance. There is **no patch-coverage percentage or overall winner** in
scanner-comparison mode. Dependencies or updates missed by both cannot be measured.
Reports make failed, partial and unsupported scans explicit.
Dependabot marks observations with native requirements as direct and requirements-free
observations as transitive. Renovate package-file declarations are treated as direct,
except for explicitly indirect/transitive entries; npm overrides and resolutions are
unclassified because they may target transitive packages. Observations missing enough
directness evidence are excluded from findings and counted separately as unclassified.
The raw scan JSON retains all these observations, and comparison JSON includes them under
`scans`; the HTML's evidence context contains the comparison, not the full raw scans.
This is based on scanner/manifest metadata, not a complete dependency graph: for example,
a generated or flattened requirements file may not distinguish indirect packages.
There is no 100-dependency cap.

New scan exports use schema `1.1.0` and include an observation's `directness`.
Older `1.0.0` exports remain readable but did not retain this evidence, so their
unclassified observations cannot be used in direct-only findings. The report warns
about exclusions; run a new scan to obtain directness metadata. Existing saved
reports are not modified automatically.
Each tool's summary shows dependencies with recommended updates and the number of distinct
recommended target versions per dependency/manifest context, with an ecosystem breakdown.
One dependency can have several target versions; these totals are not PR counts.
Incomplete scans explicitly warn that additional recommendations may be missing.
The same counts are exported in `metrics.providers` and `metrics.byEcosystem`.
In the HTML, **Only show updates or scanner differences** is on by default.
Uncheck it or generate with `--verbose` to show all rows. It works offline without JavaScript
and does not change summary counts or JSON exports. Unknown availability alone does not
keep a row visible, so an incomplete scan may hide updates. In legacy inventory reports
the filter shows opportunities identified by either scanner and hides opportunities missed
by both. Regenerate existing reports to update the toggle.
Authentication failures remain partial or failed: GitHub tokens are forwarded only with explicit
`--github-token-env NAME`; other private-source credentials are not supplied automatically.
A missing GitHub token in Renovate and a Dependabot `private_source_authentication_failure`
are reported explicitly. Changing configs or increasing timeouts does not supply credentials. Dependabot
discovery already received before an update job error is retained, with incomplete lookups.
Renovate warnings distinguish a missing token, a rejected token (HTTP 401 Unauthorized),
and configuration/preset resolution failure without exporting raw native errors.
If both authentication and configuration warnings appear, check the selected token first:
an inaccessible GitHub preset can prevent scanning before any dependencies are discovered.
An empty failed scan is not evidence that the tool found no dependency updates.
Gappy enables the pinned Dependabot npm parser's native `dealias-packages` option.
This resolves npm installation aliases to real registry package names, including
transitive lockfile aliases. Without it, a public mirror's 404 for an alias can be
misreported by Dependabot as `private_source_authentication_failure`. The override
is recorded in scan warnings; Gappy does not rewrite dependencies or invent discoveries.

When a scanner completes an eligible lookup without a recommendation, its dependency
cell shows a green **No newer update recommended** label. This can mean the package is
already up to date or no newer eligible version is available under the scanned configuration;
it does not certify the latest published release. Policy exclusions, deferrals and
unknown/incomplete lookups have separate labels and are never presented as up to date.

## Ecosystems and local policy

Dependabot runs a separate native job for each configured ecosystem/directory.
Mappings include npm, GitHub Actions, Docker/Compose, Helm, pip, uv, Maven, Gradle,
NuGet, Go, Cargo, Composer, Bundler, Terraform and other official updater families.
Unknown ecosystems and unsupported options emit per-entry diagnostics; supported
entries still run instead of being blocked by unrelated entries.

The npm, Helm, GitHub Actions and Docker updaters and shared proxy use the digests in
[toolchain.json](src/gappy/scan/toolchain.json). Other ecosystem updater images are
resolved from official GHCR tags to immutable digests **before execution**, and their
exact image references are recorded in the export. Those other digests can change
between runs. Pinning an image does not certify every native format and rule.

Supported translation includes explicit `directory`/`directories`, groups,
ignore conditions, cooldown, allow rules and versioning strategy. Multi-ecosystem
groups support group schedules, labels/assignees/milestone metadata and group PR
limits, with a `multi-ecosystem-group` reference and required nonempty `patterns`
on each ecosystem entry. Put directories, cooldown and single-ecosystem `groups`
on each update entry, not on the multi-ecosystem group. For example:

```yaml
version: 2
multi-ecosystem-groups:
  infrastructure:
    schedule:
      interval: weekly
    labels: [infrastructure, dependencies]
updates:
  - package-ecosystem: github-actions
    directory: /
    patterns: ["*"]
    multi-ecosystem-group: infrastructure
    cooldown:
      default-days: 7
```

Group schedules take precedence over member schedules. Patterns scope native
allowed updates; discovered dependencies outside those patterns are marked excluded.
Explicit allow dependency types are preserved. Named allow rules combined with
patterns must use `*` on one side or identical patterns; other intersections produce
an explicit unsupported-entry diagnostic rather than broadening policy. Other
multi-ecosystem group options also remain unsupported with explicit diagnostics.
Jobs still run separately per ecosystem/directory: cross-ecosystem PR consolidation,
PR metadata application and shared capacity are not simulated. Forecasts inherit
group schedules/limits but explicitly mark shared PR behavior as unknown.

Jobs retain the selected configuration's policy (Gappy root configs by default,
target configs with `--repository-config`). Schedules are recorded in source but
are not replayed for an explicitly invoked lookup.
Glob directories, private registry credentials and unrecognized policy options remain
unsupported with explicit diagnostics.

Renovate 44.14.1 runs its native local lookup against `renovate.json`, resolving
presets and package rules itself. Every returned manager is extracted, including
non-npm and custom managers. Package rules scope workflow paths with `matchFileNames`,
not the manager-only `fileMatch`/`managerFilePatterns` extraction settings.
Actionable candidates must also appear in its
**post-policy branch evidence**: lookup candidates subsequently disabled by package
rules are not counted as recommendations. When all returned updates are explicitly
pending policy checks, their known target versions are retained as recommendations
with deferred policy, even though no actionable branch exists yet. Skipped updates
remain excluded. Disabled/ignored and pending-age evidence remain distinct; filtered
absence without a proven reason remains unknown.

Full npm controlled scenarios cover patch/no-update, grouping, ignores, cooldown,
cooldown exclusions and multiple directories; Helm has a real scan-to-report proof.
Other ecosystem certification remains
incremental; see the task list rather than assuming exact hosted-service parity.
Non-SemVer versions, tags and digests are retained as native values, not coerced into
npm patch arithmetic.

## Separate scan and offline comparison

```sh
uv run gappy scan --repository owner/name
uv run gappy compare --repository owner/name
```

`compare` selects the latest timestamped run; incomplete latest runs fail instead
of falling back silently. Explicit `--dependabot-json` and `--renovate-json` inputs,
and `--json`/`--html` destinations remain available. Comparison needs no Docker,
credentials or network.

For compatibility, explicitly supplying `--inventory PATH` to `compare` or `full`
selects the previous independent patch-assessment mode. This is optional legacy
behavior, not the default, and Gappy never generates that input. Legacy assessments
also exclude transitive and unclassified opportunities. If a direct opportunity was
missed by both scanners, an independent inventory can mark the opportunity with
`"directness": "direct"`; assert this only when supported by manifest evidence.
Older inventory entries without this field are assessed only when a scan establishes
the same direct dependency/manifest context. Excluded counts and warnings are reported.

## Safety and development

Scanners do not create remote PRs or push commits. Dependabot proposal events are
local observations. Scripts/plugins are disabled where supported, Dependabot rejects
external code, and containers do not receive the Docker socket or host home directory.
Docker is not a complete sandbox: use trusted repositories and no secret-bearing
configuration. Raw production logs are held in memory and are not persisted.
Renovate trace output is filtered while streaming: only dependency lookup results, post-policy
branches, repository completion, and warning/error events are retained. By default neither
scanner has a fixed evidence-output byte ceiling, for both `scan` and `full`; large outputs
can therefore use substantial memory, and an operating system may terminate the process
if memory is exhausted. For Renovate, `--renovate-output-limit-mib 64` sets an optional
1–256 MiB limit on retained evidence and individual log lines. The selected limit, or its
absence, is recorded in scan warnings. Exceeding an explicit limit fails rather than silently
truncating evidence; execution timeouts still apply. Parsed JSON and normalized observations
can use more memory than the raw byte budget.

There is **no 100-dependency or finding limit**. All native dependency observations
are processed for the scan exports; comparison filters by directness, identical identities
may be merged, and configured policy can exclude updates.

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src/gappy
uv run --no-sync pytest
uv run --no-sync pytest -m integration tests/test_scan_integration.py
uv build --python .venv/bin/python
```

Default tests are synthetic and require no live registries or credentials.
Opt-in Docker tests use synthetic repositories and a controlled HTTPS registry.
