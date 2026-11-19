# Feature Specification: Docker-Based Dependency Patch Detection Comparison

**Feature identifier**: `001-gappy-cli` (no Git branch created)  
**Created**: 2026-11-11  
**Updated**: 2026-11-11  
**Status**: Draft  
**Input**: Run Dependabot and Renovate scanning through Docker, output JSON, and compare dependency/patch detection under existing configurations, not final PR outcomes.

## User Scenarios & Testing

### User Story 1 - Run consistent Docker scans (Priority: P1)

As a maintainer, I want Gappy to run both tools on the same repository content using their existing configuration so their dependency detection can be compared consistently.

**Independent test**: Use a synthetic snapshot and controlled scanner processes to verify snapshot identity, config preservation, pinned provenance, isolated workspaces, and two normalized exports.

**Acceptance scenarios**:

1. Given a repository snapshot and both configurations, when `gappy scan` runs, both Docker-backed scans receive identical source content in separate workspaces.
2. Given registry access is required, scans may access registries and required preset sources with explicitly supplied credentials, without creating PRs, branches, issues, or other remote mutations.
3. Given supported pinned scanner versions, Gappy records image digests, adapter/launcher versions, config hashes, scan session, observation times, and completeness in each JSON export.
4. Given failure, timeout, cancellation, or unsupported capability, Gappy records an explicit non-success state and sanitized error, never a successful empty detection list.

### User Story 2 - Compare dependency and patch detection (Priority: P1)

As a maintainer, I want to see extracted dependencies and available patches identified by each scanner, without waiting for PR creation.

**Independent test**: Compare normalized scan fixtures against a local inventory containing patches detected by one, both, and neither scanner.

**Acceptance scenarios**:

1. Given an actionable native patch lookup result, comparison counts it as detected independently of PR creation or merge status.
2. Given a later actionable patch in the same supported patch line, earlier inventory patches available by lookup time are also counted.
3. Given a grouped result or the same package in multiple directories, dependency/version/context matching preserves separate opportunities without double-counting evidence.
4. Given failed extraction, lookup, or unsupported semantics, affected opportunities are unknown/not assessable rather than not detected.
5. Given an inventory opportunity absent from both complete, assessable scan results, the report shows “neither.”

### User Story 3 - See clear winner tables (Priority: P1)

As a maintainer, I want a clear per-patch comparison and an honest summary of which tool detected more.

**Independent test**: Verify one-tool-only detections, both/neither results, unknowns, policy differences, zero denominators, and tied coverage.

**Acceptance scenarios**:

1. Given exactly one scanner detected an opportunity and the other is assessable, show that scanner as the per-opportunity winner.
2. Given exclusion or deferral under one tool's configuration, display that policy reason beside the detection result instead of hiding it.
3. Given jointly eligible, assessable opportunities, summarize each scanner's detections using the same denominator and identify the higher count or a tie.
4. Given unknowns or no jointly assessable opportunities, disclose exclusions and return insufficient evidence where needed; never invent a winner.
5. Given dependencies or patches outside the supplied inventory, do not claim inventory completeness or a universal tool ranking.

### User Story 4 - Reuse exports offline (Priority: P1)

As a maintainer, I want to retain JSON scan exports and produce reports later without Docker, registry access, or credentials.

**Independent test**: Run `gappy compare` on synthetic exports and inventory without Docker or network access and verify matching JSON/HTML content.

**Acceptance scenarios**:

1. Given two compatible scan exports and an independent inventory, comparison writes versioned JSON and self-contained Bootstrap-styled HTML to explicit paths.
2. Given incompatible repository snapshots or scan sessions, comparison fails with an actionable diagnostic.
3. Given imported markup, unsafe links, or secret-bearing configuration, rendering escapes text, refuses unsafe link targets, and excludes secrets.
4. Given a path collision with an input, comparison refuses to overwrite it.

### User Story 5 - Diagnose scan and policy limitations (Priority: P2)

As a maintainer, I want to understand unsupported manager behavior, policy rules, and live-registry variability so I can assess the comparison's reliability.

**Independent test**: Exercise missing Docker, image/start failures, incomplete scans, unresolved presets, unsupported native schemas, and registry timing differences.

**Acceptance scenarios**:

1. CLI help documents scan versus compare inputs, required Docker capabilities, output contracts, credentials, and network boundaries.
2. Known limitations or overrides are surfaced per manager/dependency and affect assessability.
3. Recommendations cite scan/inventory evidence or policy and do not claim exact hosted-bot parity.

## Functional Requirements

- **FR-001**: Implement a standalone Python CLI using Click, providing `gappy scan`, `gappy compare`, and command help through an installed `gappy` console entry point. `scan` requires only `--repository`: default to a temporary GitHub checkout, revision `HEAD`, and unique provider export paths under `./gappy-results/<owner>/<repository>/`. Preserve explicit source/revision/output overrides, print both destinations, and remove temporary checkouts after execution.
- **FR-002**: Gappy MUST orchestrate both scans through Docker-backed execution against isolated copies of one immutable repository snapshot, preserving existing tool configurations.
- **FR-003**: The scan stage MAY access registries and preset sources; offline comparison MUST NOT require network access, Docker, PR exports, or credentials.
- **FR-004**: Scans MUST NOT create remote PRs, branches, issues, commits, or other remote mutations, and MUST NOT modify the source checkout.
- **FR-005**: Record shared session ID, repository/revision/content hash, configuration hashes, tool/adapter/launcher versions, image digests, scan/lookup times, cache conditions, and completion/error states.
- **FR-006**: Pin native integrations and use version-aware adapters to export a documented common JSON schema. Native YAML events or structured lookup logs MAY be transformed; do not assume identical native JSON interfaces.
- **FR-007**: Exports MUST retain extracted dependencies, installed and candidate versions, ecosystem/package/context, supported versioning scheme, eligibility decision, detection evidence, and coverage/limitations.
- **FR-008**: Separate eligible, excluded, deferred, and unknown policy decisions from observed detection. Unknown inherited policy or unsupported translation MUST NOT silently become eligibility.
- **FR-009**: An actionable native patch lookup result MUST count as detection without requiring PR creation. Retain known but excluded/deferred candidates separately.
- **FR-010**: Comparison MUST require both normalized scan exports and an independent local patch inventory identifying repository/snapshot, observation metadata, dependency/context, installed/candidate versions, and patch availability dates.
- **FR-011**: Match by ecosystem/package, installed version, and manifest/directory context within a shared snapshot. Grouped native updates MUST preserve constituent dependencies; duplicate evidence MUST NOT inflate opportunity counts.
- **FR-012**: Later actionable patches in the same supported patch line MUST cover earlier inventory patches available by lookup time, never future releases or other minor/major lines.
- **FR-013**: Absence MUST mean not detected only when extraction, policy interpretation, and lookup are sufficiently complete for that opportunity. Otherwise report unknown/not assessable.
- **FR-014**: Show an opportunity table with package/context, installed/available versions, release date, per-provider extraction, eligibility, detection/evidence, result, and sole-detector winner where assessable.
- **FR-015**: Show all inventory totals and policy-only differences. Paired coverage MUST use the same jointly eligible and assessable denominator; disclose unknowns and exclusions from the total jointly eligible set.
- **FR-016**: Report higher detection count, tie, or insufficient evidence. Zero denominator MUST produce unavailable coverage and insufficient evidence, not a 0% tie. No opaque score.
- **FR-017**: HTML and versioned JSON MUST contain consistent metadata, opportunities, scan evidence, metrics, policy differences, recommendations, and limitations.
- **FR-018**: HTML MUST be accessible, static, self-contained, and Bootstrap-styled, with escaped imported text and safe evidence references/links; no remote assets or browser-side API calls.
- **FR-019**: Scan export paths MAY default to a unique run directory with printed destinations. Comparison MAY select the latest repository run and default reports beside its exports, while retaining explicit overrides and requiring an explicit inventory. Missing/incomplete latest runs MUST fail rather than silently selecting older data. All output destinations MUST be printed and distinct from inputs; existing files MUST NOT be overwritten. Invalid data/schema/dates/snapshot/session and write failures MUST produce actionable sanitized errors.
- **FR-020**: Secrets MUST NOT enter exports, persisted logs, reports, or diagnostics. Explicit scan credentials MUST be isolated from report generation and unrelated host files.
- **FR-021**: Docker execution MUST use bounded timeouts, explicit cancellation/failure states, and cleanup limited to owned resources. Scanner containers MUST NOT mount the Docker socket or broad home/credential directories.
- **FR-022**: Live observations and their time skew MUST be disclosed. Known availability differences MUST make affected opportunities not assessable; no arbitrary historical replay or unsupported claim of identical registry data.
- **FR-023**: Recommendations MUST cite concrete scan/inventory evidence or known policy. Results are analytics, not security findings.
- **FR-024**: Scans MUST NOT silently enable repository scripts/custom commands. Any necessary manager execution and remaining container risk MUST be documented.

## Key Entities

- **Scan Session**: One comparison collection with shared immutable source/config identity, provider runs, and observation metadata.
- **Provider Scan Export**: Versioned normalized dependencies, updates, policy decisions, evidence, provenance, coverage, and failures for one scanner.
- **Patch Opportunity**: Independent inventory entry for one installed dependency/context and available patch release.
- **Opportunity Assessment**: Provider-specific extraction, eligibility, detection, evidence, and reason for unknown/exclusion/deferral.
- **Comparison Report**: Per-opportunity winner table, shared-denominator coverage, policy differences, recommendations, and limitations in JSON and HTML.

## Success Criteria

- **SC-001**: One scan invocation produces compatible normalized exports for both supported Docker integrations on the same immutable fixture snapshot.
- **SC-002**: Supported native-result fixtures prove extraction, policy preservation, grouped updates, exact/later patch matching, context isolation, and unsupported-case reporting.
- **SC-003**: Synthetic comparisons correctly report Dependabot only, Renovate only, both, neither, and not assessable, with evidence and visible policy reasons.
- **SC-004**: Coverage uses equal denominators and correctly handles exclusions, unknowns, empty inventories, zero denominators, and ties.
- **SC-005**: Failed/partial/cancelled scans cannot yield successful empty results or false missed-patch conclusions.
- **SC-006**: Both report formats pass structure, escaping, safe-link, secret-exclusion, path-protection, and deterministic offline smoke tests without Docker or credentials.
- **SC-007**: Package typecheck, lint, and tests pass; automated tests require no live registries or GitHub authentication.

## Feasibility and Planning Gates

Before implementing general orchestration, verify pinned integrations on sanitized fixtures:

- Dependabot CLI orchestrates Docker updater/proxy jobs and records API-call results as YAML via its output option. Confirm job translation, configuration coverage, and whether required extraction/candidate evidence is available. Transform supported events into Gappy JSON; no invented `--json` interface.
- Renovate's local platform supports lookup dry runs; it is experimental and has preset/platform limitations. Confirm supported structured lookup evidence and rule behavior for the pinned image. Do not assume full dry-run parity or PR creation support.
- If a manager/configuration cannot produce the required scan evidence or cannot honor safety requirements, mark it unsupported/not assessable. A working comparison requires documented supported adapters, not replacing real scanners with configuration simulation.

Reference documentation (capabilities must be checked against chosen versions):

- [Dependabot CLI](https://github.com/dependabot/cli/blob/main/README.md)
- [Renovate local platform](https://docs.renovatebot.com/modules/platform/local/)

## Scanner-native scope amendment (supersedes inventory-based defaults)

The default comparison uses every dependency discovered by either native scanner,
across configured ecosystems. Gappy does not generate an independent inventory.
Compare recommended native target sets per ecosystem/package/manifest, retaining
installed-version and registry differences, empty recommendations, policy evidence
and failures. Docker scanners use the same frozen local configs. `full` and
`compare` require no inventory. No independent coverage percentage or overall
winner is claimed. Explicit `--inventory` selects legacy patch assessment only.
Acceptance requires non-npm extraction tests, no inventory/network collection in
comparison, post-policy recommendation evidence, and separate discovery versus
recommendation differences in JSON and Bootstrap HTML.

## Historical Scope and Assumptions (amended above)

- Initial target: `ministryofjustice/hmpps-developer-portal`; current supplied configuration, not hardcoded policy descriptions, governs scanning.
- Independent inventory remains a required comparison baseline, including updates neither scanner detected. `full` generates a limited direct-npm inventory from frozen files and registries by default; `--inventory` overrides it. Provenance warnings disclose excluded scope and never claim complete repository coverage.
- Network access is allowed for explicit acquisition, inventory collection, and scans only. Given fixed normalized inputs, comparison remains deterministic and offline.
- These are point-in-time scanner observations, not retrospective PR windows or claims of exact hosted-service behavior.
- No organization-wide polling, scanner-database integration, remote PR creation, final-PR analytics, registry-history replay, portal routes, or CI integration.
# Configuration-forecast scope amendment

The current dependency comparison remains supported. Add a separate single-repository
configuration forecast showing baseline and optimised results for each scanner together.
Each baseline selects its tracked repository config, falling back independently to
Gappy only when absent. Optimised configs are a second complete Gappy config set.

The user now also needs native grouped PR-demand counts and conditional predictions
over an explicit future window with repeated invocations and read-only GitHub state.
Potential demand, new creations, queues, schedule deferrals, existing matches and
unknowns are separate measures. Native dependency recommendation counts are not PRs.
No actual PR creation, future releases or merge/process simulation is in scope.
Unsupported policies or platform evidence must remain unknown. Preserve configuration
provenance, same-source isolation, safe local execution and deterministic offline replay.
