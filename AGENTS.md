MESA MVP Certification Round 2 — Agent Operating Contract

1. Mission

The repository has already completed one implementation/review/finalization cycle. That prior cycle improved the code substantially, but subsequent regression audits exposed blind spots in both the implementation and the verification contract.

This round is a certification repair pass, not a feature expansion and not another open-ended architecture redesign.

The objective is to close the remaining code-level MVP blockers and prove the hard invariants using adversarial tests that cross the real failure boundaries.

Optimize for:

physical and logical lifecycle correctness;

one canonical mutation authority;

current-truth correctness;

embedding-space truth;

complete 0..N memory extraction;

V3 compatibility safety;

deployment/runtime contract truth;

retry/idempotency correctness;

bounded memory/resource behavior;

minimum necessary change.

2. Source of Truth

Use this hierarchy:

current user instruction;

current reachable executable source code;

schema/migrations and durable state transitions;

runtime composition and packaging;

adversarial tests that exercise the actual boundary;

.agents/ certification files;

previous task ledgers, reports, docs, comments, README and prior agent output.

Previous BUILT, VERIFIED, FINAL_VERIFIED, or CODE_MVP_READY labels are historical claims only. They are not proof in this round.

3. Mandatory Reading Order

Read before production edits:

AGENTS.md

.agents/00_RULES.md

.agents/01_MVP_SCOPE.md

.agents/02_TASKS.md

.agents/03_VERIFICATION.md

Then inspect only the source needed for the current certification task.

Do not bulk-read historical audits unless a task explicitly needs historical context.

4. Round-2 Principle: Invariant, Not Implementation Shape

A repair is complete only when the invariant is true across supported reachable paths.

Examples:

rejecting stale projection completion is not enough if a stale worker can leave a physical vector/graph side effect;

rejecting an embedding dimension mismatch is not enough if the default runtime always creates the mismatch;

preserving multiple REBEL triplets is not enough if the LLM fallback still emits exactly one triplet;

calculating a RAM budget is not enough if no production consumer uses it;

adding a bounded DAO count helper is not enough if the hot path still materializes all memories;

declaring an experimental flag is not enough if the composition root still starts the worker by default.

5. Hard Lifecycle Invariant

For rollback and purge, MESA must guarantee both:

Logical terminality

A fenced/terminal mutation cannot re-enter forward canonical state.

Physical terminality

A stale/in-flight worker cannot leave active unowned vector/graph/secondary-store data after rollback or purge.

Required shape for non-transactional secondary effects:

pre-side-effect fence -> physical write -> post-side-effect fence/receipt -> compensate immediately if fence lost -> terminal success only after ownership is safe

Reconciliation remains defense-in-depth, not the primary correctness guarantee.

6. Current-Truth Invariant

For the current non-branching MVP model:

one document has at most one ACTIVE revision/head.

Concurrent corrections from the same predecessor must not create multiple ACTIVE children.

Use a head CAS and a database safety constraint where practical.

If branching is ever wanted, it must be an explicit post-MVP branch model, not accidental multiple-ACTIVE state.

7. Embedding Invariant

The supported runtime must expose one canonical embedding provider/service contract used by the MVP write/projection/query/rebuild paths.

Identity must reflect the actual runtime:

provider;

model;

actual dimension;

embedding space/version;

normalization where relevant.

A valid default runtime must work. Invalid identity must fail closed.

Do not call a fail-closed mismatch test proof of a valid default embedding contract.

8. 0..N Extraction Invariant

Every supported extraction route must support:

one input event -> 0..N memories

This includes the LLM fallback, not only REBEL.

The fallback contract must not require exactly one triplet per record.

Tests must force REBEL unavailable/disabled/failing and prove multiple facts survive the LLM fallback path.

9. Canonical Mutation Authority

The MVP target remains:

public write -> canonical lifecycle/mutation ledger -> projection outbox -> physical projection

Default-enabled cognitive/maintenance workers must not directly mutate canonical MVP truth outside that authority.

REM, PageRank, entity rewriting/consolidation, Valence and similar cognitive/background features are default OFF for MVP unless explicitly required for the critical path.

If they are enabled later, mutating behavior should emit proposals through the canonical lifecycle rather than bypassing it.

10. V3 Compatibility Rule

V3 is compatibility, not an independent truth engine.

Prefer routing supported V3 mutations through canonical lifecycle services.

Where legacy V3 paths remain temporarily, they must have complete compensation and single-secondary-writer safety.

API-only split topology must not perform physical vector/graph mutation while a worker process is the designated storage writer.

11. Deployment Contract Is Code-Level Evidence

A documented supported runtime profile must be internally bootable from its declared package/image/config contract.

Model-enabled/full-cognitive support cannot be called code-ready if:

required adapter/ML dependencies are absent from the documented image;

provider/Tier-3 configuration is impossible or contradictory;

default embedding identity is internally inconsistent;

startup unconditionally requires a supposedly optional feature.

Long soak/load/paid-provider validation remains external, but boot/package/config coherence is a code-level gate.

12. No Self-Certification by Narrow Tests

A test written with the implementation is useful but not sufficient by itself.

For P0/P1 tasks, ask:

does this test cross the actual failure boundary?

does it force the bad interleaving/fallback/transport?

does it inspect physical state, not only ledger state?

does it validate the default supported runtime, not only rejection behavior?

does it use the real hot path, not only a helper introduced by the fix?

Terra and Sol must distrust prior tests until they answer those questions.

13. Resource Safety

Do not run automatically:

24h soak;

sustained load/capacity tests;

uncontrolled parallel pytest / pytest -n auto;

large benchmark corpora;

automatic REBEL/SentenceTransformer/CrossEncoder/Ollama downloads;

paid provider benchmarks;

giant Docker topologies;

destructive real-data migration/recovery tests.

Use faithful in-memory/fake providers for race orchestration when real provider execution is unavailable, but the fake must expose the same physical write/delete boundary being proven.

14. Git Contract

This is a new cycle after the prior closure branch was merged.

Gemini creates/uses:

mvp/certification-round-2

Terra and Sol continue on the SAME branch.

Never implement directly on main/master.

Never merge into main.

Commit coherent major repairs separately and push the same branch.

Preserve unrelated user changes.

15. Status Ownership

Gemini may use:

BUILT

ALREADY_FIXED_VERIFIED

BLOCKED_ENV

Terra may independently promote tasks to:

VERIFIED

Sol owns the final code-level certification decision:

CODE_MVP_READY

NOT_CODE_MVP_READY

MVP_FULLY_VERIFIED requires external validation evidence and must not be claimed from this coding cycle alone.

16. Completion Standard

Do not stop after closing the old report wording. Close the actual current-code invariant.

This certification round ends only when:

all Round-2 tasks are processed;

all known P0 code blockers are closed;

all MVP-relevant P1 blockers are closed or explicitly removed from the supported MVP surface;

adversarial regression coverage exists for the critical failure boundaries;

Sol makes a final decision from current code, not previous status labels.