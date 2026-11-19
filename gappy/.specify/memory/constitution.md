# Gappy Constitution

## Core Principles

### 1. Clear, Focused, Type-Safe Code

Code MUST be straightforward to understand and change. Implementations MUST prefer small, cohesive modules and explicit data flow over unnecessary abstraction, duplication, or hidden behavior. Python type annotations and runtime input validation MUST make important assumptions visible; unsafe casts and implicit fallback behavior MUST be avoided. Errors MUST be actionable and MUST NOT be silently swallowed or turned into success-shaped results.

### 2. Correctness Through Evidence and Explicit Contracts

Behavior MUST follow documented requirements and preserve explicit distinctions in the data. Comparisons MUST use the same repository and date window; metrics MUST expose counts and denominators; unavailable values MUST remain unavailable rather than being inferred. Different providers' records MUST remain distinct, and unresolved preset behavior MUST be identified as unresolved. Recommendations MUST cite observed data or explicit configuration and MUST state when evidence is insufficient.

### 3. Tests as Part of Every Change

Tests MUST verify observable behavior and meaningful edge cases, not implementation details alone. Changes MUST add or update targeted tests for affected behavior, including malformed and empty inputs, missing values, boundaries, and escaping where relevant. Tests MUST be deterministic, use synthetic fixtures, and MUST NOT require network access, GitHub credentials, or live repository data. A change is not complete until relevant tests and checks pass, or any failure is clearly reported.

### 4. Maintainability Through Consistency

Contributors MUST follow the existing package structure, naming, formatting, and tooling. Shared behavior SHOULD use existing helpers rather than diverging implementations. Documentation and tests MUST be updated when behavior, interfaces, or output contracts change. Changes SHOULD remain narrowly scoped, avoid unrelated cleanup, and preserve compatibility unless an intentional change is documented and tested.

### 5. Safe, Reproducible Outputs

Analysis and report generation MUST be deterministic for the same inputs. Credentials and private registry secrets MUST NOT appear in outputs, logs, or errors. Every imported string rendered in HTML MUST be escaped. The self-contained HTML report MUST NOT load remote assets or make browser-side API calls. JSON and HTML MUST describe the same repository, comparison window, findings, and limitations.

## Scope and Constraints

- `gappy` is a standalone Python CLI using Click for repository-scoped comparison of dependency patch detection by Dependabot and Renovate under their existing configurations.
- The CLI MUST provide `scan`, `compare`, `full`, and `--help`. Docker-backed scans MUST use the same immutable repository snapshot and local configuration across configured ecosystems. Default comparison MUST use native discoveries and recommendations only, without generating an inventory. Explicit legacy inventory assessment MAY remain available.
- Scans MAY access registries and required preset sources with explicitly supplied credentials. Comparison MUST remain offline and credential-free. Neither scan may create remote PRs, push commits, or alter the source checkout. Unresolved policy and unsupported scan capabilities MUST remain explicit.
- Patch detection results are analytics, not security findings, and MUST NOT be represented using security-finding severities.
- The initial scope excludes organization-wide collection or ranking, automatic polling, full remote preset-chain resolution by Gappy, GitHub status/check enrichment, scanner database dependencies, and portal routes or CI integration. Default reports MUST NOT claim to identify dependencies or updates missed by both tools. Explicit legacy inventory assessment MUST be limited to that supplied baseline.
- [`REQUIREMENTS.md`](../../REQUIREMENTS.md) defines detailed product behavior and acceptance criteria. Conflicts MUST be resolved explicitly rather than weakening correctness, privacy, or offline operation implicitly.

## Verification

- Run the smallest relevant existing test, typecheck, and lint commands for each change; run the package's complete checks when warranted by scope.
- For report or CLI changes, verify both output formats with sanitized synthetic fixtures and confirm key metrics, scan-evidence references, snapshot/window identity, and warnings.
- Adapter contract tests and Docker orchestration tests MUST use sanitized fixtures or controlled test containers. Live registry checks are optional and manual, and MUST NOT be a CI prerequisite.

## Governance

This constitution governs engineering decisions for Gappy. Amendments MUST be explicit, include a rationale, and identify affected requirements, tests, and output contracts. Detailed, non-conflicting product acceptance criteria remain governed by `REQUIREMENTS.md`.

### Amendment 1.2.0

Replace historical PR-outcome comparison with Docker-backed patch detection on a shared repository snapshot. Permit network access only during explicitly invoked scans; retain offline comparison and privacy guarantees. The requirements, feature specification, scanner adapter tests, input contracts, and report tables MUST reflect this scope. Rationale: compare dependency detection consistently without conflating it with final PR creation or merge outcomes.

### Amendment 1.3.0

Use Python and Click for the host CLI instead of Node.js/TypeScript. Preserve Docker-backed native scanners, JSON contracts, offline comparison, and all evidence/safety rules. Rationale: explicit user technology choice. Update the requirements, feature specification, implementation plan, packaging, testing guidance, and task targets together; native ecosystem labels such as npm remain unchanged.

### Amendment 1.4.0

Allow `full` to independently generate a direct-npm patch inventory from the same
frozen files and live registry metadata before scanning. Retain `--inventory` as
an override and keep comparison offline. Rationale: the user requested a complete
single-command workflow without preparing inventory manually. Generated provenance
MUST disclose incomplete scope and registry observation skew; tests and contracts
MUST verify independence from scanner detections and identical snapshot identity.

### Amendment 1.5.0

Supersede automatic inventory generation and default patch-coverage ranking with
scanner-native dependency discovery and recommendation comparison across ecosystems.
Rationale: the user explicitly requested each tool's discoveries and recommended
updates, not Gappy's reference inventory. Preserve dependencies without updates,
native version schemes, policy and failed-scan distinctions. The union cannot
establish what both missed; default reports MUST NOT imply independent coverage
or an overall winner. Retain optional supplied inventory only for compatibility.

**Version:** 1.5.0  
**Ratified:** 2026-11-11  
**Last amended:** 2026-11-11
