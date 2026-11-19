# Design Decisions and Integration Research

**Date**: 2026-11-11  
**Scope**: Planning plus implementation evidence for the root npm baseline. Broader manager/policy feasibility remains open.

## Implementation evidence (2026-11-11)

- Dependabot CLI 1.94.0 and digest-pinned AMD64 updater/proxy images ran through Docker emulation on macOS ARM64. Renovate 44.14.1 used its digest-pinned ARM64 image.
- Both native tools identified synthetic `gappy-fixture` 1.2.0 -> 1.2.2 and emitted distinct successful no-update evidence when installed at 1.2.2. Sanitized captures are committed as adapter regression fixtures.
- The controlled HTTPS registry uses an explicitly trusted fixture CA. The initial HTTP probe exposed Dependabot upgrading the registry request to HTTPS; the retry used proper TLS rather than disabling verification.
- An opt-in integration test now exercises immutable source collection, both adapters, compatible exports, and offline JSON/HTML reports. Expected outcome: both detect two inventory opportunities, denominator 2, tie.
- Groups, ignores, cooldowns, other managers, private credentials, and exact inherited-policy eligibility are not verified. These remain unsupported/unknown, so the portal's full Dependabot configuration cannot yet be compared.
- The implementation deliberately restricts version parsing to validated stable numeric SemVer triples; no PEP 440 conversion or general version coercion is used. The unused `semantic-version` dependency was removed.
- Shared adapter parsing is in `src/gappy/scan/native.py`; comparison arithmetic is in `src/gappy/compare/assess.py`. This consolidates the originally proposed smaller files without adding abstraction.

## Decisions

### Separate collection from comparison

Use `scan` for explicitly network-enabled Docker orchestration, then `compare` for deterministic local analysis. This preserves reuse of exports and prevents reporting from acquiring credentials or invoking Docker. A single always-online comparison command would obscure this boundary.

### Adapt native evidence, not PR history

Dependabot and Renovate do not expose an identical scan JSON API. Use concrete, version-specific adapters producing one validated schema. Do not scrape human-readable summary tables or derive detection from configuration alone.

Upstream documentation reviewed during specification:

- [Dependabot CLI README](https://github.com/dependabot/cli/blob/main/README.md): Docker-backed update jobs do not themselves create PRs; output records API calls as YAML. Job-description support and configuration-to-job equivalence require a prototype.
- [Renovate local platform](https://docs.renovatebot.com/modules/platform/local/): experimental local mode defaults to lookup dry run, has no branch creation, and cannot resolve `local>` presets. Exact evidence fields and policy behavior must be checked against the selected image.

These are capabilities described by upstream, not acceptance evidence for Gappy. Exact commands, native schema fixtures, and digests belong to the Phase 0 implementation record.

### Use an independent inventory

Superseded for the default workflow by explicit user direction: compare the native
discovery population and recommended targets across ecosystems without generating
an inventory. Independent patch assessment remains an opt-in compatibility mode.
Real policy probes also established that Renovate's package-file lookup log includes
update-type-disabled candidates; actionable recommendations must be matched against
its post-policy structured `branches[].upgrades` trace. Recent releases expose
`pendingChecks`, while Dependabot cooldown can suppress proposals without a
per-dependency reason: retain unknown rather than inventing a miss.

The inventory is the reference population; scanner outputs supply observations. The union of the scanners' updates cannot expose a patch both missed. Keep inventory provenance and completeness in both reports. A later user amendment adds independent direct-npm inventory generation in `full` from frozen manifests/lockfiles and registry release metadata, retaining supplied inventories and offline comparison.

### Keep policy and observed lookup separate

Model eligible, excluded, deferred, and unknown policy states. Observed candidates excluded or delayed by policy stay visible but are not counted as actionable detections. Preserve policy reasons; unsupported rules make affected conclusions unknown.

For an overall performance comparison, use jointly eligible, assessable opportunities. Keep row-level sole-detector wins visible even when the other tool excluded the patch.

### Start with stable SemVer patch matching

Use supported stable SemVer lines for the first npm prototype. Later `1.2.x` patches may cover earlier available `1.2.x` candidates, but `1.3.x`, prereleases, digest updates, and arbitrary tags do not inherit that rule. Extend version schemes only with manager-specific evidence/tests, not coercion.

### Use Python and Click with focused supporting libraries

Use Click for command groups, help, option handling, and CLI tests. Use Python standard-library subprocess/filesystem/hash/HTML facilities, Pydantic v2 strict models, PyYAML safe loading, and `semantic-version` for native SemVer rather than PEP 440 semantics. Use pytest, mypy, and Ruff, with `pyproject.toml`, Hatchling, and a committed uv lockfile for packaging and reproducible development. Python 3.12+ is the planned baseline. This replaces the earlier Node.js/TypeScript host design; native Docker scanners and npm test ecosystems remain unchanged. Do not introduce a database, web server, UI framework, queue, or plugin infrastructure.

## Open Feasibility Gates

| Gate | Required proof | If unavailable |
|---|---|---|
| Dependabot policy translation | Existing settings represented in native jobs, with documented supported/unsupported rules | Affected manager/opportunities unknown; no silent drop |
| Native extraction/candidate completeness | Distinguish extraction failure, no updates, filtered candidate, latest-only output | No negative claims outside proven coverage |
| Renovate shared preset support | Resolved policy/provenance for the pinned local integration | Inherited eligibility unknown; no config simulation |
| No remote mutations | Lookup/job execution cannot write to hosted repositories | Do not launch unsupported mode |
| Host/architecture support | Launcher and digest-pinned images work on intended macOS/Linux architectures | Explicit unsupported-platform error |
| Secrets and package-manager execution | Sanitized allowlisted events and narrowly scoped execution/credentials | Block unsafe mode |

No additional user policy decisions are required for planning. Any integration that requires changing repository policy, enabling scripts, broad host access, or remote writes requires explicit approval before implementation proceeds with that behavior.
