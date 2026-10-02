# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Google Transport classification.

These tests verify the classification logic for Google responses without
making live HTTP requests. All tests use mocked responses.
"""

import pytest
import sys
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime

from searx.enginelib.google_transport import (
    GoogleTransport,
    GoogleTransportResult,
    GoogleTransportMetrics,
    GoogleResponseClassification,
    GoogleBrowserTransport,
    MockSXNGResponse,
    classify_google_response,
    _classify_by_status,
    _classify_by_body,
    get_browser_transport,
)


class TestGoogleResponseClassification:
    """Tests for classification enum."""
    
    def test_classification_values(self):
        """Test that all classification values are strings."""
        for classification in GoogleResponseClassification:
            assert isinstance(classification.value, str)
    
    def test_classification_unique(self):
        """Test that all classification values are unique."""
        values = [c.value for c in GoogleResponseClassification]
        assert len(values) == len(set(values))
    
    def test_new_classifications_exist(self):
        """Test that new browser-related classifications exist."""
        assert GoogleResponseClassification.BROWSER_SUCCESS.value == "browser_success"
        assert GoogleResponseClassification.BROWSER_FALLBACK.value == "browser_fallback"


class TestClassifyByStatus:
    """Tests for _classify_by_status function."""
    
    def test_success_200(self):
        assert _classify_by_status(200) == GoogleResponseClassification.SUCCESS
    
    def test_rate_limited_429(self):
        assert _classify_by_status(429) == GoogleResponseClassification.RATE_LIMITED
    
    def test_access_denied_403(self):
        assert _classify_by_status(403) == GoogleResponseClassification.ACCESS_DENIED
    
    def test_not_found_404(self):
        assert _classify_by_status(404) == GoogleResponseClassification.UNEXPECTED_HTTP_ERROR
    
    def test_server_error_500(self):
        assert _classify_by_status(500) == GoogleResponseClassification.UNEXPECTED_HTTP_ERROR
    
    def test_server_error_503(self):
        assert _classify_by_status(503) == GoogleResponseClassification.UNEXPECTED_HTTP_ERROR


class TestClassifyByBody:
    """Tests for _classify_by_body function."""
    
    def test_captcha_body(self):
        """Test CAPTCHA detection from body content."""
        body = "<html><body>Please verify you are not a robot</body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_captcha_body_recaptcha(self):
        """Test CAPTCHA detection from recaptcha content."""
        body = "<html><body><div class='recaptcha'></div></body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_consent_body(self):
        """Test consent page detection from body content."""
        body = "<html><body>Before you continue to Google Search, accept all cookies</body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CONSENT
    
    def test_consent_body_google(self):
        """Test consent page detection from consent.google."""
        body = "<html><body>consent.google.com</body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CONSENT
    
    def test_bot_protection_body(self):
        """Test bot protection detection from body content."""
        body = "<html><body>Our systems have detected unusual traffic</body></html>"
        result = _classify_by_body(403, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_rate_limit_body(self):
        """Test rate limit detection from body content."""
        body = "<html><body>Too many requests. Please slow down.</body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.RATE_LIMITED
    
    def test_403_without_captcha_indicators(self):
        """Test 403 without CAPTCHA indicators is access_denied."""
        body = "<html><body>Forbidden</body></html>"
        result = _classify_by_body(403, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.ACCESS_DENIED
    
    def test_sorry_url_redirect(self):
        """Test CAPTCHA detection from sorry.google.com URL."""
        body = "<html><body>Redirecting...</body></html>"
        result = _classify_by_body(200, body, "https://sorry.google.com/sorry/index")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_sorry_path(self):
        """Test CAPTCHA detection from /sorry/ path."""
        body = "<html><body>Redirecting...</body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/sorry/index")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_success_no_indicators(self):
        """Test success classification when no failure indicators present."""
        body = "<html><body><div class='rc'>Search results</div></body></html>"
        result = _classify_by_body(200, body, "https://www.google.com/search")
        assert result == GoogleResponseClassification.SUCCESS


class TestClassifyGoogleResponse:
    """Tests for classify_google_response function."""
    
    def test_success_response(self):
        """Test classification of successful response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>Results</body></html>"
        
        result = classify_google_response(200, mock_response, "https://www.google.com/search")
        assert result == GoogleResponseClassification.SUCCESS
    
    def test_403_response(self):
        """Test classification of 403 response."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        
        result = classify_google_response(403, mock_response, "https://www.google.com/search")
        assert result == GoogleResponseClassification.ACCESS_DENIED
    
    def test_429_response(self):
        """Test classification of 429 response."""
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.text = "Too Many Requests"
        
        result = classify_google_response(429, mock_response, "https://www.google.com/search")
        assert result == GoogleResponseClassification.RATE_LIMITED
    
    def test_captcha_response(self):
        """Test classification of CAPTCHA response."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html><body>verify you are not a robot</body></html>"
        
        result = classify_google_response(200, mock_response, "https://www.google.com/search")
        assert result == GoogleResponseClassification.CAPTCHA
    
    def test_none_response(self):
        """Test classification when response is None."""
        result = classify_google_response(None, None, "https://www.google.com/search")
        assert result == GoogleResponseClassification.NETWORK_ERROR
    
    def test_none_status(self):
        """Test classification when status_code is None."""
        result = classify_google_response(None, None, "https://www.google.com/search")
        assert result == GoogleResponseClassification.NETWORK_ERROR


class TestGoogleTransportResult:
    """Tests for GoogleTransportResult dataclass."""
    
    def test_default_values(self):
        """Test default values of GoogleTransportResult."""
        result = GoogleTransportResult()
        assert result.response is None
        assert result.status_code is None
        assert result.classification == GoogleResponseClassification.NETWORK_ERROR
        assert result.elapsed_time == 0.0
        assert result.error_type is None
        assert result.error_message is None
        assert result.parser_result_count == 0
        assert result.used_browser is False
    
    def test_custom_values(self):
        """Test custom values of GoogleTransportResult."""
        mock_response = Mock()
        result = GoogleTransportResult(
            response=mock_response,
            status_code=200,
            classification=GoogleResponseClassification.SUCCESS,
            elapsed_time=1.5,
            error_type=None,
            error_message=None,
            parser_result_count=10,
            used_browser=False,
        )
        assert result.status_code == 200
        assert result.classification == GoogleResponseClassification.SUCCESS
        assert result.elapsed_time == 1.5
        assert result.parser_result_count == 10
        assert result.used_browser is False
    
    def test_browser_result(self):
        """Test result with browser usage."""
        mock_response = Mock()
        result = GoogleTransportResult(
            response=mock_response,
            status_code=200,
            classification=GoogleResponseClassification.BROWSER_SUCCESS,
            elapsed_time=2.5,
            used_browser=True,
        )
        assert result.used_browser is True
        assert result.classification == GoogleResponseClassification.BROWSER_SUCCESS


class TestGoogleTransportMetrics:
    """Tests for GoogleTransportMetrics."""
    
    def test_initial_state(self):
        """Test initial state of metrics."""
        metrics = GoogleTransportMetrics()
        assert metrics.total_requests == 0
        assert metrics.classifications == {}
        assert metrics.total_elapsed_time == 0.0
        assert metrics.avg_elapsed_time == 0.0
        assert metrics.browser_requests == 0
        assert metrics.fallback_requests == 0
    
    def test_record_result(self):
        """Test recording a transport result."""
        metrics = GoogleTransportMetrics()
        result = GoogleTransportResult(
            status_code=200,
            classification=GoogleResponseClassification.SUCCESS,
            elapsed_time=1.0,
            parser_result_count=5,
        )
        metrics.record(result)
        
        assert metrics.total_requests == 1
        assert metrics.classifications[GoogleResponseClassification.SUCCESS] == 1
        assert metrics.total_elapsed_time == 1.0
        assert metrics.avg_elapsed_time == 1.0
        assert metrics.browser_requests == 0
    
    def test_record_multiple_results(self):
        """Test recording multiple transport results."""
        metrics = GoogleTransportMetrics()
        
        result1 = GoogleTransportResult(
            status_code=200,
            classification=GoogleResponseClassification.SUCCESS,
            elapsed_time=1.0,
            parser_result_count=5,
        )
        result2 = GoogleTransportResult(
            status_code=429,
            classification=GoogleResponseClassification.RATE_LIMITED,
            elapsed_time=0.5,
            parser_result_count=0,
        )
        
        metrics.record(result1)
        metrics.record(result2)
        
        assert metrics.total_requests == 2
        assert metrics.classifications[GoogleResponseClassification.SUCCESS] == 1
        assert metrics.classifications[GoogleResponseClassification.RATE_LIMITED] == 1
        assert metrics.total_elapsed_time == 1.5
        assert metrics.avg_elapsed_time == 0.75
    
    def test_record_browser_result(self):
        """Test recording a browser-based result."""
        metrics = GoogleTransportMetrics()
        
        result = GoogleTransportResult(
            status_code=200,
            classification=GoogleResponseClassification.BROWSER_SUCCESS,
            elapsed_time=2.0,
            used_browser=True,
        )
        metrics.record(result)
        
        assert metrics.browser_requests == 1
        assert metrics.fallback_requests == 1
    
    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = GoogleTransportMetrics()
        result = GoogleTransportResult(
            status_code=200,
            classification=GoogleResponseClassification.SUCCESS,
            elapsed_time=1.0,
            parser_result_count=5,
        )
        metrics.record(result)
        
        result_dict = metrics.to_dict()
        
        assert result_dict["total_requests"] == 1
        assert result_dict["classifications"]["success"] == 1
        assert result_dict["avg_elapsed_time"] == 1.0
        assert isinstance(result_dict["last_request_time"], float)
        assert result_dict["browser_requests"] == 0
        assert result_dict["fallback_requests"] == 0


class TestGoogleTransport:
    """Tests for GoogleTransport class."""
    
    def test_init(self):
        """Test transport initialization."""
        transport = GoogleTransport()
        assert transport.metrics is not None
        assert isinstance(transport.metrics, GoogleTransportMetrics)
        assert transport.use_browser_fallback is True
    
    def test_init_no_fallback(self):
        """Test transport initialization without browser fallback."""
        transport = GoogleTransport(use_browser_fallback=False)
        assert transport.use_browser_fallback is False
    
    def test_get_default_transport(self):
        """Test default transport singleton."""
        from searx.enginelib.google_transport import get_default_transport
        
        transport1 = get_default_transport()
        transport2 = get_default_transport()
        
        assert transport1 is transport2
        assert isinstance(transport1, GoogleTransport)
    
    def test_record_parser_results(self):
        """Test recording parser results."""
        transport = GoogleTransport()
        result = GoogleTransportResult(
            status_code=200,
            classification=GoogleResponseClassification.SUCCESS,
            elapsed_time=1.0,
        )
        
        transport.record_parser_results(result, 10)
        
        assert result.parser_result_count == 10
        assert transport.metrics.total_requests == 1


class TestMockSXNGResponse:
    """Tests for MockSXNGResponse class."""
    
    def test_init(self):
        """Test MockSXNGResponse initialization."""
        response = MockSXNGResponse(
            text="<html>test</html>",
            status_code=200,
            url="https://www.google.com/search"
        )
        assert response.text == "<html>test</html>"
        assert response.status_code == 200
        assert response.ok is True
    
    def test_host_property(self):
        """Test host property extraction."""
        response = MockSXNGResponse(
            text="<html>test</html>",
            status_code=200,
            url="https://www.google.com/search"
        )
        assert response.host == "www.google.com"
    
    def test_path_property(self):
        """Test path property extraction."""
        response = MockSXNGResponse(
            text="<html>test</html>",
            status_code=200,
            url="https://www.google.com/search?q=test"
        )
        assert response.path == "/search"
    
    def test_non_200_status(self):
        """Test non-200 status code."""
        response = MockSXNGResponse(
            text="<html>error</html>",
            status_code=404,
            url="https://www.google.com/search"
        )
        assert response.ok is False


class TestGoogleBrowserTransport:
    """Tests for GoogleBrowserTransport class."""
    
    def test_init(self):
        """Test browser transport initialization."""
        transport = GoogleBrowserTransport(headless=True, timeout=30.0)
        assert transport.headless is True
        assert transport.timeout == 30.0
        assert transport._initialized is False
    
    def test_close_not_initialized(self):
        """Test closing uninitialized transport."""
        transport = GoogleBrowserTransport()
        # Should not raise an error
        transport.close()


class TestTransportFallbackBehavior:
    """Tests for transport fallback behavior."""
    
    @patch('searx.enginelib.google_transport.searx_get')
    def test_standard_request_success(self, mock_get):
        """Test standard request succeeds without fallback."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html>results</html>"
        mock_get.return_value = mock_response
        
        transport = GoogleTransport()
        result = transport._standard_request(
            url="https://www.google.com/search",
            timeout=10.0
        )
        
        assert result.classification == GoogleResponseClassification.SUCCESS
        assert result.used_browser is False
        assert result.response is mock_response
    
    @patch('searx.enginelib.google_transport.searx_get')
    def test_standard_request_captcha(self, mock_get):
        """Test standard request returns CAPTCHA classification."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html>verify you are not a robot</html>"
        mock_get.return_value = mock_response
        
        transport = GoogleTransport()
        result = transport._standard_request(
            url="https://www.google.com/search",
            timeout=10.0
        )
        
        assert result.classification == GoogleResponseClassification.CAPTCHA
        assert result.used_browser is False
    
    @patch('searx.enginelib.google_transport.searx_get')
    @patch('searx.enginelib.google_transport.get_browser_transport')
    def test_request_with_fallback_disabled(self, mock_get_browser, mock_get):
        """Test request does not fallback when disabled."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "<html>verify you are not a robot</html>"
        mock_get.return_value = mock_response
        
        transport = GoogleTransport(use_browser_fallback=False)
        result = transport.request(
            url="https://www.google.com/search",
            timeout=10.0
        )
        
        assert result.classification == GoogleResponseClassification.CAPTCHA
        assert result.used_browser is False
        mock_get_browser.assert_not_called()


class TestDeterministicClassification:
    """Tests to ensure classification is deterministic."""
    
    def test_same_input_same_output(self):
        """Test that same input produces same classification."""
        body = "<html><body>verify you are not a robot</body></html>"
        
        result1 = _classify_by_body(200, body, "https://www.google.com/search")
        result2 = _classify_by_body(200, body, "https://www.google.com/search")
        
        assert result1 == result2
    
    def test_different_input_different_output(self):
        """Test that different input can produce different classification."""
        body1 = "<html><body>verify you are not a robot</body></html>"
        body2 = "<html><body>Results</body></html>"
        
        result1 = _classify_by_body(200, body1, "https://www.google.com/search")
        result2 = _classify_by_body(200, body2, "https://www.google.com/search")
        
        assert result1 != result2
        assert result1 == GoogleResponseClassification.CAPTCHA
        assert result2 == GoogleResponseClassification.SUCCESS
