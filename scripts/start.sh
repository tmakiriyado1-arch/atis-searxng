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
SETTINGS_FILE="${REPO_DIR}/settings.yml"

# Detect if running on Render (read-only filesystem)
ON_RENDER=false
if [ -f "/etc/render" ] || [ -d "/opt/render" ]; then
    ON_RENDER=true
fi
if [ -z "${RENDER:-}" ] && [ "$ON_RENDER" = "true" ]; then
    ON_RENDER=true
fi

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
# FOR RENDER: ENSURE SEARX IS INSTALLED
# On Render, /tmp is ephemeral, so we need to install searx to site-packages
# ============================================================================
ensure_searx_installed() {
    # On Render, check if searx is importable
    if ! python3 -c "import searx; print('searx is installed')" 2>/dev/null; then
        log_info "SearXNG not found in Python path, installing from /tmp/searxng-source..."
        
        # Set SEARXNG_SETTINGS_PATH to avoid /etc/searxng/settings.yml error during install
        export SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}"
        
        # Install searxng-source as a regular package (not editable)
        # This installs to site-packages, which is persistent on Render
        if [ -d "/tmp/searxng-source" ]; then
            if ! pip install /tmp/searxng-source 2>&1; then
                log_error "Failed to install SearXNG from /tmp/searxng-source"
                exit 1
            fi
        else
            log_error "/tmp/searxng-source not found, cannot install SearXNG"
            exit 1
        fi
        
        log_success "SearXNG installed to site-packages"
    else
        log_info "SearXNG is already installed"
    fi
}

# ============================================================================
# VALIDATION
# ============================================================================
validate_environment() {
    log_info "Validating environment..."
    
    cd "${REPO_DIR}"
    
    # On Render, ensure searx is installed first
    if [ "$ON_RENDER" = "true" ]; then
        ensure_searx_installed
        
        # Check settings file exists
        if [ ! -f "${SETTINGS_FILE}" ]; then
            log_error "settings.yml not found at ${SETTINGS_FILE}"
            exit 1
        fi
        
        # Check Python is available
        if ! command -v python3 &> /dev/null; then
            log_error "python3 not found"
            exit 1
        fi
        
        log_success "Render environment validated"
        return 0
    fi
    
    # Check virtual environment exists
    if [ ! -d "${VENV_NAME}" ]; then
        log_error "Virtual environment not found. Please run setup.sh first."
        exit 1
    fi
    
    # Check settings file exists
    if [ ! -f "${SETTINGS_FILE}" ]; then
        log_error "settings.yml not found at ${SETTINGS_FILE}"
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
# VALIDATE CONFIGURATION
# ============================================================================
validate_configuration() {
    log_info "Validating configuration from ${SETTINGS_FILE}..."
    
    cd "${REPO_DIR}"
    
    # On Render, use system python3
    if [ "$ON_RENDER" = "true" ]; then
        local validation_output
        validation_output=$(python3 << EOF
import yaml
import sys

try:
    with open('${SETTINGS_FILE}', 'r') as f:
        config = yaml.safe_load(f)
    
    if config is None:
        print("ERROR: settings.yml is empty")
        sys.exit(1)
    
    # Check search formats
    formats = config.get('search', {}).get('formats', [])
    if 'json' not in formats:
        print("ERROR: JSON format not enabled in search.formats")
        sys.exit(1)
    else:
        print(f"OK: JSON format enabled, formats = {formats}")
    
    # Check server settings
    server = config.get('server', {})
    
    bind_addr = server.get('bind_address', 'NOT SET')
    if bind_addr != '0.0.0.0':
        print(f"ERROR: bind_address is '{bind_addr}', expected '0.0.0.0'")
        sys.exit(1)
    else:
        print(f"OK: bind_address = '{bind_addr}'")
    
    port = server.get('port', 'NOT SET')
    print(f"OK: port = {port}")
    
    method = server.get('method', 'NOT SET')
    if method != 'GET':
        print(f"ERROR: method is '{method}', expected 'GET'")
        sys.exit(1)
    else:
        print(f"OK: method = '{method}'")
    
    print("\\nConfiguration validation PASSED")
    
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
EOF
        )
        
        echo "${validation_output}"
        
        if [ $? -ne 0 ]; then
            log_error "Configuration validation failed"
            exit 1
        fi
        
        log_success "Configuration validated"
        return 0
    fi
    
    # Non-Render: Activate virtual environment to use Python
    source "${VENV_NAME}/bin/activate"
    
    # Use Python to validate the YAML and check key settings
    local validation_output
    validation_output=$(python3 << EOF
import yaml
import sys

try:
    with open('${SETTINGS_FILE}', 'r') as f:
        config = yaml.safe_load(f)
    
    if config is None:
        print("ERROR: settings.yml is empty")
        sys.exit(1)
    
    # Check search formats
    formats = config.get('search', {}).get('formats', [])
    if 'json' not in formats:
        print("ERROR: JSON format not enabled in search.formats")
        sys.exit(1)
    else:
        print(f"OK: JSON format enabled, formats = {formats}")
    
    # Check server settings
    server = config.get('server', {})
    
    bind_addr = server.get('bind_address', 'NOT SET')
    if bind_addr != '0.0.0.0':
        print(f"ERROR: bind_address is '{bind_addr}', expected '0.0.0.0'")
        sys.exit(1)
    else:
        print(f"OK: bind_address = '{bind_addr}'")
    
    port = server.get('port', 'NOT SET')
    print(f"OK: port = {port}")
    
    method = server.get('method', 'NOT SET')
    if method != 'GET':
        print(f"ERROR: method is '{method}', expected 'GET'")
        sys.exit(1)
    else:
        print(f"OK: method = '{method}'")
    
    print("\\nConfiguration validation PASSED")
    
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
EOF
    )
    
    echo "${validation_output}"
    
    # Check exit code of the Python validation
    if [ $? -ne 0 ]; then
        log_error "Configuration validation failed"
        deactivate
        exit 1
    fi
    
    deactivate
    log_success "Configuration validated"
}

# ============================================================================
# VERIFY RUNTIME CONFIGURATION
# ============================================================================
verify_runtime_config() {
    log_info "Verifying runtime configuration..."
    
    cd "${REPO_DIR}"
    
    # On Render, use system Python with SEARXNG_SETTINGS_PATH
    if [ "$ON_RENDER" = "true" ]; then
        # Set environment variables
        export SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}"
        export SEARXNG_SECRET="${SEARXNG_SECRET:-$(openssl rand -hex 32)}"
        
        # Use Python to check the effective runtime configuration
        local runtime_check
        runtime_check=$(python3 << EOF
import sys
import os

# Set SEARXNG_SETTINGS_PATH before importing searx
os.environ['SEARXNG_SETTINGS_PATH'] = '${SETTINGS_FILE}'

# Initialize settings the same way the server will
from searx import settings, init_settings
init_settings()

print('Effective runtime configuration:')
print(f'  formats = {settings.get("search", {}).get("formats", [])}')
print(f'  bind_address = {settings.get("server", {}).get("bind_address", "NOT SET")}')
print(f'  port = {settings.get("server", {}).get("port", "NOT SET")}')
print(f'  method = {settings.get("server", {}).get("method", "NOT SET")}')

# Verify critical settings
formats = settings.get('search', {}).get('formats', [])
bind_addr = settings.get('server', {}).get('bind_address', '')

if 'json' not in formats:
    print("ERROR: JSON not in effective formats")
    sys.exit(1)

if bind_addr != '0.0.0.0':
    print(f"ERROR: Effective bind_address is '{bind_addr}', expected '0.0.0.0'")
    sys.exit(1)

print("\\nRuntime configuration VERIFIED")
EOF
        )
        
        echo "${runtime_check}"
        
        if [ $? -ne 0 ]; then
            log_error "Runtime configuration verification failed"
            exit 1
        fi
        
        log_success "Runtime configuration verified"
        return 0
    fi
    
    # Non-Render: Activate virtual environment
    source "${VENV_NAME}/bin/activate"
    
    # Set the same environment variables that will be used for the server
    export SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}"
    export SEARXNG_SECRET="${SEARXNG_SECRET:-$(openssl rand -hex 32)}"
    export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"
    
    # Use Python to check the effective runtime configuration
    local runtime_check
    runtime_check=$(python3 << EOF
import sys
sys.path.insert(0, '.')

# Initialize settings the same way the server will
from searx import settings, init_settings
init_settings()

print('Effective runtime configuration:')
print(f'  formats = {settings.get("search", {}).get("formats", [])}')
print(f'  bind_address = {settings.get("server", {}).get("bind_address", "NOT SET")}')
print(f'  port = {settings.get("server", {}).get("port", "NOT SET")}')
print(f'  method = {settings.get("server", {}).get("method", "NOT SET")}')

# Verify critical settings
formats = settings.get('search', {}).get('formats', [])
bind_addr = settings.get('server', {}).get('bind_address', '')

if 'json' not in formats:
    print("ERROR: JSON not in effective formats")
    sys.exit(1)

if bind_addr != '0.0.0.0':
    print(f"ERROR: Effective bind_address is '{bind_addr}', expected '0.0.0.0'")
    sys.exit(1)

print("\\nRuntime configuration VERIFIED")
EOF
    )
    
    echo "${runtime_check}"
    
    if [ $? -ne 0 ]; then
        log_error "Runtime configuration verification failed"
        deactivate
        exit 1
    fi
    
    deactivate
    log_success "Runtime configuration verified"
}

# ============================================================================
# START SEARXNG
# ============================================================================
start_searxng() {
    cd "${REPO_DIR}"
    
    log_info "Starting SearXNG on port ${SEARXNG_PORT}..."
    log_info "Using settings file: ${SETTINGS_FILE}"
    echo ""
    
    # On Render, use system Python with SEARXNG_SETTINGS_PATH
    if [ "$ON_RENDER" = "true" ]; then
        export SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}"
        export SEARXNG_PORT="${SEARXNG_PORT}"
        export SEARXNG_BIND_ADDRESS="0.0.0.0"
        export SEARXNG_DEBUG="false"
        export SEARXNG_SECRET="${SEARXNG_SECRET:-$(openssl rand -hex 32)}"
        
        log_success "Starting SearXNG server on Render..."
        echo ""
        echo "Settings file: ${SETTINGS_FILE}"
        echo "Local endpoint: http://127.0.0.1:${SEARXNG_PORT}/"
        echo "External endpoint: http://0.0.0.0:${SEARXNG_PORT}/"
        echo ""
        echo "JSON API test: http://127.0.0.1:${SEARXNG_PORT}/search?q=African%20Development%20Bank&format=json"
        echo ""
        echo "Press Ctrl+C to stop the server"
        echo ""
        
        # Run SearXNG using the webapp module
        exec python3 -m searx.webapp run
    fi
    
    # Non-Render: Activate virtual environment
    source "${VENV_NAME}/bin/activate"
    
    # CRITICAL: Set SEARXNG_SETTINGS_PATH to point to our custom settings.yml
    export SEARXNG_SETTINGS_PATH="${SETTINGS_FILE}"
    
    # Set other environment variables
    export SEARXNG_PORT="${SEARXNG_PORT}"
    export SEARXNG_BIND_ADDRESS="0.0.0.0"
    export SEARXNG_DEBUG="false"
    export SEARXNG_SECRET="${SEARXNG_SECRET:-$(openssl rand -hex 32)}"
    
    # Add current directory to Python path so it can find the searx package
    export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"
    
    log_success "Starting SearXNG server with custom configuration..."
    echo ""
    echo "Settings file: ${SETTINGS_FILE}"
    echo "Local endpoint: http://127.0.0.1:${SEARXNG_PORT}/"
    echo "External endpoint: http://0.0.0.0:${SEARXNG_PORT}/"
    echo ""
    echo "JSON API test: http://127.0.0.1:${SEARXNG_PORT}/search?q=African%20Development%20Bank&format=json"
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo ""
    
    # Run SearXNG using the webapp module directly
    exec python3 -m searx.webapp run
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
    validate_configuration
    verify_runtime_config
    start_searxng
}

# Run main function
main "$@"
