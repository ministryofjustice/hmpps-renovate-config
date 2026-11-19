# Tasks: Docker-Based Dependency Patch Detection Comparison

**Feature**: `001-gappy-cli`  
**Inputs**: [spec.md](./spec.md), [plan.md](./plan.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts.md](./contracts.md), [quickstart.md](./quickstart.md)  
**Status**: Partial implementation under the scanner-native scope amendment below.
Inventory-free comparison, npm policy scenarios and Helm collection are verified.
Broader ecosystem/policy certification remains incomplete. Checkboxes represent
full acceptance, not merely the existence of a mapping or code.

## Current scope amendment and verified progress

Cache follow-up: persistent repository/scanner/image-isolated named volumes now
default on (`--no-cache` preserves fresh scans). Dependabot stages its supported bind
cache through Docker copy; Renovate mounts directly. Mode, volumes and freshness
warnings are exported. Thirteen targeted cache tests and a two-run real Docker
reuse test passed; default suite now has 173 passing tests. This is native
best-effort caching, not complete registry mirroring or universal ecosystem proof.

The user replaced automatic inventory/patch-coverage defaults with scanner-native
discovery and recommendation comparison across ecosystems. No Gappy inventory is
produced. Historical inventory, winner and coverage tasks below apply only to the
optional legacy `--inventory` mode; they are not the current default acceptance target.

- [x] D001 Compare union native discoveries by canonical ecosystem/package/manifest,
  retaining all recommended target sets, installed versions, policy and failed-scan context.
- [x] D002 Make inventory unnecessary in `full` and `compare`; default full emits four
  files and never calls Gappy inventory generation.
- [x] D003 Render Bootstrap ecosystem tables with discovery and recommendation
  differences, not independent coverage or an unsupported overall winner.
- [x] D004 Extract all Renovate manager results; require post-policy branch evidence
  so update-type-disabled candidates are not counted as actionable recommendations.
- [x] D005 Dispatch configured Dependabot ecosystems/directories individually,
  translate groups/ignores/cooldowns and resolve per-ecosystem immutable image digests.
  Keep unsupported/failed entries explicit without blocking other jobs.
- [x] D006 Verify ten controlled real npm Docker scenarios and one non-npm Helm
  scenario, plus synthetic cross-manager extraction and inventory-free CLI tests.
- [ ] D007 Certify remaining ecosystem formats/policies, private authentication,
  wildcard directories and ambiguous multi-installation contexts. Mapped is not certified.

## Implementation Progress (2026-11-11)

- Implemented Python/Click packaging, strict contracts, immutable tracked snapshots, bounded native execution, pinned root npm adapters, scan exports, offline patch matching/metrics, and static JSON/HTML reports.
- Default regression tests and an opt-in real Docker/local-registry scan-to-report test pass. The latter verifies two patch opportunities detected by both scanners on a shared snapshot, not PR outcomes.
- Local groups, ignores and cooldowns now have controlled npm proofs, alongside Helm
  lookup and multi-directory dispatch. Broader ecosystem certification, private credentials
  and complete portal policy parity remain open. No full-plan completion is claimed.
- Downstream tasks stay unchecked where their full prerequisites, scenario matrix, or required surfaces are incomplete, even if a baseline implementation exists. Do not equate the working npm prototype with all 38 tasks being done.

## Format and Execution Rules

`- [ ] Tnnn [P] [USn] Description with target paths`

- `[P]` means work can run concurrently only after its listed prerequisites pass and without editing shared files.
- `[USn]` maps to the feature's user stories. Setup and shared foundations have no story label.
- Paths are relative to the standalone package root.
- Write the relevant tests before or alongside implementation; demonstrate they exercise the required behavior.
- Do not run scans against private repositories or registries without explicit authorization. Use synthetic fixtures and controlled local registry data.
- No database, scanner database integration, PR-history analytics, or remote PR creation is required.
- A blocked feasibility gate is a blocker, not permission to replace scanner output with simulated configuration results.

## Phase 1 - Setup

- [x] T001 Confirm Python 3.12+/uv and Docker/architecture availability; initialize the standalone Python src-layout package in `pyproject.toml`, `uv.lock`, and `src/gappy/__init__.py` with Click, Pydantic v2, PyYAML, Hatchling, the dev extra, and the `gappy = "gappy.cli:main"` console entry point. Stable numeric SemVer matching currently needs no additional version library. Do not initialize Git or create a branch without a separate request.
- [x] T002 Configure strict mypy, Ruff lint/format checks, and pytest in `pyproject.toml` and `tests/test_cli.py`; use Click `CliRunner` for command tests and verify installed-executable behavior separately from source imports.
- [ ] T003 Create sanitized repository/registry scenarios in `tests/fixtures/native/` covering npm patches, no update, grouping, ignores, age delays, multiple directories, and failed lookup. Keep private repository content and credentials out of fixtures.

**Setup checkpoint**: Package tooling runs; controlled scenarios have explicit expected updates and policy decisions.

## Phase 2 - Scanner Feasibility Gate

- [x] T004 Select and record exact native/launcher versions, verified image digests, architecture support, and upstream references in `src/gappy/scan/toolchain.json` and `README.md`; reject mutable-tag-only toolchains.
- [ ] T005 [US1] Prove Dependabot's Docker updater/proxy path against T003 scenarios; preserve sanitized native YAML events and expected normalized observations in `tests/fixtures/native/dependabot/`, and record job translation, extraction, policy, no-mutation, and negative-evidence limits in `research.md`.
- [ ] T006 [P] [US1] Prove Renovate's pinned Docker local lookup path against T003 scenarios; preserve sanitized structured lookup fixtures in `tests/fixtures/native/renovate/`, and record preset, extraction, filtering, no-mutation, and negative-evidence limits in `README.md`.
- [ ] T007 [US5] Reconcile both prototypes into a tested manager/rule capability matrix in `README.md` and the feasibility record in `research.md`; document npm, Actions, Helm, Docker, and configured additional managers as supported, unsupported, or unverified. Confirm a shared actionable patch and a defensible negative-evidence case for both tools.

**Hard gate**: T005-T007 must demonstrate real native evidence without remote writes or unsafe host access. If either integration cannot support the required evidence, report the concrete blocker and stop general implementation. Local registry fixtures are acceptable; fabricated native results are not prototype evidence.

## Phase 3 - Shared Contracts and File Safety

- [ ] T008 Implement typed inventory, provider export, diagnostics, evidence, coverage, policy, and report contracts using strict Pydantic v2 models/discriminated unions in `src/gappy/contracts.py`; add `tests/test_contracts.py` for schema versions, forbidden coercions, timestamps, identity, cross-field references, invalid terminal statuses, and secret-bearing URL rejection.
- [ ] T009 Implement deterministic identity/hash/context normalization in `src/gappy/scan/normalize.py`; add `tests/test_normalize.py` for grouped updates, repeated evidence, conflicting inventory duplicates, directory isolation, registry identity, and ambiguous context.
- [ ] T010 Implement sanitized diagnostics and allowlisted serialization in `src/gappy/io.py`; test secret canaries in inputs, parse failures, nested native payloads, and subprocess errors in `tests/test_io.py`. Never print raw input excerpts or process output.
- [ ] T011 Implement safe JSON loading and staged no-clobber publication in `src/gappy/io.py`; extend `tests/test_io.py` for distinct outputs, input collisions, symlink/hard-link aliases, existing outputs, permission failures, partial publication, and cleanup restricted to owned staging files.

**Foundation checkpoint**: Contracts reject incompatible/unsafe inputs; no result type permits a failed scan to masquerade as an empty successful scan.

## Phase 4 - US2: Dependency and Patch Assessment

**Goal**: Compare native lookup evidence with inventory without using PR outcomes.

- [ ] T012 [US2] Add version/matching fixtures and implement supported stable SemVer patch rules in `src/gappy/compare/versions.py` and `tests/test_versions.py`; cover exact patches, later-patch coverage, future releases, minor/major boundaries, prereleases, digests, and unsupported schemes.
- [ ] T013 [US2] Implement input compatibility and contextual evidence indexes in `src/gappy/compare/assess.py`; add `tests/test_compare.py` for mismatched providers, sessions, snapshots/config hashes, inventory identity, grouped results, duplicates, and multiple installations.
- [ ] T014 [US2] Implement per-provider extraction, policy, and detection assessment in `src/gappy/compare/assess.py`; extend `tests/test_compare.py` for complete versus partial negative scope, failed lookup, missing timestamps, ambiguous context, latest-only output, exclusions/deferrals, and policy contradictions.
- [ ] T015 [US2] Add observation compatibility checks in `src/gappy/compare/assess.py` and `tests/test_compare.py`; known registry skew and releases unavailable at lookup time must not become false misses, and scan-end time must not be invented as a lookup time.

**Independent story test**: Normalized fixture inputs yield exact/later patch matches and safe unknowns, with each opportunity counted once per provider.

## Phase 5 - US3: Fair Winner Tables

**Goal**: Make per-patch winners and shared-denominator coverage transparent.

- [ ] T016 [US3] Implement five row outcomes and sole-detector winners in `src/gappy/compare/metrics.py` and `tests/test_metrics.py`; positive evidence must remain visible when the paired outcome is not assessable.
- [ ] T017 [US3] Implement policy partitions, extraction coverage, jointly eligible total, assessable denominator, integer counts, nullable coverage, and subset winner in `src/gappy/compare/metrics.py`; test exact `3/4` versus `2/4`, exclusions, unknowns, ties, empty inventory, and zero-denominator insufficient evidence in `tests/test_metrics.py`.
- [ ] T018 [P] [US3] Implement evidence-linked recommendations and safe configuration differences in `src/gappy/compare/recommendations.py` and `tests/test_recommendations.py`; no opaque score or unsupported hosted-service equivalence.
- [ ] T019 [US3] Assemble one deterministic report model in `src/gappy/compare/assess.py`; verify stable ordering, input hashes, evidence references, inventory limitations, and byte-identical JSON for repeated fixed inputs in `tests/test_compare.py`.
- [ ] T020 [US3] Render accessible opportunity/winner and summary tables in `src/gappy/report/html.py` and `src/gappy/report/styles.css`; add `tests/test_report.py` for JSON/HTML agreement, headings/captions, text-based statuses, fractions, and non-color-only comparisons.
- [ ] T021 [US3] Extend `tests/test_report.py` and rendering helpers for imported text/attribute escaping, unsafe URL schemes, credential-bearing URLs, safe local evidence anchors, no scripts/remote assets, and byte-identical HTML.

**Independent story test**: Four assessed opportunities show Dependabot `3/4`, Renovate `2/4`, and the correct row winners. Policy-only/unknown opportunities do not inflate the paired denominator. Both/neither/tie/insufficient-evidence cases remain explicit.

## Phase 6 - US4: Offline Comparison CLI

**Goal**: Reuse normalized JSON to write both reports without Docker or credentials.

- [ ] T022 [US4] Implement the Click group, compare command/options, and help in `src/gappy/cli.py`; validate required options and map usage/execution/interruption exit statuses to `contracts.md`, explicitly testing Click interruption handling for status `130`.
- [ ] T023 [US4] Wire `compare` to validation, assessment, rendering, and safe publication in `src/gappy/cli.py`; add `tests/test_cli.py` for valid outputs, schema/session mismatch, malformed data, existing outputs, publication failure, and clear unknown-result reporting.
- [ ] T024 [US4] Add offline executable smoke fixtures in `tests/fixtures/normalized/` and `tests/fixtures/inventory/`; prove both outputs, exact table/metric contents, safe evidence references, and deterministic reruns in `tests/test_cli.py`. Run with Docker unavailable and an environment/process boundary that fails any attempted scan/network operation.

**Offline milestone**: A working comparison CLI is available using validated exports. This is not completion of the Docker-scanning product.

## Phase 7 - US1: Production Docker Scan Orchestration

**Goal**: Produce compatible exports from actual pinned integrations on one source snapshot.

- [ ] T025 [US1] Implement revision materialization, sorted content hashing, config hashes, isolated copies, and source-integrity checks in `src/gappy/scan/snapshot.py`; add `tests/test_snapshot.py` for untracked exclusion, traversal, escaping symlinks, unsupported submodule/LFS content, missing configs, and identical scanner inputs.
- [ ] T026 [P] [US1] Implement `subprocess.Popen` execution with argument lists and `shell=False`, bounded output/timeouts, explicit environment allowlists, cancellation, and owned-resource tracking in `src/gappy/scan/process.py`; add `tests/test_process.py` for failures, signals, injection-resistant arguments, Docker mount restrictions, and targeted cleanup.
- [ ] T027 [US1] Implement the proven Dependabot launcher/job translation/native adapter in `src/gappy/scan/dependabot.py`; add `tests/test_dependabot.py` against T005 fixtures for supported settings, grouped dependencies, fresh cache, coverage, unsupported formats, and sanitized failures.
- [ ] T028 [P] [US1] Implement the proven Renovate Docker lookup/native adapter in `src/gappy/scan/renovate.py`; add `tests/test_renovate.py` against T006 fixtures for config/preset behavior, filters/age, structured observations, coverage, fresh cache, unsupported formats, and sanitized failures.
- [ ] T029 [US1] Implement shared session/preflight and provider lifecycle orchestration in `src/gappy/scan/session.py`; add `tests/test_scan.py` for Docker/image/architecture/authentication failures, shared identity, second-provider execution after a safe first-provider failure, partial/cancelled exports, and cleanup errors.
- [ ] T030 [US1] Wire `scan` options, help, finite timeout, explicit credentials-file handling, and both export destinations in `src/gappy/cli.py`; extend `tests/test_cli.py` for preflight failure, valid export publication, nonzero failed-scan status, and interruption status `130`.
- [ ] T031 [US1] Run a controlled Docker end-to-end fixture using real supported integrations and local registry data; verify normalized outputs, immutable source/config identity, no remote mutations, safe mounts, and repeatable observations in `tests/test_scan_integration.py`. Keep this clearly separate from fake-runner tests and optional live-registry verification.

**Independent story test**: Both real integrations produce schema-valid exports for one shared supported scenario. A controlled failure produces explicit non-success exports, not a successful empty scan.

## Phase 8 - US5: Diagnostics, Limitations, and Guidance

**Goal**: Explain exactly what each scan and comparison can establish.

- [ ] T032 [US5] Propagate manager/rule capability, policy translation/safety overrides, unresolved presets, filtering reasons, cache conditions, and observation skew through existing adapters, `src/gappy/scan/session.py`, and report recommendations; test their effect on assessability in `tests/test_scan.py` and `tests/test_compare.py`.
- [ ] T033 [US5] Complete command help and actionable sanitized diagnostic coverage in `src/gappy/cli.py`, `tests/test_cli.py`, and `tests/test_io.py`; cover missing Docker, authentication, unknown schemas, unsupported managers/native output, malformed data, and output errors without leaking raw messages/secrets.
- [ ] T034 [P] [US5] Document installation, scan credentials/network risks, supported pinned integrations, capability matrix, inventory provenance/schema, safety restrictions, winner arithmetic, and example output in `README.md`; synchronize `contracts.md` and `quickstart.md` with the actual supported executable.

**Independent story test**: Every unsupported or partial scenario explains its reason and cannot silently improve coverage or imply exact hosted-bot parity.

## Phase 9 - Packaging and Release Validation

- [ ] T035 Build wheel/sdist with `uv build` and verify the installed Click console entry point, CSS/toolchain asset inclusion through `importlib.resources`, and clean installation in a separate temporary virtual environment; add `tests/test_package.py` and adjust `pyproject.toml` only as needed.
- [ ] T036 Run strict mypy, Ruff lint/format checks, wheel/sdist build, and the complete default pytest synthetic suite; run the controlled Docker integration check where prerequisites exist. Record actual results and blockers in the feature validation section of `quickstart.md`, not inferred successes.
- [ ] T037 Audit acceptance coverage against FR-001-FR-024 and SC-001-SC-007 in `spec.md`; verify real scan-to-export-to-offline-report flow and update `plan.md` status only when its gates pass.
- [ ] T038 Remove only task-owned scratch containers/files and confirm unrelated checkout files remain unchanged; report remaining limitations and whether any manual live-registry validation was performed.

## Task Dependencies

All prerequisites below must be complete before a task is marked done. A range includes every task in that range.

| Task | Prerequisites |
|---|---|
| T001 | None |
| T002 | T001 |
| T003 | None |
| T004 | T001 |
| T005 | T002, T003, T004 |
| T006 | T002, T003, T004 |
| T007 | T005, T006 |
| T008 | T007 |
| T009 | T008 |
| T010 | T008 |
| T011 | T010 |
| T012 | T009 |
| T013 | T009, T010 |
| T014 | T012, T013 |
| T015 | T014 |
| T016 | T015 |
| T017 | T016 |
| T018 | T015 |
| T019 | T017, T018 |
| T020 | T019 |
| T021 | T020 |
| T022 | T002, T008 |
| T023 | T011, T019, T021, T022 |
| T024 | T023 |
| T025 | T007, T009, T011, T024 |
| T026 | T007, T010, T024 |
| T027 | T005, T009, T025, T026 |
| T028 | T006, T009, T025, T026 |
| T029 | T011, T025-T028 |
| T030 | T023, T029 |
| T031 | T030 |
| T032 | T024, T031 |
| T033 | T032 |
| T034 | T032 |
| T035 | T033, T034 |
| T036 | T035 |
| T037 | T036 |
| T038 | T037 |

## Parallel Work and Delivery Order

- T003 can proceed independently of tooling setup.
- T005/T006 are independent native prototypes, with separate fixture/document targets.
- T012/T013 and T016/T018 may proceed in parallel where test/source ownership remains separate.
- T025/T026 and T027/T028 use separate source/test files.
- T033/T034 can proceed in parallel after diagnostics and supported behavior are settled.
- Do not parallelize edits to shared schemas, CLI, aggregate tests, or publication helpers without explicit coordination.

**Delivery order**: feasibility gate -> shared contracts -> US2 assessment -> US3 tables -> US4 offline CLI -> US1 production scans -> US5 diagnostics -> packaging/validation.

US2-US4 use validated native fixtures and can be verified without Docker. US1 remains a release requirement; neither the offline milestone nor fake-runner tests alone satisfy the product.
# Configuration-forecast implementation amendment

Implemented surfaces: independent repository-first fallback resolution, separate
optimised examples, four isolated native scans, grouped proposal adapters, versioned
input/output contracts, explicit read-only GitHub capture, bounded conditional
schedule/queue simulation, four-column offline reporting and replay.

Capability boundaries remain explicit rather than complete certification: native
future-clock injection, cooldown/age transitions, resolved remote preset policy,
Renovate natural-language/cron schedules, security forecasts, custom bot identities
and ambiguous existing/closed PR matching. These return unknown creation totals;
they must not be advertised as actual or exhaustive future PR counts.

Validation includes exact native grouping/capacity fixtures, deterministic replay,
snapshot/config precedence, no-clobber outputs, unknown-state propagation, escaping,
token transport and compatibility suites.

Completion hardening: forecast forwards native concurrency/output-budget controls,
stops later variants on cancellation, preserves exit 130 and diagnostic reports,
rejects ambiguous replay policy/PR identities, guards unmodelled PR policy, and
suppresses incomparable configuration deltas. Real four-way integration checks
publish all seven deliverables and verify serialized counts against native evidence.
