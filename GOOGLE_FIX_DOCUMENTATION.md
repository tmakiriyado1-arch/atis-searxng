# Google Engine Fix - Permanent Solution

## Problem Summary

Google was consistently returning **HTTP 403 Forbidden** errors for all requests to the `/wml/search` endpoint, which was the primary method used by SearXNG's Google engine. This caused the engine to be suspended with the message "Suspended: access denied".

### Root Cause
1. The `/wml/search` endpoint is deprecated (designed for 2000s-era Nokia Symbian phones)
2. Google's bot detection systems now block automated requests to this endpoint
3. Even with Chrome impersonation, the WML endpoint returns 403 consistently

## Solution Implemented

### 1. Switched from WML to Standard Google Search
- Changed endpoint from `https://www.google.com/wml/search` to `https://www.google.com/search`
- Updated URL construction in `google_request()` function
- Maintained all existing parameter handling (language, region, safe search, etc.)

### 2. Enhanced Browser Fallback System
The existing `GoogleTransport` class already had browser fallback capability via Playwright. We enhanced it:

- **Automatic detection**: When HTTP requests fail with 403, CAPTCHA, or access denied, the system automatically falls back to browser-based requests
- **Force browser mode**: After repeated failures, the engine can force browser mode for all subsequent requests
- **Failure tracking**: Tracks consecutive failures and automatically switches to browser mode

### 3. Improved Error Detection
- Added detection for access denied patterns in response bodies
- Enhanced `detect_google_sorry()` to catch more bot protection scenarios
- Better classification of 403 responses (CAPTCHA vs. access denied vs. rate limiting)

### 4. Dual Parser Support
- Maintained WML parser for backward compatibility
- Added HTML parser for standard Google search results
- Automatic fallback between parsers based on response content

## Files Modified

1. **`searx/engines/google.py`**
   - Changed endpoint from `/wml/search` to `/search`
   - Added `_get_traits()` function for lazy traits initialization
   - Enhanced `detect_google_sorry()` with access denied detection
   - Added `_parse_html_results()` for standard Google HTML parsing
   - Added `_parse_wml_results()` for backward compatibility
   - Enhanced `response()` function with dual parser support
   - Added failure tracking and forced browser mode

2. **`searx/enginelib/google_transport.py`**
   - Enhanced browser fallback to trigger on 403, CAPTCHA, and rate limiting
   - Added logging for transport decisions
   - Improved error classification

## Requirements for Permanent Solution

### 1. Playwright Installation (Required)

The browser fallback requires Playwright to be installed. Without it, Google may still block requests.

```bash
# Install Playwright and browsers
pip install playwright
playwright install
```

For Docker/container environments:
```dockerfile
RUN pip install playwright
RUN playwright install chromium
```

### 2. Render-Specific Configuration

If running on Render.com, add these to your `requirements.txt`:
```
playwright>=1.40.0
```

And ensure the Render build command includes:
```bash
pip install -r requirements.txt && playwright install chromium
```

### 3. Environment Variables (Optional)

You can control the browser fallback behavior with these environment variables:

```bash
# Enable/disable browser fallback (default: True)
GOOGLE_USE_BROWSER_FALLBACK=true

# Maximum failures before forcing browser mode (default: 3)
GOOGLE_MAX_HTTP_FAILURES=3

# Browser timeout in seconds (default: 30)
GOOGLE_BROWSER_TIMEOUT=30
```

## Testing the Fix

### Manual Test

```bash
cd /workspace/github__tmakiriyado1-arch__atis-searxng

# Reset failure tracking
.venv/bin/python3 -c "from searx.engines.google import reset_browser_failure_tracking; reset_browser_failure_tracking()"

# Run a test search
curl "http://localhost:8888/search?q=test+query&engines=google"
```

### Using the Diagnostic Script

```bash
# Run the Google diagnostic
.venv/bin/python3 diagnose_google.py
```

This will show you:
- DNS resolution status
- TCP/TLS connectivity
- HTTP status codes
- Response classification
- Parser results

## Troubleshooting

### If You Still See "Suspended: access denied"

1. **Check Playwright installation**:
   ```bash
   .venv/bin/python3 -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
   ```

2. **Check browser availability**:
   ```bash
   .venv/bin/python3 -c "
   from searx.enginelib.google_transport import get_browser_transport
   transport = get_browser_transport()
   print('Browser available:', transport._ensure_browser())
   ```

3. **Check logs for transport classification**:
   Look for lines like:
   ```
   [GOOGLE_TRANSPORT] classification=captcha status=403 duration_ms=1234
   [GOOGLE_TRANSPORT] Browser fallback used, classification=browser_success
   ```

4. **Force reset the browser tracking**:
   ```bash
   .venv/bin/python3 -c "from searx.engines.google import reset_browser_failure_tracking; reset_browser_failure_tracking()"
   ```

### Common Issues

#### Issue: Playwright not installed
**Solution**: Install Playwright as shown above.

#### Issue: Browser not available in headless environment
**Solution**: Ensure you're using a compatible base image:
```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2
```

#### Issue: Google still blocking requests
**Solution**: The browser fallback should handle this. If it doesn't:
1. Check that `_force_browser_mode` is being set to `True` in the logs
2. Ensure Playwright is properly installed
3. The system will automatically retry with browser after HTTP failures

## Monitoring

Check your logs for these indicators:

### Success
```
[GOOGLE_TRANSPORT] classification=success status=200 duration_ms=2345
```

### HTTP Request Failed, Browser Fallback Used
```
[GOOGLE_TRANSPORT] classification=access_denied status=403 duration_ms=1234
[GOOGLE_TRANSPORT] Browser fallback used, classification=browser_success
```

### Forced Browser Mode Active
```
[GOOGLE] Forced browser mode active due to previous failures
```

## Performance Considerations

- **Browser requests are slower** than HTTP requests (typically 2-5 seconds vs 0.5-1 second)
- **Browser requests use more memory** (each browser instance uses ~100-200MB)
- The system will use HTTP requests when possible and only fall back to browser when needed
- After repeated failures, it will stay in browser mode to avoid wasting time on failing HTTP requests

## Rollback

If you need to rollback to the original WML-based implementation:

```bash
cd /workspace/github__tmakiriyado1-arch__atis-searxng
git checkout HEAD -- searx/engines/google.py searx/enginelib/google_transport.py
```

However, this will likely continue to fail as Google has deprecated the WML endpoint.

## Future Considerations

1. **Google API**: Consider using Google's Custom Search JSON API for more reliable results (requires API key)
2. **Request rotation**: Rotate user agents and IP addresses to avoid rate limiting
3. **Caching**: Implement result caching to reduce the number of requests to Google
4. **Alternative engines**: Consider using other search engines as backups

## Technical Details

### URL Changes
- **Before**: `https://www.google.com/wml/search?q=...&sca_esv=1&...`
- **After**: `https://www.google.com/search?q=...&sca_esv=1&...`

### Parser Changes
The new implementation tries multiple parsing strategies:
1. First attempts WML parsing (for backward compatibility)
2. Falls back to HTML parsing with multiple XPath patterns:
   - `//div[@data-hveid]` (modern Google)
   - `//div[contains(@class, 'g') and contains(@class, 'rc')]` (traditional)
   - `//div[@jscontroller]` (JavaScript-rendered)
   - `//div[@id='search']//div[@id='main']` (main container)

### Error Classification
The transport now classifies responses as:
- `success`: HTTP 200 with parseable results
- `access_denied`: HTTP 403 without CAPTCHA
- `captcha`: CAPTCHA or sorry page detected
- `rate_limited`: HTTP 429
- `browser_success`: Browser-based request succeeded
- `browser_fallback`: Browser was used as fallback

## Support

If you continue to experience issues:

1. Check the logs for `[GOOGLE_TRANSPORT]` messages
2. Run `diagnose_google.py` for detailed diagnostics
3. Ensure Playwright is installed and working
4. Verify your environment has the required dependencies for headless browsers
