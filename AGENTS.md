# MESA MVP — Certification Round 4 Agent Contract

## 1. Mission

The repository is now in:

MESA MVP Certification Round 4

Active branch:

mvp/certification-round-4

Round 4 is a targeted architecture migration and certification pass.

The central change is:

Mandatory Tier-3 dual-LLM validation
→
Selectable validation policy:

- Mode 0 = deterministic validation only
- Mode 1 = one LLM validator
- Mode 2 = two independent LLM validators with consensus

This round exists because the previous architecture coupled:

model-enabled runtime
=
mandatory dual-LLM Tier-3 infrastructure

That coupling is no longer the intended product contract.

Round 3 is historical evidence.

Do not reopen Round 3 as the active execution plan.

Do not append Round 4 tasks to the Round 3 ledger.

Do not start a general MESA rewrite.

Production code changes must remain the smallest coherent root-cause changes required to implement and certify the new validation-policy architecture.

---

# 2. Source of Truth

Use this evidence hierarchy:

1. current user instruction;
2. actual executable production code;
3. database schema and migration history;
4. runtime composition/configuration;
5. executable tests proving the real invariant;
6. current Round 4 `.agents/` contract;
7. audit reports, README, docs, comments and old AI output.

Previous Round 3 VERIFIED statuses are useful historical evidence.

They are not proof that Round 4 changes preserve those invariants.

---

# 3. Mandatory Reading

Before modifying production code read:

1. `AGENTS.md`
2. `.agents/00_RULES.md`
3. `.agents/01_MVP_SCOPE.md`
4. `.agents/02_TASKS.md`
5. `.agents/03_VERIFICATION.md`
6. the prompt file for the current agent

Do not use previous-round control files from Git history as the active contract.

---

# 4. Agent Roles

## Gemini

Primary implementation agent.

Gemini:

- verifies each issue against current executable code;
- implements V001-V014;
- writes bounded regression tests;
- commits coherent root-cause repairs;
- pushes the Round 4 branch.

Gemini may report:

BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV

Gemini may not declare final MVP certification.

---

## Terra

Independent reviewer and repairer.

Terra assumes Gemini may be wrong.

Terra independently verifies:

- production call paths;
- configuration;
- runtime composition;
- state-machine semantics;
- persistence;
- tests;
- failure paths.

If Terra finds a safe code-level blocker, Terra fixes it and adds regression coverage.

Terra marks independently proven tasks:

VERIFIED

Terra may create:

TERRA-V01
TERRA-V02
...

inside the same Round 4 ledger.

Terra may not issue the final MVP verdict.

---

## Sol

Final adversarial certifier and finalizer.

Sol trusts neither Gemini nor Terra.

Sol independently compares the actual branch against the frozen Round 4 contract.

Sol may create and repair:

SOL-V01
SOL-V02
...

Sol gives the final code-level verdict:

CODE_MVP_READY

or:

NOT_CODE_MVP_READY

---

# 5. Frozen Core Architecture

Round 4 does not replace the canonical MESA architecture.

The intended architecture remains:

V3 compatibility
        \
         -> Canonical MESA Core
        /
V4 native

Canonical durable truth:

SQL
mutation ledger
pipeline lifecycle
catalog lifecycle

Derived state:

vector projection
graph projection

Validation policy controls admission validation.

It does not become a second persistence engine.

---

# 6. Central Round 4 Invariant

Model processing and LLM validation are different capabilities.

The following must be valid:

MESA_MODEL_ENABLED=true
MESA_TIER3_MODE=0

This means:

- model-dependent extraction may run;
- embedding may run;
- vector retrieval may run;
- graph processing may run;
- canonical mutation lifecycle may run;
- zero validation LLMs are required.

Validation mode must not accidentally disable unrelated model capabilities.

---

# 7. Public Validation Modes

Only these explicit values are supported:

MESA_TIER3_MODE=0
MESA_TIER3_MODE=1
MESA_TIER3_MODE=2

Do not introduce a public `auto` mode in Round 4.

## Mode 0

Meaning:

deterministic validation only.

Requirements:

- zero validation LLM adapters;
- zero validation LLM calls;
- no Tier3Unavailable failure caused by an intentionally disabled validator;
- all existing deterministic admission, schema, identity, tenancy, idempotency and lifecycle checks remain active;
- projection remains fenced until the active validation policy is satisfied.

Mode 0 does NOT mean:

accept everything.

---

## Mode 1

Meaning:

one LLM validator participates in validation.

Requirements:

- one configured validation model;
- no second validator dependency;
- no fake A+A consensus;
- explicit STORE/DISCARD decision;
- infrastructure failure remains distinguishable from cognitive rejection.

---

## Mode 2

Meaning:

two independently configured LLM validators participate in the final validation decision.

Requirements:

- validator A exists;
- validator B exists;
- provider/model pairs are distinct;
- both participate;
- consensus determines STORE/DISCARD;
- disagreement fails closed according to the existing consensus contract.

Mode 2 must not silently degrade to one-model validation because an adaptive router considers one model confident.

---

# 8. Backward-Compatible Default

Do not add a user-visible `auto` value.

If `MESA_TIER3_MODE` is explicitly supplied, use exactly 0, 1 or 2.

If it is omitted:

- model-enabled full-cognitive runtime should preserve the previous dual-validation behavior;
- model-disabled runtime must not create validation adapters.

This compatibility resolution must be explicit in code and testable.

Do not let an implicit default silently weaken an existing full-cognitive deployment.

---

# 9. Validation Policy Is a First-Class Runtime Capability

Do not scatter checks such as:

if mode == 0

through unrelated production code.

Prefer one explicit validation-policy abstraction or equivalent composition boundary.

The architecture should conceptually expose:

ValidationPolicy

with implementations equivalent to:

DeterministicOnlyValidationPolicy
SingleLLMValidationPolicy
DualLLMValidationPolicy

The existing Tier3Validator may and should be reused for Mode 2 where correct.

Do not rewrite a proven dual validator merely to rename it.

---

# 10. Extraction and Validation Must Be Decoupled

Current code historically shares `llm_a` / `llm_b` between validation, routing and extraction-related components.

Round 4 must remove validation-count coupling from extraction capability.

Mode 0 must not accidentally disable LLM extraction.

Mode 1 must not imply that extraction is restricted to one model unless extraction itself requires that contract.

Mode 2 validation must not force unrelated extraction calls.

Validation adapters and extraction adapters may use the same underlying provider when intentionally configured, but their roles must be compositionally distinct.

---

# 11. Adaptive Router Contract

Validation mode is the upper policy boundary.

Adaptive routing must not increase or decrease the selected validation assurance level.

Examples:

Mode 0 + legal_domain_mode
→ still zero validation LLMs.

Mode 1 + explicit correction
→ still one validation model.

Mode 2 + confident small model
→ still dual-validator consensus.

The router may influence routing inside a mode only when doing so does not violate the mode contract.

---

# 12. Legal Domain Contract

MESA Core must not hard-code:

legal data
→ dual LLM

as a universal architectural rule.

`MESA_LEGAL_DOMAIN_MODE` may continue to control legal-specific extraction, prompts, provenance handling or conservative routing where appropriate.

It must not override:

MESA_TIER3_MODE

and silently change the number of validation LLMs.

MESA Law or an operator may choose:

MESA_TIER3_MODE=0

for deterministic trusted-source ingestion.

Source-specific legal trust policy itself is not to be hard-coded into MESA Core during Round 4.

---

# 13. Zero-Cost Contract

`MESA_ZERO_COST_MODE` must not silently mutate validation assurance level.

It may select local providers or local extraction/embedding resources.

It may not silently convert:

Mode 2
→ Mode 1

or:

Mode 1
→ Mode 0.

If the selected validation mode cannot be satisfied with the available zero-cost providers, fail closed with a truthful configuration error.

---

# 14. Durable Policy Snapshot

MESA uses asynchronous durable work.

The validation contract attached to admitted work must not silently change because the process restarts with different environment variables.

Example:

record admitted under Mode 2
→ process restart
→ runtime now configured Mode 0

The old record must not silently bypass the policy under which it was admitted.

Round 4 must persist sufficient validation-policy identity with canonical work.

At minimum preserve:

effective validation mode

and, if needed for safe evolution:

validation policy version.

The implementation may use existing canonical metadata if that is safe and immutable enough.

If schema changes are required, use a new Alembic migration.

Do not mutate released migrations.

---

# 15. State-Machine Semantics

The following concepts must be different:

SKIPPED_BY_POLICY
VALIDATED
REJECTED
UNAVAILABLE

Mode 0:

SKIPPED_BY_POLICY
→ normal canonical processing continues.

Mode 1/2 accepted:

VALIDATED
→ normal canonical processing continues.

Mode 1/2 cognitive reject:

REJECTED

Infrastructure/provider failure:

UNAVAILABLE
→ retry/deferred lifecycle where applicable.

Intentional Mode 0 must never appear as:

Tier3Unavailable.

---

# 16. Projection Safety

Projection fencing remains mandatory.

Existing:

BLOCKED_VALIDATION

may remain as a storage state if changing it would create unnecessary risk.

Its semantic meaning becomes:

blocked until the selected validation policy has completed successfully.

Mode 0 must not mean:

directly project before canonical validation/lifecycle gates.

The transition remains conceptually:

RECEIVED
→ active validation policy satisfied
→ VALIDATED or equivalent admission-complete state
→ projection PENDING
→ projection lifecycle

---

# 17. Candidate / Deferred Semantics

Hard-coded assumptions such as:

tier3_deferred=True

must be reviewed.

A canonical V4 candidate must not claim dual-LLM deferral when Mode 0 or Mode 1 is active.

Historical field names may remain temporarily for compatibility only if runtime meaning is correct and unambiguous.

Do not perform a broad rename solely for cosmetic reasons.

---

# 18. Capability Truth

Runtime capability output must describe actual composed behavior.

At minimum expose enough information to determine:

configured/effective validation mode;
validation enabled/disabled;
validator count;
validation policy name.

Example Mode 0:

mode = 0
policy = deterministic_only
llm_validation_enabled = false
validator_count = 0

Mode 1:

validator_count = 1

Mode 2:

validator_count = 2

A config variable existing is not proof that a runtime capability is active.

---

# 19. Embedding Independence

Tier-3 mode must not change embedding identity by itself.

The configured:

provider
model
version
dimension

must remain coherent across:

writer
projection
query
rebuild

Mode 0 with model processing enabled must still support normal embedding/vector retrieval.

Do not redesign the complete `MESA_MODEL_ENABLED` system in Round 4 unless a concrete blocker requires a small root-cause repair.

---

# 20. Existing Round 3 Invariants Must Survive

Round 4 must not regress:

- aggregate revision completeness;
- aggregate pipeline completeness;
- single ACTIVE document head;
- historical non-head rollback protection;
- immutable content vs manifest identity;
- tenant-wide queue accounting;
- immutable migration history;
- previous-release upgrade parity;
- multi-tenant catalog physical isolation;
- HTTP/SDK/MCP temporal parity;
- bounded long-lived runtime state;
- physical rollback compensation;
- physical purge compensation;
- restart durability;
- 0..N extraction behavior;
- rebuild parity;
- embedding identity.

Round 3 tests are regression evidence, not substitutes for Round 4 tests.

---

# 21. Migration Rules

Released Alembic migrations are immutable.

If Round 4 requires durable validation-policy fields:

create a NEW migration at current head.

Required:

fresh install
and
previous-release upgrade

must converge on the same critical schema contract.

Do not edit historical migration files to make fresh install tests pass.

---

# 22. Testing Epistemology

A test file existing is not proof.

A mock returning PASS is not proof.

The test must exercise the real invariant.

Fake providers are allowed at provider/AdapterFactory boundaries.

Do not directly mutate DAO state and then claim the full runtime validation pipeline worked.

Mode E2E tests must use the real runtime composition path wherever practical.

---

# 23. Resource Safety

Do not automatically run:

pytest -n auto
unbounded full-suite loops
large benchmarks
24h soak
paid provider benchmarks
large model downloads
Ollama model pulls
destructive migrations
huge Docker environments

Use bounded focused verification.

If the environment lacks a required dependency and safe installation is outside the current task:

BLOCKED_ENV

Do not convert environment failures into fake code PASS results.

---

# 24. Branch Policy

Active branch:

mvp/certification-round-4

Gemini creates or uses this branch.

Terra uses the SAME branch.

Sol uses the SAME branch.

Do not implement on main/master.

Do not merge to main/master.

Do not open a new Terra or Sol branch.

---

# 25. Commit Policy

Use coherent root-cause commits.

Examples:

feat(validation): add selectable validation policy

fix(runtime): decouple validation from model composition

fix(ingestion): preserve validation policy across durable work

fix(router): enforce configured validation assurance

test(validation): certify mode zero one two runtime paths

Do not create:

one giant unrelated commit

or:

meaningless micro-commits for every line.

---

# 26. Scope Discipline

Do not:

- split DAO merely because it is large;
- rewrite the application architecture;
- replace vector or graph engines;
- redesign retrieval without a Round 4 blocker;
- optimize experimental cognitive features;
- add public `auto` validation mode;
- hard-code Resmî Gazete or another legal source into MESA Core;
- convert this round into general cleanup.

Fix adjacent issues only when they directly violate the frozen Round 4 contract or create a clear code-level blocker.

---

# 27. Final Status Ownership

Gemini:

BUILT
ALREADY_FIXED_VERIFIED
BLOCKED_ENV

Terra:

VERIFIED
or creates/fixes TERRA-Vxx tasks.

Sol:

CODE_MVP_READY
or
NOT_CODE_MVP_READY

CODE_MVP_READY is code-level certification.

It is not:

MVP_FULLY_VERIFIED.

Real providers, load, soak and deployment rehearsal remain external validation gates.