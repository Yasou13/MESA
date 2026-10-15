# MESA Core v0.8.0 release notes

**Release date: 2026-10-15. Package version: 0.8.0.**

The repository owner explicitly approved publication after reviewing the missing
certification evidence. This source release does not certify production readiness.
Actual publication and artifact status are recorded in GitHub Releases and the
release audit; a version bump alone is not publication proof.

## Verification and known limitations

- The preceding documentation PR passed both MESA CI and external release gates;
  the version/release commit is checked independently before tagging.
- Real model/provider boot/restart/token-cost evidence, a production-like 24-hour
  soak and new official Profile B certification are not supplied by this release.
- Full per-method docstring certification and the pre-existing full-tree Black
  formatting debt remain incomplete; required scoped CI checks are preserved.
- No local GPG signing key is available: the annotated v0.8.0 tag is unsigned.
  Signed-tag preflight success is not claimed. Build/SBOM/OIDC provenance status
  is verified separately and must not be inferred from publication alone.
- Production status remains **NO-GO** until the normal certification gates pass.

## Changes

The development history since v0.7.1 contains selectable validation, canonical
embedding identity, durable offline projection rebuild, immutable revision
activation/ownership recovery, four-lane scoped retrieval, graph runtime closure,
atomic evidence packing, bounded opt-in query planning and typed legal identity.
The current documentation refresh aligns entry points, API examples, provider
defaults, configuration, ecosystem boundaries and limitations; removes unsourced
showcase metrics/PASS output; and adds scoped neutral V3 and provider-required V4 examples.

Independent review corrected conservative offline tokenizer counting, low-level
vector versus canonical authorization, V3 router descriptions and smoke checks
under Python optimization. Dashboard build dependency lock patches address two
detected HIGH advisories; audit/build proof remains separate from production gates.

See [CHANGELOG](../CHANGELOG.md) v0.8.0 for the detailed implementation
history, [migration](migration-v0.8.0.md) for forward schema/representation
requirements, and [release policy](release.md) for immutable signed tagging,
same-commit CI, provider, backup/rebuild and 24-hour soak requirements.

No new model accuracy, legal correctness, official Profile B PASS, native
MESA_Data delivery, production-size memory bound or 24-hour soak result is
claimed. Publication proof and exact test results belong to the release audit run
under `reports/v0.8.0-documentation-release/`; an absent tag/release is not success.
