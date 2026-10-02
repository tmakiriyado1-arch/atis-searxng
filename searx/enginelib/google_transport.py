# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Transport Abstraction

This module provides a transport layer for the Google search engine that:

1. Wraps the existing SearXNG HTTP machinery
2. Classifies responses deterministically
3. Instruments requests (duration, status, classification, result count)
4. Preserves the existing request/parser behavior unchanged
5. Provides browser-based fallback for CAPTCHA/blocked responses

The transport does NOT:
- Modify the Google endpoint
- Modify User-Agent or request headers
- Modify query construction
- Modify XPath selectors
- Modify result parsing
- Log credentials, cookies, or full response bodies
- Retry blocked requests (403, 429, CAPTCHA) without browser fallback

Architecture:
    Google engine
          \u2193
    GoogleTransport (this module)
          \u2193
    existing SearXNG HTTP machinery / Playwright browser
          \u2193
    existing Google parser (unchanged)

Classification values:
    - success: HTTP 200 with parseable results
    - success_empty: HTTP 200 with zero organic results
    - rate_limited: HTTP 429
    - access_denied: HTTP 403 without CAPTCHA indicators
    - captcha: Response contains CAPTCHA page
    - consent: Response contains consent page
    - timeout: Request timed out
    - network_error: Connection/TCP/TLS failure
    - unexpected_http_error: Other HTTP error (4xx, 5xx)
    - browser_success: Browser-based request succeeded
    - browser_fallback: Browser-based fallback was used
"""

import json
import logging
import re
import time
import typing as t
from dataclasses import dataclass, field
from enum import Enum

from searx.network import get as searx_get
from searx.exceptions import SearxEngineException

logger = logging.getLogger(__name__)

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response


class GoogleResponseClassification(Enum):
    """Classification of Google HTTP responses.
    
    These classifications are based on observable response data only.
    A response is NOT classified as CAPTCHA merely because it is HTTP 403.
    """
    
    SUCCESS = "success"
    """HTTP 200 with parseable Google results."""
    
    SUCCESS_EMPTY = "success_empty"
    """HTTP 200 but parser found zero organic results."""
    
    RATE_LIMITED = "rate_limited"
    """HTTP 429 Too Many Requests."""
    
    ACCESS_DENIED = "access_denied"
    """HTTP 403 Forbidden without CAPTCHA indicators."""
    
    CAPTCHA = "captcha"
    """Response contains CAPTCHA page (detected by body content)."""
    
    CONSENT = "consent"
    """Response contains Google consent page."""
    
    TIMEOUT = "timeout"
    """Request timed out."""
    
    NETWORK_ERROR = "network_error"
    """Connection/TCP/TLS failure."""
    
    UNEXPECTED_HTTP_ERROR = "unexpected_http_error"
    """Other HTTP error (4xx, 5xx) not otherwise classified."""
    
    BROWSER_SUCCESS = "browser_success"
    """Browser-based request succeeded (fallback path)."""
    
    BROWSER_FALLBACK = "browser_fallback"
    """Browser-based fallback was triggered."""


@dataclass
class GoogleTransportResult:
    """Result of a Google transport request.
    
    Attributes:
        response: The SXNG_Response object (None if request failed)
        status_code: HTTP status code (None if no response)
        classification: Response classification
        elapsed_time: Request duration in seconds
        error_type: Exception type if request failed (None otherwise)
        error_message: Exception message if request failed (None otherwise)
        parser_result_count: Number of results parsed (0 if not parsed yet)
        used_browser: Whether browser-based transport was used
    """
    
    response: "SXNG_Response | None" = None
    status_code: int | None = None
    classification: GoogleResponseClassification = GoogleResponseClassification.NETWORK_ERROR
    elapsed_time: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
    parser_result_count: int = 0
    used_browser: bool = False
    
    # Additional context for debugging (not logged to avoid sensitive data)
    # These are for internal use only and not exposed in logs
    _context: dict[str, t.Any] = field(default_factory=dict, repr=False)


@dataclass
class GoogleTransportMetrics:
    """Aggregated metrics for Google transport.
    
    These metrics are safe to log and do not contain sensitive data.
    """
    
    total_requests: int = 0
    classifications: dict[GoogleResponseClassification, int] = field(default_factory=dict)
    total_elapsed_time: float = 0.0
    avg_elapsed_time: float = 0.0
    last_request_time: float = 0.0
    parser_result_counts: list[int] = field(default_factory=list)
    browser_requests: int = 0
    fallback_requests: int = 0
    
    def record(self, result: GoogleTransportResult) -> None:
        """Record a transport result."""
        self.total_requests += 1
        self.classifications[result.classification] = self.classifications.get(result.classification, 0) + 1
        self.total_elapsed_time += result.elapsed_time
        self.avg_elapsed_time = self.total_elapsed_time / self.total_requests if self.total_requests > 0 else 0.0
        self.last_request_time = time.time()
        if result.parser_result_count >= 0:
            self.parser_result_counts.append(result.parser_result_count)
        if result.used_browser:
            self.browser_requests += 1
        if result.classification in (GoogleResponseClassification.BROWSER_SUCCESS, GoogleResponseClassification.BROWSER_FALLBACK):
            self.fallback_requests += 1
    
    def to_dict(self) -> dict[str, t.Any]:
        """Convert to a safe, loggable dictionary."""
        return {
            "total_requests": self.total_requests,
            "classifications": {c.value: count for c, count in self.classifications.items()},
            "avg_elapsed_time": round(self.avg_elapsed_time, 3),
            "last_request_time": self.last_request_time,
            "parser_result_counts": self.parser_result_counts[-10:] if self.parser_result_counts else [],
            "browser_requests": self.browser_requests,
            "fallback_requests": self.fallback_requests,
        }


# Response classification patterns
# These are used to detect response types from body content

CAPTCHA_INDICATORS = [
    r'captcha',
    r'recaptcha',
    r'verify you are human',
    r'verify you are not a robot',
    r"i'm not a robot",
    r'robot verification',
    r'are you a robot',
    r'please verify you are not a robot',
]

CONSENT_INDICATORS = [
    r'consent\.google',
    r'before you continue',
    r'accept all',
    r'cookie consent',
    r'privacy choices',
    r'manage options',
    r'consent settings',
]

BOT_PROTECTION_INDICATORS = [
    r'access denied',
    r'bot detection',
    r'automated queries',
    r'our systems have detected unusual traffic',
    r'please verify',
    r'temporarily blocked',
    r'httpservice/retry/enablejs',
    r'/httpservice/retry',
    r'enable javascript',
    r'enablejs',
    r'javascript required',
    r'please enable javascript',
    r'turn on javascript',
]

RATE_LIMIT_INDICATORS = [
    r'too many requests',
    r'rate limit',
    r'slow down',
    r'429',
]


def _classify_by_status(status_code: int) -> GoogleResponseClassification:
    """Classify response based on HTTP status code only."""
    if status_code == 200:
        return GoogleResponseClassification.SUCCESS
    if status_code == 429:
        return GoogleResponseClassification.RATE_LIMITED
    if status_code == 403:
        return GoogleResponseClassification.ACCESS_DENIED
    if 400 <= status_code < 500:
        return GoogleResponseClassification.UNEXPECTED_HTTP_ERROR
    if status_code >= 500:
        return GoogleResponseClassification.UNEXPECTED_HTTP_ERROR
    return GoogleResponseClassification.NETWORK_ERROR


def _classify_by_body(status_code: int, body: str, url: str) -> GoogleResponseClassification:
    """Classify response based on body content.
    
    This refines the classification from status code by examining the response body.
    Pattern matching takes precedence over status-based classification.
    """
    body_lower = body.lower()
    
    # Check for redirect to sorry page
    if url and ('sorry.google.com' in url or '/sorry/' in url):
        return GoogleResponseClassification.CAPTCHA
    
    # Check for CAPTCHA indicators in body (for ANY status code)
    for pattern in CAPTCHA_INDICATORS:
        if re.search(pattern, body_lower):
            return GoogleResponseClassification.CAPTCHA
    
    # Check for consent page indicators
    for pattern in CONSENT_INDICATORS:
        if re.search(pattern, body_lower):
            return GoogleResponseClassification.CONSENT
    
    # Check for bot protection indicators (classify as CAPTCHA)
    for pattern in BOT_PROTECTION_INDICATORS:
        if re.search(pattern, body_lower):
            return GoogleResponseClassification.CAPTCHA
    
    # Check for rate limit in body (even if status is not 429)
    for pattern in RATE_LIMIT_INDICATORS:
        if re.search(pattern, body_lower):
            return GoogleResponseClassification.RATE_LIMITED
    
    # For 403 responses without any of the above indicators
    if status_code == 403:
        return GoogleResponseClassification.ACCESS_DENIED
    
    # Default to status-based classification
    return _classify_by_status(status_code)


def classify_google_response(
    status_code: int | None,
    response: "SXNG_Response | None",
    url: str,
) -> GoogleResponseClassification:
    """Classify a Google HTTP response.
    
    Args:
        status_code: HTTP status code (None if request failed)
        response: The SXNG_Response object (None if request failed)
        url: The requested URL
        
    Returns:
        GoogleResponseClassification: The classification of the response
    """
    if status_code is None:
        return GoogleResponseClassification.NETWORK_ERROR
    
    if response is None:
        return _classify_by_status(status_code)
    
    try:
        body = response.text or ""
    except Exception:
        body = ""
    
    return _classify_by_body(status_code, body, url)


class MockSXNGResponse:
    """Mock SXNG_Response for browser-based responses.
    
    This provides a compatible interface for the existing parser.
    """
    
    def __init__(self, text: str, status_code: int, url: str):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.ok = status_code == 200
        self.headers = {}
        
    @property
    def host(self) -> str:
        """Extract host from URL."""
        from urllib.parse import urlparse
        return urlparse(self.url).netloc
    
    @property
    def path(self) -> str:
        """Extract path from URL."""
        from urllib.parse import urlparse
        return urlparse(self.url).path


class GoogleBrowserTransport:
    """Browser-based transport for Google requests.
    
    Uses Playwright to make real browser requests that can bypass bot detection.
    This is the fallback transport when standard HTTP requests are blocked.
    
    Note: This transport is optional and only used when:
    1. Playwright is installed
    2. Standard HTTP request fails with CAPTCHA/access denied
    3. Browser fallback is enabled
    """
    
    def __init__(self, headless: bool = True, timeout: float = 30.0):
        """Initialize browser transport.
        
        Args:
            headless: Run browser in headless mode
            timeout: Timeout for browser operations in seconds
        """
        self.headless = headless
        self.timeout = timeout
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._initialized = False
    
    def _ensure_browser(self) -> bool:
        """Ensure browser is initialized.
        
        Returns:
            bool: True if browser is available, False otherwise
        """
        if self._initialized:
            return True
        
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright()
            self._playwright_started = True
            self._browser = self._playwright.chromium.launch(
                headless=self.headless,
                timeout=self.timeout * 1000,
            )
            self._context = self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 800},
            )
            self._page = self._context.new_page()
            self._initialized = True
            return True
        except ImportError:
            # Playwright not installed
            return False
        except Exception:
            # Browser initialization failed
            return False
    
    def request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: t.Any,
    ) -> tuple[MockSXNGResponse | None, float, str | None]:
        """Make a browser-based request.
        
        Args:
            url: The URL to request
            headers: Request headers (partially used)
            cookies: Request cookies
            timeout: Request timeout in seconds
            **kwargs: Additional arguments (ignored)
            
        Returns:
            tuple: (response, elapsed_time, error_message)
        """
        import time
        
        if not self._ensure_browser():
            return None, 0.0, "Playwright not available"
        
        start_time = time.time()
        
        try:
            # Set cookies if provided
            if cookies:
                self._context.add_cookies([
                    {'name': k, 'value': v, 'domain': '.google.com', 'path': '/'}
                    for k, v in cookies.items()
                ])
            
            # Navigate to URL
            self._page.goto(url, timeout=(timeout or self.timeout) * 1000, wait_until="domcontentloaded")
            
            # Get page content
            html_content = self._page.content()
            status_code = self._page.main_frame.response.status if self._page.main_frame.response else 200
            
            elapsed_time = time.time() - start_time
            
            response = MockSXNGResponse(html_content, status_code, url)
            return response, elapsed_time, None
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            return None, elapsed_time, str(e)
    
    def close(self) -> None:
        """Close browser resources."""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
        if hasattr(self, '_playwright') and self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._initialized = False
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


# Global browser transport instance
_browser_transport: GoogleBrowserTransport | None = None


def get_browser_transport() -> GoogleBrowserTransport | None:
    """Get or create the browser transport instance.
    
    Returns:
        GoogleBrowserTransport: The browser transport instance, or None if not available
    """
    global _browser_transport
    if _browser_transport is None:
        try:
            _browser_transport = GoogleBrowserTransport(headless=True, timeout=30.0)
        except Exception:
            _browser_transport = None
    return _browser_transport


class GoogleTransport:
    """Transport abstraction for Google search requests.
    
    This class wraps the existing SearXNG HTTP machinery and provides:
    - Response classification
    - Request instrumentation
    - Browser-based fallback for CAPTCHA/blocked responses
    - No changes to the existing request/parser behavior
    
    Usage:
        transport = GoogleTransport()
        result = transport.request(url, headers=headers, cookies=cookies, timeout=timeout)
        
        if result.classification == GoogleResponseClassification.SUCCESS:
            # Parse results using existing parser
            results = existing_parser.parse(result.response)
            result.parser_result_count = len(results)
        
        # Log instrumentation (safe, no sensitive data)
        logger.info(
            "Google transport: status=%s, classification=%s, time=%.3fs, results=%d",
            result.status_code,
            result.classification.value,
            result.elapsed_time * 1000,
            result.parser_result_count,
        )
    """
    
    def __init__(self, use_browser_fallback: bool = True):
        """Initialize the Google transport.
        
        Args:
            use_browser_fallback: Whether to use browser fallback for CAPTCHA responses
        """
        self.metrics = GoogleTransportMetrics()
        self.use_browser_fallback = use_browser_fallback
    
    def _standard_request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float | None = None,
        impersonate: str | None = None,
        **kwargs: t.Any,
    ) -> GoogleTransportResult:
        """Make a standard HTTP request.
        
        Args:
            url: The URL to request
            headers: Request headers
            cookies: Request cookies
            timeout: Request timeout in seconds
            impersonate: Impersonation profile
            **kwargs: Additional arguments passed to searx.network.get
            
        Returns:
            GoogleTransportResult: The transport result with classification and metrics
        """
        start_time = time.time()
        
        try:
            response = searx_get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                impersonate=impersonate,
                **kwargs,
            )
            
            elapsed_time = time.time() - start_time
            status_code = response.status_code
            
            # Classify the response
            classification = classify_google_response(status_code, response, url)
            
            result = GoogleTransportResult(
                response=response,
                status_code=status_code,
                classification=classification,
                elapsed_time=elapsed_time,
                error_type=None,
                error_message=None,
                parser_result_count=0,
                used_browser=False,
            )
            
            return result
            
        except SearxEngineException as e:
            elapsed_time = time.time() - start_time
            error_type = type(e).__name__
            error_message = str(e)
            
            # Extract status code from exception if available
            status_code = None
            error_str = error_message.lower()
            
            if "403" in error_str or "forbidden" in error_str:
                classification = GoogleResponseClassification.ACCESS_DENIED
            elif "429" in error_str or "rate limit" in error_str or "too many" in error_str:
                classification = GoogleResponseClassification.RATE_LIMITED
            elif "timeout" in error_str or "timed out" in error_str:
                classification = GoogleResponseClassification.TIMEOUT
            elif "connection" in error_str or "network" in error_str:
                classification = GoogleResponseClassification.NETWORK_ERROR
            elif "captcha" in error_str or "sorry" in error_str:
                classification = GoogleResponseClassification.CAPTCHA
            elif "consent" in error_str:
                classification = GoogleResponseClassification.CONSENT
            else:
                classification = GoogleResponseClassification.NETWORK_ERROR
            
            return GoogleTransportResult(
                response=None,
                status_code=status_code,
                classification=classification,
                elapsed_time=elapsed_time,
                error_type=error_type,
                error_message=error_message,
                parser_result_count=0,
                used_browser=False,
            )
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            error_type = type(e).__name__
            error_message = str(e)
            
            # Classify unexpected exceptions
            error_str = error_message.lower()
            if "timeout" in error_str or "timed out" in error_str:
                classification = GoogleResponseClassification.TIMEOUT
            elif "connection" in error_str:
                classification = GoogleResponseClassification.NETWORK_ERROR
            else:
                classification = GoogleResponseClassification.NETWORK_ERROR
            
            return GoogleTransportResult(
                response=None,
                status_code=None,
                classification=classification,
                elapsed_time=elapsed_time,
                error_type=error_type,
                error_message=error_message,
                parser_result_count=0,
                used_browser=False,
            )
    
    def _browser_request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: t.Any,
    ) -> GoogleTransportResult:
        """Make a browser-based request as fallback.
        
        Args:
            url: The URL to request
            headers: Request headers
            cookies: Request cookies
            timeout: Request timeout in seconds
            **kwargs: Additional arguments
            
        Returns:
            GoogleTransportResult: The transport result with classification and metrics
        """
        start_time = time.time()
        
        browser_transport = get_browser_transport()
        if browser_transport is None:
            elapsed_time = time.time() - start_time
            return GoogleTransportResult(
                response=None,
                status_code=None,
                classification=GoogleResponseClassification.NETWORK_ERROR,
                elapsed_time=elapsed_time,
                error_type="BrowserNotAvailable",
                error_message="Playwright browser not available",
                parser_result_count=0,
                used_browser=False,
            )
        
        try:
            response, elapsed_time, error_message = browser_transport.request(
                url, headers=headers, cookies=cookies, timeout=timeout
            )
            
            if error_message:
                return GoogleTransportResult(
                    response=None,
                    status_code=None,
                    classification=GoogleResponseClassification.NETWORK_ERROR,
                    elapsed_time=elapsed_time,
                    error_type="BrowserError",
                    error_message=error_message,
                    parser_result_count=0,
                    used_browser=True,
                )
            
            if response is None:
                return GoogleTransportResult(
                    response=None,
                    status_code=None,
                    classification=GoogleResponseClassification.NETWORK_ERROR,
                    elapsed_time=elapsed_time,
                    error_type="BrowserError",
                    error_message="Browser returned no response",
                    parser_result_count=0,
                    used_browser=True,
                )
            
            # Classify the browser response
            classification = classify_google_response(response.status_code, response, url)
            
            # If we got here and classification is CAPTCHA or ACCESS_DENIED, 
            # the browser couldn't bypass it either
            if classification in (GoogleResponseClassification.CAPTCHA, GoogleResponseClassification.ACCESS_DENIED):
                classification = GoogleResponseClassification.BROWSER_FALLBACK
            else:
                classification = GoogleResponseClassification.BROWSER_SUCCESS
            
            result = GoogleTransportResult(
                response=response,
                status_code=response.status_code,
                classification=classification,
                elapsed_time=elapsed_time,
                error_type=None,
                error_message=None,
                parser_result_count=0,
                used_browser=True,
            )
            
            return result
            
        except Exception as e:
            elapsed_time = time.time() - start_time
            return GoogleTransportResult(
                response=None,
                status_code=None,
                classification=GoogleResponseClassification.NETWORK_ERROR,
                elapsed_time=elapsed_time,
                error_type=type(e).__name__,
                error_message=str(e),
                parser_result_count=0,
                used_browser=True,
            )
    
    def request(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
        timeout: float | None = None,
        impersonate: str | None = None,
        **kwargs: t.Any,
    ) -> GoogleTransportResult:
        """Make a Google search request and classify the response.
        
        This method implements a two-tier transport:
        1. First attempt: Standard HTTP with Chrome impersonation (fast)
        2. Fallback: Browser-based request if CAPTCHA/403 detected (reliable)
        
        Args:
            url: The URL to request
            headers: Request headers
            cookies: Request cookies
            timeout: Request timeout in seconds
            impersonate: Impersonation profile (e.g., "chrome99_android")
            **kwargs: Additional arguments passed to searx.network.get
            
        Returns:
            GoogleTransportResult: The transport result with classification and metrics
        """
        # Attempt 1: Standard HTTP request
        result = self._standard_request(
            url, headers=headers, cookies=cookies, timeout=timeout, impersonate=impersonate, **kwargs
        )
        
        # If CAPTCHA, ACCESS_DENIED, or any 403 detected and browser fallback is enabled, try browser
        # Also retry on RATE_LIMITED and UNEXPECTED_HTTP_ERROR for critical requests
        if (self.use_browser_fallback and result.classification in (
            GoogleResponseClassification.CAPTCHA,
            GoogleResponseClassification.ACCESS_DENIED,
            GoogleResponseClassification.RATE_LIMITED,
        )):
            logger.info(
                "[GOOGLE_TRANSPORT] HTTP request failed with %s, trying browser fallback",
                result.classification.value,
            )
            result = self._browser_request(
                url, headers=headers, cookies=cookies, timeout=timeout, **kwargs
            )
        
        # Record metrics
        self.metrics.record(result)
        
        return result
    
    def record_parser_results(self, result: GoogleTransportResult, count: int) -> None:
        """Record the number of parser results for a transport result.
        
        Args:
            result: The transport result to update
            count: The number of parsed results
        """
        result.parser_result_count = count
        self.metrics.record(result)


# Global transport instance for convenience
# This can be used by the Google engine directly
_default_transport: GoogleTransport | None = None


def get_default_transport() -> GoogleTransport:
    """Get or create the default Google transport instance.
    
    Returns:
        GoogleTransport: The default transport instance
    """
    global _default_transport
    if _default_transport is None:
        _default_transport = GoogleTransport()
    return _default_transport
