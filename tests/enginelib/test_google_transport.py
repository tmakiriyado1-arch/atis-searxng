# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for Google Transport classification.

These tests verify the classification logic for Google responses without
making live HTTP requests. All tests use mocked responses.
"""

import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

from searx.enginelib.google_transport import (
    GoogleTransport,
    GoogleTransportResult,
    GoogleTransportMetrics,
    GoogleResponseClassification,
    classify_google_response,
    _classify_by_status,
    _classify_by_body,
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
        )
        assert result.status_code == 200
        assert result.classification == GoogleResponseClassification.SUCCESS
        assert result.elapsed_time == 1.5
        assert result.parser_result_count == 10


class TestGoogleTransportMetrics:
    """Tests for GoogleTransportMetrics."""
    
    def test_initial_state(self):
        """Test initial state of metrics."""
        metrics = GoogleTransportMetrics()
        assert metrics.total_requests == 0
        assert metrics.classifications == {}
        assert metrics.total_elapsed_time == 0.0
        assert metrics.avg_elapsed_time == 0.0
    
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


class TestGoogleTransport:
    """Tests for GoogleTransport class."""
    
    def test_init(self):
        """Test transport initialization."""
        transport = GoogleTransport()
        assert transport.metrics is not None
        assert isinstance(transport.metrics, GoogleTransportMetrics)
    
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
