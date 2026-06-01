#!/usr/bin/env bash
# =============================================================================
# MESA v0.4.2 — Release Script
# Phase 4D: Tag, push, and create GitHub Release
# =============================================================================
set -euo pipefail

TAG="v0.4.2"
MSG="MESA v0.4.2: Enterprise standards, 85% coverage barrier restored, missing tests"

echo "=========================================="
echo "  MESA v0.4.2 — Release Pipeline"
echo "=========================================="

# ------------------------------------------------------------------
# Step 1: Stage and commit all v0.4.1 changes
# ------------------------------------------------------------------
echo ""
echo "[1/5] Staging all v0.4.2 changes..."

git add \
  .github/workflows/python-app.yml \
  mesa_api/router.py \
  mesa_evals/legal_generator.py \
  mesa_evals/recall_harness.py \
  mesa_evals/soak_test.py \
  tests/test_chaos.py \
  tests/test_rbac_leak.py

git commit -m "feat(v0.4.2): ${MSG}

- Zero-Trust RBAC isolation tests (test_rbac_leak.py)
- Chaos Saga rollback resilience tests (test_chaos.py)
- Proxy Context Precision and Answer Relevance metrics
- 12-hour soak test with telemetry collection (soak_test.py)
- CI/CD security-and-audit pipeline with 3 merge gates
- Adversarial golden dataset (70/15/15 distribution)
- Status-based polling and TCPConnector socket management"

# ------------------------------------------------------------------
# Step 2: Create annotated Git tag
# ------------------------------------------------------------------
echo ""
echo "[2/5] Creating annotated tag ${TAG}..."

git tag -a "${TAG}" -m "${MSG}"

# ------------------------------------------------------------------
# Step 3: Push commits and tag to origin
# ------------------------------------------------------------------
echo ""
echo "[3/5] Pushing commits to origin/main..."
git push origin main

echo ""
echo "[4/5] Pushing tag ${TAG} to origin..."
git push origin "${TAG}"

# ------------------------------------------------------------------
# Step 4: Create GitHub Release via gh CLI
# ------------------------------------------------------------------
echo ""
echo "[5/5] Creating GitHub Release from CHANGELOG.md..."

gh release create "${TAG}" \
  --title "${TAG}" \
  --notes-file CHANGELOG.md \
  --latest

echo ""
echo "=========================================="
echo "  ✓ MESA ${TAG} released successfully"
echo "=========================================="
