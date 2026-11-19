# Implementation Plan: Docker-Based Patch Detection Comparison

**Date**: 2026-11-11  
**Feature**: `001-gappy-cli`  
**Specification**: [spec.md](./spec.md)  
**Status**: Amended to scanner-native discovery/recommendation comparison.
Default `full` and `compare` no longer generate or require an inventory. Multi-manager
extraction and configured-ecosystem job dispatch are implemented; npm policy and Helm
have controlled real Docker proofs. Remaining manager/credential certification stays open.

## Current implementation amendment

Cache amendment: default scan/full runs now reuse repository/scanner/image-isolated
named volumes. Dependabot also isolates ecosystems and stages downloads through the
CLI's bind-only interface. `--no-cache` preserves clean runs. Cache identities and
freshness limitations are recorded; no normal cleanup removes persistent volumes.

The user explicitly replaced the inventory/patch-coverage default below. Use
`compare/discovered.py` to compare every native dependency/context and recommended
target set across ecosystems; render grouped Bootstrap tables. No default independent
coverage or winner. `--inventory` is a compatibility-only opt-in. Translate local
Dependabot entries individually and resolve official ecosystem updater images to
recorded digests before execution; Renovate resolves presets/rules and exposes
post-policy actionable branch evidence. Keep unsupported entries and failed lookups
explicit without discarding supported jobs. Historical sections below describe the
legacy assessment mode, not the current default.

## Summary

Build a standalone Python CLI using Click with two independent workflows:

1. `scan`: freeze repository content, run pinned Docker-backed Dependabot and Renovate integrations on isolated copies, and normalize supported native evidence into two JSON exports.
2. `compare`: validate those exports against an independent patch inventory and produce deterministic JSON and self-contained HTML, without Docker, network access, or credentials.

The report compares dependency extraction and actionable patch detection, not final PR outcomes. It identifies per-opportunity winners and compares overall coverage only on the jointly eligible, assessable subset.

## Technical Context

| Area | Planned choice |
|---|---|
| Runtime | Python 3.12+ with explicit type annotations and strict mypy checking |
| Package | `pyproject.toml`, src layout, Hatchling wheel/sdist, `gappy = "gappy.cli:main"` console entry point; uv with committed `uv.lock` |
| CLI | Click command group with `scan` and `compare`; command-specific validation and explicit exit codes |
| Process execution | Python `subprocess.Popen`, argument lists, `shell=False`, bounded streams/timeouts and cancellation |
| Validation | Pydantic v2 strict models and discriminated unions shared by runtime validation and type checking |
| Native formats | PyYAML safe loading for Dependabot job/results; version-specific structured Renovate result parsing |
| Version matching | Validated stable numeric SemVer triples only; no PEP 440 conversion or unsupported tag/range coercion |
| Testing | pytest fixtures, Click `CliRunner`, monkeypatched process runner, and installed-executable subprocess smoke tests |
| Quality checks | mypy strict, Ruff lint/format checks, pytest, wheel/sdist build |
| Reports | Server-side escaped HTML with embedded original Bootstrap-compatible CSS; no client JavaScript |
| Storage | Explicit local JSON/HTML files; no service or database |
| Execution targets | macOS/Linux hosts with Docker; compare requires only Python and installed package dependencies |

Choose and lock exact dependency, scanner, launcher, updater, and proxy versions during implementation. Do not write unverified image digests into the plan or use mutable tags as reproducibility evidence. ARM64/AMD64 compatibility is part of scanner feasibility testing, not assumed.

## Constitution Check

| Principle | Design response | Gate |
|---|---|---|
| Clear, type-safe code | Small concrete adapters; shared validated contracts; pure comparison functions | Typecheck and review |
| Evidence and explicit contracts | Per-dependency provenance, coverage, policy state and reasons; shared denominator | Adapter and comparison fixtures |
| Tests with every change | Synthetic native fixtures, fake process runner, CLI smoke tests | No credentials/live registries in default tests |
| Maintainability | Separate acquisition from comparison; one report model for JSON/HTML | Contract tests and documentation updates |
| Safe reproducible outputs | Frozen snapshots, pinned integrations, allowlisted output fields, escaped HTML | Safety and determinism tests |
| Network boundary | Network only in scan integrations; compare imports no Docker/network code | Offline CLI smoke test |

No constitutional exception is proposed. Unresolved integration capabilities must produce unsupported/unknown results, not weakened evidence rules.

## Planned Structure

```text
pyproject.toml
uv.lock
README.md
src/
  gappy/
    __init__.py
    cli.py
    contracts.py
    scan/
      __init__.py
      session.py
      snapshot.py
      process.py
      dependabot.py
      renovate.py
      normalize.py
      toolchain.json
    compare/
      __init__.py
      assess.py
      versions.py
      metrics.py
      recommendations.py
    report/
      __init__.py
      html.py
      styles.css
    io.py
tests/
  fixtures/
    native/
    normalized/
    inventory/
  test_adapters.py
  test_scan.py
  test_compare.py
  test_report.py
  test_cli.py
```

Keep schema definitions and discriminated state types together initially; do not introduce plugin discovery, a generic workflow engine, or a framework. Add files only when their concrete responsibility warrants it.

## Implementation Sequence and Gates

### Phase 0 - Prove native integrations

1. Select exact upstream versions and image digests. Record platform/architecture and image verification requirements.
2. Run sanitized npm fixtures through Dependabot's Docker updater/proxy path and Renovate's Docker local lookup path. Native tools, not configuration simulations, must identify updates.
3. Capture sanitized native structured evidence for: actionable patch, no update, grouped update, ignore, cooldown/minimum age, multiple directories, failed lookup, and unresolved preset.
4. Prove configuration translation and non-mutation behavior. Identify any safety override and its effect on findings.
5. Establish whether outputs enumerate candidates, only actionable/latest updates, extraction, and filtering reasons. Define opportunity-level completeness conservatively; successful process exit alone cannot establish negative evidence.
6. Publish a tested capability matrix for npm, GitHub Actions, Helm, Docker, and configured additional managers such as pre-commit. Unsupported managers remain visible; npm is the first prototype, not an implicit promise of all-manager support.

**Exit gate**: Each provider must produce real normalized detection evidence for at least one shared supported patch scenario, with a demonstrated negative-evidence rule and no remote mutations. If this fails, stop and report the precise blocker before general orchestration. Do not report the product complete using fake adapters.

### Phase 1 - Contracts and offline comparison

Depends on Phase 0 evidence for the adapter-facing contract.

1. Implement validated schemas from [data-model.md](./data-model.md) and [contracts.md](./contracts.md).
2. Reject incompatible schema versions, snapshots, configuration bundles, or sessions. Validate timestamps, dependency context, safe paths, and evidence references.
3. Implement exact and later-patch matching, temporal availability checks, tri-state detection, eligibility partitioning, metrics, and evidence-based recommendations.
4. Implement JSON and HTML from the same immutable report object.
5. Add offline CLI smoke tests and synthetic fixtures with exact expected outcomes and denominators.

**Exit gate**: All five opportunity outcomes, coverage calculations, zero denominator, uncertainty, safety, and determinism pass without Docker or credentials.

### Phase 2 - Snapshot and Docker orchestration

Depends on Phase 0 validated launch paths and Phase 1 contracts.

1. Materialize a tracked Git revision into an owned temporary workspace; exclude untracked/local files by default. Hash a sorted content manifest, record config hashes, and prepare equivalent isolated copies.
2. Reject traversal, escaping symlinks, unresolved submodules/LFS content, or other unsupported snapshot materialization rather than silently scanning different content. Document supported snapshot content.
3. Preflight Docker, pinned image identities, outputs, credentials, and manager support before launching either provider.
4. Run each integration with fresh isolated cache/workspace state, bounded output and execution time, and explicit owned resource IDs.
5. Normalize dependency-level results, policy/provenance, observation times, and failures; attempt the second scan even if the first fails when safe to do so.
6. On cancellation, stop only owned processes/containers and write valid partial/cancelled exports when session/output initialization succeeded. Cleanup errors remain visible.

Dependabot's launcher may use the host Docker API; updater/scanner containers may not receive the Docker socket. Do not solve orchestration using privileged Docker-in-Docker or broad host mounts.

**Exit gate**: Controlled orchestration tests prove identical source identity, no source changes, isolated credentials, non-success states, timeouts, cancellation, and owned-resource cleanup.

### Phase 3 - End-to-end packaging and documentation

Depends on Phases 1 and 2.

1. Wire `scan`, `compare`, executable packaging, and all help/error paths.
2. Document inventory preparation, schemas, supported versions/managers, credentials, network boundary, policy limitations, and example tables.
3. Verify both report outputs and export reuse with synthetic fixtures.
4. Run mypy, Ruff lint/format checks, build, and the complete pytest suite. Verify wheel/sdist contents and the installed `gappy` executable in an owned temporary virtual environment, including bundled CSS/toolchain resources loaded through `importlib.resources`.
5. Optionally run manual live scans with explicit authorization; record tested capabilities without making live access an automated test prerequisite.

**Completion gate**: Real pinned Docker integrations, normalized exports, offline reports, tests, and documented limitations are delivered. A specification, fake runner, or successful empty result is not proof of scanner integration.

## Verification Matrix

| Requirement area | Evidence |
|---|---|
| FR-001-007 | CLI/help, snapshot/provenance, adapter and schema tests |
| FR-008-013 | Policy, grouped update, exact/later patch, timing, partial lookup fixtures |
| FR-014-017 | Table rows, summary arithmetic, zero/tied/unknown cases; JSON/HTML agreement |
| FR-018-020 | HTML/URL injection, secret canaries, path/symlink/hard-link collisions, offline smoke |
| FR-021-024 | Fake runner and controlled-container failures, cancellation, cleanup, script/safety behavior |

Use a decisive metric fixture: four jointly eligible opportunities with complete evidence, Dependabot detecting three and Renovate two. Assert exact fractions `3/4` and `2/4`, per-opportunity results, and Dependabot as subset winner. Add a fifth one-tool-excluded opportunity and an unknown lookup; neither may change the shared denominator. Verify incomplete evidence changes assessability, not silently to non-detection.

## Risks and Explicit Limits

- Native APIs are version-sensitive: gate on captured fixtures and reject unsupported formats.
- Local scanning may differ from hosted behavior: surface preset/platform/schedule limitations rather than claiming exact parity.
- Live registries are not frozen by Docker: record observation skew and withhold affected comparisons.
- Manager version schemes differ: never apply SemVer to Docker digests, commit SHAs, arbitrary tags, or unsupported ranges.
- Some scanners only expose latest actionable updates: absence of intermediate releases is not automatically a miss.
- An independent inventory is required to identify opportunities missed by both; `full` now generates a limited direct-npm inventory before scanning, with an explicit `--inventory` override and incomplete-scope warnings.
- Docker is an execution boundary, not a guarantee against malicious build code. Keep scripts disabled unless supported behavior is explicitly authorized and documented; never expose unrelated host files.

## Supporting Design Documents

- [research.md](./research.md): decisions, verified upstream capabilities, and open feasibility gates.
- [data-model.md](./data-model.md): entities, identities, matching, state and metric invariants.
- [contracts.md](./contracts.md): CLI and JSON contract design.
- [quickstart.md](./quickstart.md): intended implementation validation workflow.
# Four-configuration forecast implementation amendment

Add a dedicated forecast path without changing existing two-scan command semantics.
Implementation modules live under `src/gappy/forecast/`: versioned models, independent
config resolution, allowlisted native proposal extraction, paginated read-only GitHub
capture, pure bounded queue/demand simulation, collection, CLI/replay and Bootstrap report.

Native capability gate: controlled pinned Docker evidence proves one proposal per
native group and preserves queue demand before forecast limits. Native lookup does
not expose a reliable injected future clock or full resolved platform/PR policy.
Therefore unresolved presets, age transitions, unsupported schedules and ambiguous
PR history/matching are unknown, not silently approximated.

Baseline resolution supports certified repository JSON/YAML/config-in-package paths
and absent-only per-scanner fallback. Optimised uses complete separate configs.
Four immutable revision evaluations retain native scan contracts while new forecast
contracts record shared source, selected configuration and platform provenance.
Read-only GitHub capture is opt-in. Creation predictions remain conditional fixed-state
scenarios with no invented merges, CI results, releases or security updates.

Controlled four-way integration fixtures verify baseline two proposals versus grouped
optimised one proposal and supported capacity queuing, without modifying original source.
Replay consumes saved normalized inputs without network, Docker or credentials.
