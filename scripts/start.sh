#!/bin/bash
# SearXNG Start Script for ATIS/NORA Test Server
# Native Python installation for GitHub Codespaces
# https://github.com/searxng/searxng

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_NAME=".venv"
SEARXNG_PORT="${SEARXNG_PORT:-8888}"

# ============================================================================
# COLORS FOR OUTPUT
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# LOGGING FUNCTIONS
# ============================================================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# VALIDATION
# ============================================================================
validate_environment() {
    log_info "Validating environment..."
    
    cd "${REPO_DIR}"
    
    # Check virtual environment exists
    if [ ! -d "${VENV_NAME}" ]; then
        log_error "Virtual environment not found. Please run setup.sh first."
        exit 1
    fi
    
    # Check settings file exists
    if [ ! -f "settings.yml" ]; then
        log_error "settings.yml not found. Please ensure the configuration exists."
        exit 1
    fi
    
    log_success "Environment validated"
}

# ============================================================================
# DETECT CODESPACES PORT
# ============================================================================
detect_codespaces_port() {
    # GitHub Codespaces provides a PORT environment variable
    if [ -n "${PORT:-}" ]; then
        SEARXNG_PORT="${PORT}"
        log_info "Using Codespaces PORT: ${SEARXNG_PORT}"
    else
        log_info "No Codespaces PORT detected. Using default: ${SEARXNG_PORT}"
    fi
}

# ============================================================================
# START SEARXNG
# ============================================================================
start_searxng() {
    cd "${REPO_DIR}"
    
    log_info "Starting SearXNG on port ${SEARXNG_PORT}..."
    log_info "Binding to 0.0.0.0 for external access"
    log_info "Configuration: settings.yml"
    echo ""
    
    # Activate virtual environment
    source "${VENV_NAME}/bin/activate"
    
    # Set environment variables for SearXNG
    export SEARXNG_PORT="${SEARXNG_PORT}"
    export SEARXNG_BIND_ADDRESS="0.0.0.0"
    export SEARXNG_DEBUG="false"
    export SEARXNG_SECRET="atis-nora-test-secret-key-2026"
    
    # Add current directory to Python path so it can find the searx package
    # and the version_frozen.py file
    export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"
    
    # Start SearXNG with custom settings
    # The searxng-run command will look for settings.yml in the current directory
    log_success "Starting SearXNG server..."
    echo ""
    echo "Local endpoint: http://127.0.0.1:${SEARXNG_PORT}/"
    echo "External endpoint: http://0.0.0.0:${SEARXNG_PORT}/"
    echo ""
    echo "JSON API test: http://127.0.0.1:${SEARXNG_PORT}/search?q=African%20Development%20Bank&format=json"
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo ""
    
    # Run SearXNG using the webapp module directly
    # This ensures it uses our local searx directory with version_frozen.py
    exec python -m searx.webapp run
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================
main() {
    echo ""
    echo "============================================================================"
    echo "  SearXNG Start Script for ATIS/NORA Test Server"
    echo "============================================================================"
    echo ""
    
    validate_environment
    detect_codespaces_port
    start_searxng
}

# Run main function
main "$@"
