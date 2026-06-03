#!/usr/bin/env bash
# =============================================================================
# MESA v0.5.1 — Release Pipeline Script
# Phase 4.2: Build, publish, tag, and push.
#
# Prerequisites:
#   - PyPI credentials injected via environment:
#       TWINE_USERNAME / TWINE_PASSWORD   or   TWINE_API_KEY
#   - `build` and `twine` packages installed in the active venv
#   - `gh` CLI authenticated (for GitHub Release creation)
#
# Usage:
#   chmod +x scripts/release_v0.5.1.sh
#   ./scripts/release_v0.5.1.sh
# =============================================================================
set -euo pipefail

TAG="v0.5.1"
MSG="MESA v0.5.1: Retrieval pipeline stabilisation, 87%+ coverage, production soak hardening"

echo "==========================================="
echo "  MESA v0.5.1 — Release Pipeline"
echo "==========================================="

# ------------------------------------------------------------------
# Step 1: Pre-push validation (tests + static analysis)
# ------------------------------------------------------------------
echo ""
echo "[1/6] Running pre-push validation..."

if [[ ! -x "./pre_push.sh" ]]; then
    echo "ERROR: ./pre_push.sh not found or not executable." >&2
    exit 1
fi

./pre_push.sh

echo "  ✓ Pre-push checks passed."

# ------------------------------------------------------------------
# Step 2: Clean previous build artifacts
# ------------------------------------------------------------------
echo ""
echo "[2/6] Cleaning previous build artifacts..."

rm -rf dist/ build/ *.egg-info
echo "  ✓ Build directory cleaned."

# ------------------------------------------------------------------
# Step 3: Build the package
# ------------------------------------------------------------------
echo ""
echo "[3/6] Building source and wheel distributions..."

python -m build

echo "  ✓ Package built successfully."
ls -lh dist/

# ------------------------------------------------------------------
# Step 4: Upload to PyPI
# ------------------------------------------------------------------
echo ""
echo "[4/6] Uploading to PyPI via twine..."

twine upload dist/*

echo "  ✓ Package uploaded to PyPI."

# ------------------------------------------------------------------
# Step 5: Create annotated Git tag
# ------------------------------------------------------------------
echo ""
echo "[5/6] Creating annotated tag ${TAG}..."

git tag -a "${TAG}" -m "${MSG}"

echo "  ✓ Tag ${TAG} created."

# ------------------------------------------------------------------
# Step 6: Push tag to origin
# ------------------------------------------------------------------
echo ""
echo "[6/6] Pushing tag ${TAG} to origin..."

git push origin "${TAG}"

echo ""
echo "==========================================="
echo "  ✓ MESA ${TAG} released successfully"
echo "==========================================="
