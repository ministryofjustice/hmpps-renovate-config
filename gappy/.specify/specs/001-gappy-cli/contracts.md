# CLI and JSON Contracts

## Configuration-forecast extension (implemented)

`gappy forecast` collects baseline/optimised variants for each scanner. It requires
`--repository`, `--fallback-config-dir`, `--window-start` and `--window-end`.
`--optimised-config-dir` defaults to the fallback directory's `optimised/`.
Baseline config selection is repository-first and independent per scanner; this does
not alter legacy `scan`/`full` config replacement semantics.

New forecast input schema `1.0.0` is separate from scan schema `1.0.0`/`1.1.0`:
`repository`, `revision`, `sourceHash`, `window`, `platform`, `variants[4]`.
Each variant records provider/label, selected config origin/hash, legacy-compatible
`scan`, native grouped `proposals`, supported `policies`, proposal completeness and
limitations. Proposal identity is deterministic over provider, ecosystem, directory,
branch and sorted package/target/context members. Sensitive raw messages are absent.

`forecast.json` has mode `configuration-forecast`, four `results`, same-scanner `deltas`
and a direct-only four-way `dependencies` table. Counts separate `observedPotential`
from nullable complete `potentialPRs`, `predictedNew`, `queued` and `deferred`.
`existing`/`unknown` count proven matches/uncertain observed proposals; hidden future
demand is not enumerated. Per-ecosystem proposal memberships can overlap.
Each proposal has one terminal forecast state and reason; a created proposal records
its predicted timestamp. Unknown is JSON null for unproven totals, not zero.

Window bounds are timezone-aware and end-exclusive, begin after state/native capture,
and contain at most 10,000 invocations. Frozen-state simulations invent no merges,
CI outcomes, new releases or security proposals. Unsupported native clock/schedule,
resolved-preset and matching capabilities remain explicit unknowns.

`forecast-input.json` supports deterministic offline
`gappy forecast-replay --input PATH --output-dir DIRECTORY`. Seven outputs (input,
forecast JSON/HTML and four scans) use existing no-clobber publication.
Read failures never substitute empty complete platform state. See README for the
certified subset and editable optimised policy examples.

Forecast collection also accepts `--dependabot-workers`, `--renovate-output-limit-mib`
and `--open-chrome`. Cancelled evaluations stop remaining configurations and publish
diagnostic outputs with exit 130; skipped scans explicitly identify borrowed metadata.
Replay rejects duplicate policy scopes and PR numbers. Forecast JSON includes
`comparisonWarnings`; baseline/optimised deltas are null when toolchain, cache mode
or recorded `gappy:*` registry transport identities differ. Known native branch matches
remain countable independently of unsupported future policy. Unmodelled top-level
Renovate PR settings make creation/queue totals unknown, not permissive by default.

## Current scanner-comparison contract

Cache amendment: `scan`/`full` use isolated persistent download volumes by default;
`--no-cache` selects fresh caches. Scan `cacheMode` accepts `"fresh"` or
`"persistent"` and optional `cacheVolumes: string[]` records exact volume names.
Old exports without volume names remain valid. Native expiry applies; cached runs
must not be described as fresh or as an offline mirror. Dependabot staging/copy
errors are explicit non-success diagnostics. Persistent volumes survive cleanup.

`full --repository owner/name` and `compare --repository owner/name` require no
inventory and generate none. Output report `mode: "scanner-comparison"` contains
`dependencies[]` grouped by canonical ecosystem/package/manifest with each provider's
`discovered`, `installedVersions`, `registries`, `recommendations`, `policy`,
`lookupComplete`, `evidence` and `reason`. Row `result` distinguishes one-sided
discovery, installed-version mismatch, different/same recommendations, no
recommendations and not-assessable. `metrics` contains dependency and ecosystem
counts, per-provider discovery/recommendation counts and outcome counts, with no
independent coverage or winner. `scans`, snapshot/session identity, hashes and warnings
remain. Native version strings, including tags and digests, are retained.

The optional explicit `--inventory` selects the older patch-assessment contracts
below for compatibility only. Automatic generation described in older amendments
is superseded. Default full output consists of four files, not five.

**Status**: Design contract, not implemented flags or executable schemas. Phase 0 native fixtures may require additional evidence fields; retain the documented safety and comparison invariants.

**Implementation note (2026-11-11)**: The root npm baseline now implements `scan` and `compare`. The installed help and `src/gappy/contracts.py` describe its actual wire format: dependency-level `observations` combine extraction, policy, candidates, and lookup completeness; tool/image provenance and snapshot/session identity remain explicit. `--registry-ca` is available for controlled registry trust; `--credentials-file` and resolved-policy import are not implemented. Broader manager/policy support remains open. The design below remains the target contract rather than a claim that every optional surface is implemented.

## CLI

Full workflow amendment: `gappy full --repository owner/name [--inventory PATH]`
combines scan and compare using exact export paths from the current invocation.
It supports `--source`, `--ref` (default HEAD), `--timeout-seconds`,
`--registry-ca`, and an optional existing `--output-dir`. Without an override it
generates `inventory.json` from direct npm manifests/lockfiles and registry release
metadata before either scan, on the same frozen revision. Generated provenance
includes optional `warnings: string[]` and `complete: false`; reports retain these
warnings. Registry bodies support gzip and are bounded to 64 MiB decoded per package.
By default all five outputs share a new unique scan-run directory. All destinations and supplied inventory
are preflighted before collection. Non-success scans can generate diagnostic
reports while retaining exit code 1; cancellation retains 130 and stops before
comparison. Invalid snapshot/session identities cannot produce a report.

Scan defaults amendment: only `--repository` is required for `scan`. Omitted
`--source` acquires a temporary GitHub checkout using Git, with noninteractive
authentication, and omitted `--ref` selects `HEAD`. Each omitted provider output
defaults to `./gappy-results/<owner>/<repository>/<UTC-timestamp>-<run-id>/<provider>.scan.json`;
the unique directory is created automatically and both destinations are printed.
Explicit overrides remain supported, existing outputs are refused, and temporary
checkouts are removed on success/failure. Comparison defaults are described below.
Scanner capability limitations are unchanged.

Compare defaults amendment: `compare --repository owner/name --inventory PATH`
selects the latest timestamped scan directory under `./gappy-results/owner/name/`.
Missing exports in that latest run fail rather than falling back to an older run.
Both report paths default beside the inputs to `comparison.json` and
`comparison.html`, are printed, and never overwrite existing files. Explicit
export inputs remain supported without `--repository`; if input directories
differ, both report paths must be explicit. The repository option, when supplied,
must match the export identities. Inventory is always an explicit required input.

Implement a Click group with `scan` and `compare` commands, installed through the Python console entry point `gappy = "gappy.cli:main"`. Click parameter errors retain exit code `2`; execution and interruption errors must map to the explicit statuses below rather than Click's default interruption status. Python 3.12+ is the planned host baseline; no host Node.js runtime is required.

```text
gappy --help
gappy scan --help
gappy compare --help

gappy scan \
  --repository ministryofjustice/hmpps-developer-portal \
  --source /absolute/path/to/checkout \
  --ref <commit> \
  --dependabot-json /absolute/path/to/dependabot.scan.json \
  --renovate-json /absolute/path/to/renovate.scan.json \
  [--credentials-file /absolute/path/to/scan-credentials.json] \
  [--timeout-seconds <positive-integer>]

gappy compare \
  --dependabot-json /absolute/path/to/dependabot.scan.json \
  --renovate-json /absolute/path/to/renovate.scan.json \
  --inventory /absolute/path/to/inventory.json \
  --json /absolute/path/to/comparison.json \
  --html /absolute/path/to/comparison.html
```

Read both existing configuration files from the frozen source revision. Missing required configuration fails preflight. Use the adapter's tested pinned toolchain manifest; do not expose arbitrary image/command execution options initially. The default scan timeout is 180 seconds.

The optional credentials file is explicit scan-only input with restrictive access expectations. It supplies narrowly scoped registry/preset credentials, not scripts or arbitrary Docker options. No credentials as CLI values; do not persist their values or secret-bearing file paths into diagnostics/reports. Never forward the entire host environment.

`compare` performs no Docker preflight, process spawning, preset resolution, or registry queries. It accepts no PR history or historical date-window override.

### Exit status

- `0`: operation completed. A valid comparison can contain unknown/unsupported results; diagnostics and summary must make them visible.
- `1`: execution/I/O failure, failed/partial/cancelled provider scan, or unsupported scan integration. Preserve usable initialized scan exports with their actual non-success state.
- `2`: usage, invalid contract, incompatible inputs, or preflight validation failure.
- `130`: user interruption; preserve cancelled state and clean up owned resources where possible.

Never log raw subprocess stderr or raw parser input excerpts. Diagnostics include stable code, safe provider/scope reference, and actionable message.

## Inventory v1

Illustrative shape (hash placeholders are not valid fixture values):

```json
{
  "schemaVersion": "1.0.0",
  "repository": "ministryofjustice/hmpps-developer-portal",
  "snapshotHash": "<sha256>",
  "asOf": "2026-11-11T12:00:00Z",
  "provenance": { "source": "independent inventory", "complete": false },
  "opportunities": [
    {
      "dependency": {
        "ecosystem": "npm",
        "datasource": "npm",
        "registry": "https://registry.npmjs.org",
        "package": "example-package",
        "manifestPath": "package.json",
        "installedVersion": "1.2.0",
        "versioning": "semver"
      },
      "candidateVersion": "1.2.1",
      "availableAt": "2026-11-01T00:00:00Z"
    }
  ]
}
```

Registry identities must contain no userinfo, tokens, or secret query parameters. Relative manifest paths must remain inside the snapshot. Availability dates must be explicit timezone-qualified timestamps, normalized to UTC.

## ProviderScan v1

Required top-level fields:

| Field | Contract |
|---|---|
| `schemaVersion`, `provider` | Supported version and exactly one known provider |
| `sessionId`, `repository`, `snapshot` | Shared source identity; revision/content/config hashes |
| `toolchain` | Native/launcher/adapter versions and image repository+digest identities |
| `configuration` | Allowlisted safe policy summary, preset provenance, translation/safety limitations |
| `startedAt`, `finishedAt`, `cacheMode` | Actual observations and isolated fresh-cache declaration |
| `status` | Complete, partial, failed, cancelled, or unsupported |
| `dependencies` | Extracted contextual dependency records |
| `candidates` | Safe native observations with target version, policy and evidence |
| `coverage` | Scope-specific extraction/lookup completeness and negative-evidence support |
| `diagnostics` | Sanitized structured errors, warnings, unsupported capabilities |

Validate cross-field invariants, not only JSON shape: complete scan timestamps, correct reference targets, consistent dependency identity, immutable provenance, and non-success states with reasons. Comparison accepts structurally valid partial exports for diagnostic reports but cannot turn their absent evidence into non-detection.

Unknown schema major versions and unrecognized native formats fail explicitly. Schema changes require synchronized validators, types, fixtures, and adapter tests.

## ComparisonReport v1

Required top-level fields:

- `schemaVersion`, `repository`, `sessionId`, `snapshot`, safe input content hashes.
- `inventory`: provenance/as-of/completeness and normalized opportunities.
- `scans`: provider provenance, statuses, observation times, limitations.
- `assessments`: one record per opportunity with both provider states and result/winner.
- `metrics`: inventory counts, extraction coverage, policy partitions, jointly eligible total, assessable denominator, integer detection counts, nullable coverage, and paired subset winner.
- `evidence`: allowlisted normalized references used by assessments and recommendations.
- `configurationDifferences`, `recommendations`, `warnings`.

Evidence uses deterministic IDs and local report anchors. External links, when present, must use validated `https`/`http` destinations with no credentials; unsafe links render as escaped non-clickable text with a warning.

### HTML table

```text
Dependency/context | Installed | Available patch | Released
Dependabot extraction / policy / detection / evidence
Renovate extraction / policy / detection / evidence
Result | Detection winner
```

Use real table captions/headings, text states, and non-color-only indicators. Summary tables show numerator, denominator, percentage/unavailable, exclusions, and subset qualification. Embed CSS; no scripts, fonts, assets, or API calls from remote sources.

## File Handling

Resolve existing paths and parent directories before writing. Create parent directories only for automatically chosen scan destinations; explicit destinations require existing parents. Reject equal output paths and collisions with inputs, including symlink and hard-link aliases. Refuse existing outputs rather than overwriting silently. Preflight both destinations before acquisition/scanning.

Stage and validate output files in their destination directories, then publish with no-clobber semantics. A pair of files is not a filesystem-wide transaction: if publication partly fails, return nonzero and identify the successfully published path and failed path; do not announce complete success or delete unrelated files. Clean up only owned staging files.
