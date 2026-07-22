# Release procedure

MESA releases are created from a clean checkout and a signed, annotated tag.
The existing `v0.6.1` tag and GitHub Release are historical records and are not
rewritten by this process.

## Preconditions

- A GPG signing key trusted by the release operator is configured locally.
- The next semantic version appears in `pyproject.toml` and `CHANGELOG.md`.
- The locked graph is current: `uv lock --check` succeeds.
- CI and external release gates have passed for the release commit.

## Build and verify

```bash
uv sync --locked --extra dev
python -m pytest -q tests/test_deployment_assets.py tests/test_typing_ratchet.py
git tag -s vX.Y.Z -m "MESA vX.Y.Z"
python scripts/release_preflight.py vX.Y.Z
git push origin vX.Y.Z
```

The tag push runs the package workflow. It builds reproducible wheels,
generates a CycloneDX SBOM from the locked runtime graph, and records GitHub
OIDC build attestations for the wheel and SBOM. Download and verify those
artifacts before manually creating a GitHub Release or publishing any package.

No workflow in this repository creates or changes a GitHub Release
automatically.
