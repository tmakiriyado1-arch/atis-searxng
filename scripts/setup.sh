#!/bin/bash
# SearXNG Setup Script for ATIS/NORA Test Server
# Native Python installation for GitHub Codespaces
# https://github.com/searxng/searxng

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================
SEARXNG_REPO="https://github.com/searxng/searxng.git"
PYTHON_VERSION_REQUIRED="3.10"
VENV_NAME=".venv"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEARXNG_SOURCE_DIR="/tmp/searxng-source"

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
# PREREQUISITE CHECKS
# ============================================================================
check_python_version() {
    log_info "Checking Python version..."
    
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 is not installed. Please install Python 3.10 or higher."
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    log_info "Detected Python version: ${PYTHON_VERSION}"
    
    # Simple numeric comparison without external dependencies
    # Extract major and minor version numbers
    PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    
    if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 10 ]; then
        log_success "Python version check passed"
        return 0
    else
        log_error "Python ${PYTHON_VERSION_REQUIRED} or higher is required. Found: ${PYTHON_VERSION}"
        exit 1
    fi
}

check_pip() {
    log_info "Checking pip..."
    if ! command -v pip3 &> /dev/null; then
        log_error "pip3 is not available. Please ensure pip is installed."
        exit 1
    fi
    log_success "pip is available"
}

check_git() {
    log_info "Checking git..."
    if ! command -v git &> /dev/null; then
        log_error "git is not installed. Please install git."
        exit 1
    fi
    log_success "git is available"
}

# ============================================================================
# CLONE SEARXNG SOURCE
# ============================================================================
clone_searxng() {
    log_info "Cloning SearXNG source repository..."
    
    if [ -d "${SEARXNG_SOURCE_DIR}" ]; then
        log_info "SearXNG source already exists, skipping clone"
        return 0
    fi
    
    if ! git clone --depth 1 ${SEARXNG_REPO} ${SEARXNG_SOURCE_DIR} 2>&1; then
        log_error "Failed to clone SearXNG repository"
        exit 1
    fi
    
    log_success "SearXNG source cloned"
}

# ============================================================================
# VIRTUAL ENVIRONMENT SETUP
# ============================================================================
create_venv() {
    log_info "Creating Python virtual environment..."
    
    cd "${REPO_DIR}"
    
    if [ -d "${VENV_NAME}" ]; then
        log_warning "Virtual environment already exists. Removing and recreating..."
        rm -rf "${VENV_NAME}"
    fi
    
    # Try to create virtual environment
    # On Render, ensurepip may not be available, so we need to install it first
    if ! python3 -m venv "${VENV_NAME}" 2>/dev/null; then
        log_warning "Failed to create venv directly, trying with ensurepip..."
        # Install ensurepip if available
        if command -v apt-get &> /dev/null; then
            log_info "Installing python3-venv package..."
            # On Render, we can't use apt-get due to read-only filesystem
            # Skip apt-get and fall through to manual venv creation
            log_warning "apt-get available but filesystem may be read-only, skipping..."
        else
            log_info "No apt-get available"
        fi
        
        # Try using the system python directly without venv
        log_warning "Trying without venv (using system Python)..."
        # Create a minimal venv structure manually
        mkdir -p "${VENV_NAME}/bin"
        ln -sf "$(which python3)" "${VENV_NAME}/bin/python3" 2>/dev/null || \
            cp "$(which python3)" "${VENV_NAME}/bin/python3" 2>/dev/null || \
            echo "#<! /bin/sh\nexec $(which python3) \"$@\"" > "${VENV_NAME}/bin/python3" && \
            chmod +x "${VENV_NAME}/bin/python3"
        ln -sf "$(which python3)" "${VENV_NAME}/bin/python" 2>/dev/null || \
            cp "$(which python3)" "${VENV_NAME}/bin/python" 2>/dev/null || \
            echo "#<! /bin/sh\nexec $(which python3) \"$@\"" > "${VENV_NAME}/bin/python" && \
            chmod +x "${VENV_NAME}/bin/python"
        # Create pip wrapper
        echo "#<! /bin/sh\nexec $(which pip3) \"$@\"" > "${VENV_NAME}/bin/pip" && \
        chmod +x "${VENV_NAME}/bin/pip"
        # Create activate script
        cat > "${VENV_NAME}/bin/activate" << 'EOF'
#!/bin/bash
export VIRTUAL_ENV="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$VIRTUAL_ENV/bin:$PATH"
EOF
        chmod +x "${VENV_NAME}/bin/activate"
    fi
    
    log_success "Virtual environment created"
}

# ============================================================================
# INSTALL PACKAGES
# ============================================================================
install_packages() {
    log_info "Installing packages..."
    
    cd "${REPO_DIR}"
    
    # Activate virtual environment
    source "${VENV_NAME}/bin/activate"
    
    # Install packaging for version comparison
    log_info "Installing packaging..."
    if ! pip install packaging; then
        log_error "Failed to install packaging"
        exit 1
    fi
    
    # Install setuptools
    log_info "Installing setuptools..."
    if ! pip install setuptools; then
        log_error "Failed to install setuptools"
        exit 1
    fi
    
    # Install SearXNG dependencies from requirements.txt
    log_info "Installing SearXNG dependencies..."
    if ! pip install -r ${SEARXNG_SOURCE_DIR}/requirements.txt; then
        log_error "Failed to install SearXNG dependencies"
        deactivate
        exit 1
    fi
    
    # Install SearXNG in editable mode from source
    log_info "Installing SearXNG from source..."
    if ! pip install --no-build-isolation -e ${SEARXNG_SOURCE_DIR}; then
        log_error "Failed to install SearXNG from source"
        deactivate
        exit 1
    fi
    
    log_success "SearXNG installed successfully"
    
    deactivate
    log_success "Package installation complete"
}

# ============================================================================
# COPY SEARXNG SOURCE TO REPO
# ============================================================================
copy_searxng_source() {
    log_info "Copying SearXNG source to repository..."
    
    cd "${REPO_DIR}"
    
    # Remove existing searx directory if it exists
    if [ -d "searx" ]; then
        rm -rf "searx"
    fi
    
    # Copy searx directory from source
    if ! cp -r ${SEARXNG_SOURCE_DIR}/searx .; then
        log_error "Failed to copy SearXNG source"
        exit 1
    fi
    
    # Create version_frozen.py to avoid git dependency issues
    cat > searx/version_frozen.py << 'EOF'
# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring
# this file is generated automatically by searx/version.py

VERSION_STRING = "2026.9.25"
VERSION_TAG = "2026.9.25"
DOCKER_TAG = "2026.9.25"
GIT_URL = "https://github.com/searxng/searxng"
GIT_BRANCH = "master"
EOF
    
    log_success "SearXNG source copied and version_frozen.py created"
}

# ============================================================================
# CONFIGURATION SETUP
# ============================================================================
setup_configuration() {
    log_info "Setting up configuration..."
    
    cd "${REPO_DIR}"
    
    # Check if settings.yml exists
    if [ ! -f "settings.yml" ]; then
        log_error "settings.yml not found in repository root"
        exit 1
    fi
    
    # Validate YAML syntax
    if ! python3 -c "import yaml; yaml.safe_load(open('settings.yml'))" 2>/dev/null; then
        log_error "Invalid YAML in settings.yml"
        exit 1
    fi
    
    log_success "Configuration validated"
}

# ============================================================================
# FINAL VERIFICATION
# ============================================================================
final_verification() {
    log_info "Performing final verification..."
    
    cd "${REPO_DIR}"
    
    # Check virtual environment
    if [ ! -d "${VENV_NAME}" ]; then
        log_error "Virtual environment not found"
        exit 1
    fi
    
    # Check settings file
    if [ ! -f "settings.yml" ]; then
        log_error "settings.yml not found"
        exit 1
    fi
    
    # Check searx directory
    if [ ! -d "searx" ]; then
        log_error "searx directory not found"
        exit 1
    fi
    
    # Check scripts directory
    if [ ! -d "scripts" ]; then
        log_error "scripts directory not found"
        exit 1
    fi
    
    log_success "All components verified"
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================
main() {
    echo ""
    echo "============================================================================"
    echo "  SearXNG Setup for ATIS/NORA Test Server"
    echo "============================================================================"
    echo ""
    
    check_python_version
    check_pip
    check_git
    clone_searxng
    create_venv
    install_packages
    copy_searxng_source
    setup_configuration
    final_verification
    
    echo ""
    echo "============================================================================"
    echo "  SETUP COMPLETE"
    echo "============================================================================"
    echo ""
    echo "To start SearXNG, run:"
    echo "  ./scripts/start.sh"
    echo ""
    echo "To test the installation, run:"
    echo "  ./scripts/test.sh"
    echo ""
    echo "Repository directory: ${REPO_DIR}"
    echo ""
}

# Run main function
main "$@"
