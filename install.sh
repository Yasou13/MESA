#!/bin/bash
# ==============================================================================
# MESA — Single-Command Bootstrap Script
# ==============================================================================
# Usage:  chmod +x install.sh && ./install.sh
#
# What it does:
#   1. Detects OS (Linux / macOS / WSL)
#   2. Installs system-level build dependencies
#   3. Installs Ollama for zero-cost local LLM inference
#   4. Creates a Python venv and installs all pip packages
#   5. Generates .env from .env.example (if absent)
#   6. Runs a smoke test to verify KuzuDB + LanceDB imports
# ==============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
ok()    { echo -e "${GREEN}[  OK]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

echo ""
echo "🚀  Bootstrapping MESA..."
echo ""

# ── Step 1: OS detection ─────────────────────────────────────────────────────
OS="$(uname -s)"
IS_WSL=false
if [ "$OS" = "Linux" ] && grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
fi

if [ "$IS_WSL" = true ]; then
    info "Detected OS: Linux (WSL)"
elif [ "$OS" = "Linux" ]; then
    info "Detected OS: Linux"
elif [ "$OS" = "Darwin" ]; then
    info "Detected OS: macOS"
else
    fail "Unsupported OS: $OS. MESA requires Linux, macOS, or WSL."
fi

# ── Step 2: System dependencies ──────────────────────────────────────────────
info "Installing system dependencies..."
if [ "$OS" = "Linux" ]; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq build-essential python3-dev libssl-dev python3-venv curl
    ok "Linux system dependencies installed."
elif [ "$OS" = "Darwin" ]; then
    if ! command -v brew &> /dev/null; then
        fail "Homebrew is required on macOS. Install from https://brew.sh"
    fi
    brew install python@3.10
    ok "macOS system dependencies installed."
fi

# ── Step 3: Ollama (zero-cost local LLM) ─────────────────────────────────────
info "Setting up Ollama for zero-cost local inference..."
if ! command -v ollama &> /dev/null; then
    info "Ollama not found. Installing..."
    curl -fsSL https://ollama.com/install.sh | sh
    ok "Ollama installed."
else
    ok "Ollama already installed."
fi

# Start Ollama server in the background (idempotent — skips if already running)
if ! pgrep -x "ollama" > /dev/null 2>&1; then
    info "Starting Ollama server..."
    ollama serve &
    sleep 3
    ok "Ollama server started."
else
    ok "Ollama server already running."
fi

# Pull the lightweight dev model
info "Pulling llama3.2:3b model (lightweight, ~2 GB)..."
ollama pull llama3.2:3b && ok "llama3.2:3b model ready." || warn "Model pull failed. You can retry with: ollama pull llama3.2:3b"

# ── Step 4: Python virtual environment ───────────────────────────────────────
info "Creating Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
ok "Virtual environment created and activated."

info "Upgrading pip..."
pip install --upgrade pip -q

info "Installing core dependencies..."
pip install -r requirements-core.txt -q || fail "Failed to install requirements-core.txt"
ok "Core dependencies installed."

if [ -f "requirements-ml.txt" ]; then
    info "Installing ML dependencies (optional)..."
    pip install -r requirements-ml.txt -q || warn "ML dependencies partially failed. Non-critical."
    ok "ML dependencies installed."
fi

# ── Step 5: Dynamic .env generation ──────────────────────────────────────────
info "Setting up environment variables..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env

        # Generate a cryptographically secure API key
        GENERATED_KEY="mesa-$(openssl rand -hex 16)"
        if [ "$OS" = "Darwin" ]; then
            sed -i '' "s/your_production_api_key_here/${GENERATED_KEY}/" .env
        else
            sed -i "s/your_production_api_key_here/${GENERATED_KEY}/" .env
        fi

        echo ""
        warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        warn "  MESA_API_KEY has been auto-generated. Store it securely."
        warn "  Key: ${GENERATED_KEY}"
        warn "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
    else
        warn ".env.example not found. Please create .env manually."
    fi
else
    ok ".env already exists. Skipping generation."
fi

# ── Step 6: Smoke test ───────────────────────────────────────────────────────
info "Running import smoke test..."
if python -c "import kuzu; import lancedb; print('imports OK')" 2>/dev/null; then
    ok "Smoke test passed: kuzu + lancedb imports verified."
else
    fail "Smoke test FAILED. 'import kuzu' or 'import lancedb' raised an error."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🎉  MESA bootstrap complete!"
echo ""
echo "  Activate env:   source .venv/bin/activate"
echo "  Start server:   make dev"
echo "  Run tests:      make test"
echo "  Health check:   make health"
echo "  Zero-cost mode: MESA_ZERO_COST_MODE=true make dev"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
