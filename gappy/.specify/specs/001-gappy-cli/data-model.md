# Data Model and Comparison Rules

## Default scanner-comparison amendment

No independent inventory is generated or required. Join the union of native
discoveries by canonical ecosystem/package/manifest; retain arrays of each side's
installed versions and registries rather than silently merging incompatible inputs.
Preserve all native recommended versions and evidence, not just patches. A
recommendation must survive native policy filtering. One-sided discovery is not a
proven miss; absent recommendations are distinct from failed/unknown lookups.
No independent denominator or overall winner exists in this mode. The inventory
entities and patch rules below apply only to explicit legacy `--inventory` input.

## Common Identity

- `schemaVersion`: independent version for inventory, scan exports, and report; start at `1.0.0`.
- `repository`: canonical `owner/name`; do not combine repository aliases silently.
- `snapshot`: revision plus SHA-256 content-manifest hash and both configuration hashes.
- `sessionId`: shared by both provider exports, generated once during `scan`.
- `DependencyKey`: ecosystem, datasource/registry identity where relevant, package, repository-relative manifest path, installed version, versioning scheme. Normalize only with documented ecosystem rules.
- `OpportunityId`: SHA-256 of canonical dependency identity, snapshot identity, candidate version, and availability time. Repeated identical inventory entries deduplicate; conflicting fields fail validation.

Two installations in different manifests remain distinct. Scanner grouping does not change dependency identity. Missing context produces ambiguity/unknown rather than a guessed match.

## Entities

### Inventory

Contains repository/snapshot, provenance/source, `asOf`, completeness declaration, and opportunities. Each opportunity has a dependency key, candidate patch version, and availability timestamp. Unknown inventory completeness is allowed but must be prominent in the report.

Validate installed/candidate version semantics where supported. Invalid dates or contradictory identity are errors; valid but unsupported version schemes are not assessable. Inventory `asOf` and candidate release times must be compatible with actual lookup observations; do not pretend a live scan replays a historical inventory.

### ProviderScan

Contains provider (`dependabot` or `renovate`), shared identity, tool/adapter/launcher versions, image digests, configuration provenance, cache mode, start/end timestamps, dependencies, candidates, coverage entries, and sanitized diagnostics.

Run lifecycle:

```text
pending -> running -> complete | partial | failed | cancelled | unsupported
```

`pending`/`running` are internal states, not finished successful exports. `complete` requires adapter evidence, not just exit code zero. `partial` preserves known evidence alongside explicit failed scopes. Failure before source/session initialization may produce no exports, with a nonzero exit.

### CoverageEntry

Scopes extraction and lookup coverage to a manager, directory, dependency, and candidate range where known. Records complete/partial/failed/unsupported status, observation time, native evidence references, and supported negative-evidence scope.

A missing dependency is “not extracted” only when the supported manager/file extraction scope completed and proves its absence. Otherwise extraction is unknown.

### CandidateEvidence

Contains dependency key, target version, native patch classification/version scheme, observed time, policy decision, sanitized native source locator, and evidence ID.

Policy: `eligible | excluded | deferred | unknown`, with reason and policy/config references.

Detection: `detected | not-detected | unknown`. Native candidates rejected or deferred by known policy are not actionable detections; preserve them as identified-but-filtered evidence.

Do not serialize full native payloads, arbitrary configuration objects, credentials, environment variables, or raw process output. Store only explicitly allowed fields.

### OpportunityAssessment

For each provider: extraction state, policy state/reason, detection state/reason, evidence references, exact-or-later match type, and assessability.

An eligible opportunity absent from a complete, supported lookup is not detected only within the adapter's proven negative scope. Missing timestamps, candidate ambiguity, unsupported semantics, contradictory evidence, or known registry skew produce unknown.

Known exclusion/deferral with adequate native evidence can explain a non-detection for the row, but excludes it from jointly eligible coverage. An actionable detection contradicting an asserted exclusion is a policy/provenance inconsistency, not a reason to fabricate eligibility.

### ComparisonReport

Contains validated identities and input hashes, inventory provenance, scan times/skew, assessments, normalized safe evidence, metrics, policy differences, recommendations, and warnings. Reports do not add current wall-clock time or random IDs during offline comparison.

Sort opportunities by ecosystem/package/context/installed/candidate/release using explicit deterministic ordering. Sort evidence references and warnings. Render JSON and HTML from this one model.

## Matching Algorithm

1. Validate both export versions, providers, session ID, snapshot/config bundle, and inventory identity.
2. Build dependency-context indexes; expand grouped native evidence into constituent updates.
3. Reject impossible timestamp sequences; require candidate availability at or before the evidence observation.
4. For supported stable SemVer, a patch requires equal major/minor and strictly higher patch than installed. A later detected candidate covers earlier inventory candidates on that same line with `installed < inventory <= detected`.
5. Do not use scan end as invented lookup time. Missing exact observation may use a documented native timestamp interval only when the availability relation is proven; otherwise mark affected matching unknown.
6. If no positive match, use explicit coverage and policy evidence to establish non-detection or unknown.
7. Deduplicate evidence links; count each inventory opportunity once per provider regardless of candidate/group count.

## Results and Arithmetic

| Dependabot detection | Renovate detection | Row result | Winner |
|---|---|---|---|
| detected | detected | both | none |
| detected | not-detected | Dependabot only | Dependabot |
| not-detected | detected | Renovate only | Renovate |
| not-detected | not-detected | neither | none |
| either unknown | any | not assessable | none |

Positive evidence remains visible even when the paired row is not assessable.

Let `J` be all opportunities eligible for both tools. Let `A` be the subset of `J` with assessable detection for both and no known observation incompatibility.

- Report `|J|`, `|A|`, and `|J - A|`, with reasons for exclusions.
- Dependabot coverage = detected-by-Dependabot opportunities in `A` divided by `|A|`.
- Renovate coverage = detected-by-Renovate opportunities in `A` divided by `|A|`.
- Preserve integer numerator/denominator in JSON; format percentages only in presentation.
- If `|A| = 0`, rates are `null` and result is `insufficient-evidence`.
- Otherwise compare numerators: higher count wins; equal counts tie, explicitly limited to `A`.
- Also show one-tool-only eligibility, exclusions/deferrals, unknown eligibility, all row-result totals, and dependency extraction coverage. These are separate metrics, not additions to the paired denominator.

The report must explain that patch opportunities, not unique dependencies or PRs, are the counting unit. Several earlier patch releases covered by one later proposal count as several inventory opportunities for both tools under the agreed matching rule.
