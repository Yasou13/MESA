#!/usr/bin/env bash
# =============================================================================
# MESA v0.5.2 — Release Pipeline Script
# Phase 4.2: Build, publish, tag, and push.
#
# Prerequisites:
#   - PyPI credentials injected via environment:
#       TWINE_USERNAME / TWINE_PASSWORD   or   TWINE_API_KEY
#   - `build` and `twine` packages installed in the active venv
#   - `gh` CLI authenticated (for GitHub Release creation)
#
# Usage:
#   chmod +x scripts/release_v0.5.2.sh
#   ./scripts/release_v0.5.2.sh
# =============================================================================
set -euo pipefail

TAG="v0.5.2"
MSG="MESA v0.5.2: Production memory engine release"

echo "==========================================="
echo "  MESA v0.5.2 — Release Pipeline"
echo "==========================================="

echo ""
echo "[1/6] Running full test suite..."
export PYTHONPATH=.
pytest tests/ -x
echo "  ✓ Test suite passed 100%."

echo ""
echo "[2/6] Cleaning and Building package..."
rm -rf dist/ build/ *.egg-info
python -m build
echo "  ✓ Package built successfully."

echo ""
echo "[3/6] Checking package with twine..."
twine check dist/*
echo "  ✓ Twine check passed."

echo ""
echo "[4/6] Uploading to PyPI via twine..."
twine upload dist/*
echo "  ✓ Package uploaded to PyPI."

echo ""
echo "[5/6] Creating GitHub release..."
# Assumes gh cli is installed and authenticated
gh release create ${TAG} -F CHANGELOG.md -t "MESA ${TAG}"
echo "  ✓ GitHub release created."

echo ""
echo "[6/6] Tagging and pushing..."
git tag -a "${TAG}" -m "${MSG}" || true
git push origin --tags
echo "  ✓ Tag ${TAG} pushed to origin."

echo ""
echo "==========================================="
echo "  ✓ MESA ${TAG} released successfully"
echo "==========================================="
