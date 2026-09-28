#!/bin/bash
# SearXNG Test Script for ATIS/NORA Test Server
# Validates local JSON endpoint functionality
# https://github.com/searxng/searxng

set -euo pipefail

# ============================================================================
# CONFIGURATION
# ============================================================================
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_NAME=".venv"
SEARXNG_PORT="${SEARXNG_PORT:-8888}"
BASE_URL="http://127.0.0.1:${SEARXNG_PORT}"

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
# TEST FUNCTIONS
# ============================================================================

# Test if SearXNG is running
wait_for_server() {
    local max_attempts=30
    local attempt=1
    
    log_info "Waiting for SearXNG server to start..."
    
    while [ $attempt -le $max_attempts ]; do
        if curl -s "${BASE_URL}/" > /dev/null 2>&1; then
            log_success "Server is running on port ${SEARXNG_PORT}"
            return 0
        fi
        
        log_info "Attempt ${attempt}/${max_attempts}: Server not ready yet..."
        sleep 2
        attempt=$((attempt + 1))
    done
    
    log_error "Server did not start within ${max_attempts} attempts"
    return 1
}

# Test 1: Basic connectivity
test_basic_connectivity() {
    log_info "Test 1: Basic connectivity check..."
    
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/" 2>&1)
    
    if [ "$response" = "200" ]; then
        log_success "Test 1 PASSED: HTTP 200 from root endpoint"
        return 0
    else
        log_error "Test 1 FAILED: Expected HTTP 200, got ${response}"
        return 1
    fi
}

# Test 2: JSON API with African Development Bank query
test_json_api_afdb() {
    log_info "Test 2: JSON API - African Development Bank query..."
    
    local query="African Development Bank"
    local url="${BASE_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${query}'))")&format=json"
    
    log_info "Requesting: ${url}"
    
    local response
    local http_code
    local content_type
    
    # Get response with headers
    response=$(curl -s -i "${url}" 2>&1)
    http_code=$(echo "$response" | grep -oP 'HTTP/[0-9.]+ [0-9]+' | grep -oP '[0-9]+$' | head -1)
    content_type=$(echo "$response" | grep -iP '^content-type:' | head -1 | tr -d '\r')
    
    # Extract body (everything after the first empty line)
    local body
    body=$(echo "$response" | awk 'BEGIN { found=0 } /^$/ { found=1; next } found { print }')
    
    echo "HTTP Status: ${http_code}"
    echo "Content-Type: ${content_type}"
    echo ""
    
    # Check HTTP status
    if [ "$http_code" != "200" ]; then
        log_error "Test 2 FAILED: Expected HTTP 200, got ${http_code}"
        echo "Response: ${body}"
        return 1
    fi
    
    # Check content type
    if echo "$content_type" | grep -qi "application/json"; then
        log_info "Content-Type is JSON"
    else
        log_warning "Content-Type is not JSON: ${content_type}"
    fi
    
    # Validate JSON structure
    local has_results
    local has_query
    local results_count
    local has_url
    
    has_query=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print('query' in data)" 2>/dev/null || echo "false")
    has_results=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print('results' in data and isinstance(data.get('results'), list))" 2>/dev/null || echo "false")
    results_count=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('results', [])))" 2>/dev/null || echo "0")
    has_url=$(echo "$body" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
if results:
    first = results[0]
    print('url' in first and bool(first.get('url', '').strip()))
else:
    print('false')
" 2>/dev/null || echo "false")
    
    echo "Query in response: ${has_query}"
    echo "Results field exists: ${has_results}"
    echo "Results count: ${results_count}"
    echo "First result has URL: ${has_url}"
    echo ""
    
    if [ "$has_query" = "True" ] && [ "$has_results" = "True" ] && [ "$results_count" -gt 0 ] && [ "$has_url" = "True" ]; then
        log_success "Test 2 PASSED: Valid JSON with results and URLs"
        
        # Show sample results
        echo "Sample results:"
        echo "$body" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for i, result in enumerate(data.get('results', [])[:3]):
    print(f'  {i+1}. {result.get(\"title\", \"N/A\")}')
    print(f'     URL: {result.get(\"url\", \"N/A\")}')
    print(f'     Engine: {result.get(\"engine\", \"N/A\")}')
" 2>/dev/null || echo "  Could not display sample results"
        
        return 0
    else
        log_error "Test 2 FAILED: Invalid JSON structure or missing required fields"
        echo "Full response:"
        echo "$body"
        return 1
    fi
}

# Test 3: Zimbabwe entity query
test_zimbabwe_entity() {
    log_info "Test 3: JSON API - Zimbabwe Energy Regulatory Authority query..."
    
    local query="Zimbabwe Energy Regulatory Authority"
    local url="${BASE_URL}/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${query}'))")&format=json"
    
    log_info "Requesting: ${url}"
    
    local response
    local http_code
    local body
    
    response=$(curl -s -i "${url}" 2>&1)
    http_code=$(echo "$response" | grep -oP 'HTTP/[0-9.]+ [0-9]+' | grep -oP '[0-9]+$' | head -1)
    body=$(echo "$response" | awk 'BEGIN { found=0 } /^$/ { found=1; next } found { print }')
    
    echo "HTTP Status: ${http_code}"
    echo ""
    
    if [ "$http_code" != "200" ]; then
        log_error "Test 3 FAILED: Expected HTTP 200, got ${http_code}"
        return 1
    fi
    
    local has_results
    local results_count
    local has_url
    
    has_results=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print('results' in data and isinstance(data.get('results'), list))" 2>/dev/null || echo "false")
    results_count=$(echo "$body" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data.get('results', [])))" 2>/dev/null || echo "0")
    has_url=$(echo "$body" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
if results:
    first = results[0]
    print('url' in first and bool(first.get('url', '').strip()))
else:
    print('false')
" 2>/dev/null || echo "false")
    
    echo "Results field exists: ${has_results}"
    echo "Results count: ${results_count}"
    echo "First result has URL: ${has_url}"
    echo ""
    
    if [ "$has_results" = "True" ] && [ "$results_count" -gt 0 ] && [ "$has_url" = "True" ]; then
        log_success "Test 3 PASSED: Zimbabwe entity query returned results with URLs"
        
        echo "Sample results:"
        echo "$body" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for i, result in enumerate(data.get('results', [])[:3]):
    print(f'  {i+1}. {result.get(\"title\", \"N/A\")}')
    print(f'     URL: {result.get(\"url\", \"N/A\")}')
    print(f'     Engine: {result.get(\"engine\", \"N/A\")}')
" 2>/dev/null || echo "  Could not display sample results"
        
        return 0
    else
        log_error "Test 3 FAILED: No results or missing URLs for Zimbabwe query"
        echo "Full response:"
        echo "$body"
        return 1
    fi
}

# Test 4: Engine diagnostics
test_engine_diagnostics() {
    log_info "Test 4: Engine diagnostics..."
    
    local query="test"
    local url="${BASE_URL}/search?q=${query}&format=json"
    
    local response
    local body
    
    response=$(curl -s -i "${url}" 2>&1)
    body=$(echo "$response" | awk 'BEGIN { found=0 } /^$/ { found=1; next } found { print }')
    
    echo "Checking which engines returned results..."
    
    # Extract engine information from results
    local engines_found
    engines_found=$(echo "$body" | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get('results', [])
engines = set()
for result in results:
    engine = result.get('engine', 'unknown')
    if engine and engine != 'unknown':
        engines.add(engine)
print('\\n'.join(sorted(engines)) if engines else 'None')
" 2>/dev/null || echo "None")
    
    if [ -n "$engines_found" ] && [ "$engines_found" != "None" ]; then
        log_success "Test 4 PASSED: Engines returning results:"
        echo "$engines_found" | while read -r engine; do
            log_info "  - ${engine}"
        done
    else
        log_warning "Test 4 WARNING: No engines returned results. Check engine configuration."
    fi
    
    return 0
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================
main() {
    echo ""
    echo "============================================================================"
    echo "  SearXNG Test Script for ATIS/NORA Test Server"
    echo "============================================================================"
    echo ""
    
    # Check if we can detect if SearXNG is running
    if ! wait_for_server; then
        log_error "Cannot connect to SearXNG server. Please start it first with: ./scripts/start.sh"
        exit 1
    fi
    
    echo ""
    echo "Starting tests..."
    echo ""
    
    local tests_passed=0
    local tests_failed=0
    
    # Run tests
    if test_basic_connectivity; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi
    
    echo ""
    
    if test_json_api_afdb; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi
    
    echo ""
    
    if test_zimbabwe_entity; then
        tests_passed=$((tests_passed + 1))
    else
        tests_failed=$((tests_failed + 1))
    fi
    
    echo ""
    
    test_engine_diagnostics
    
    echo ""
    echo "============================================================================"
    echo "  TEST SUMMARY"
    echo "============================================================================"
    echo "Tests passed: ${tests_passed}"
    echo "Tests failed: ${tests_failed}"
    echo ""
    
    if [ $tests_failed -eq 0 ]; then
        log_success "All tests passed!"
        echo ""
        echo "Next steps:"
        echo "1. Make the Codespace port public in GitHub Codespaces"
        echo "2. Test from an external network using:"
        echo "   curl -v \"https://<codespace-name>-8888.githubpreview.dev/search?q=African%20Development%20Bank&format=json\""
        echo ""
        exit 0
    else
        log_error "Some tests failed. Please check the output above."
        exit 1
    fi
}

# Run main function
main "$@"
