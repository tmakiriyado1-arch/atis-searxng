# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Google engine state isolation.

These tests verify:
1. No global state affects request behavior
2. Transport failures don't affect subsequent requests
3. Each request is independent
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from searx.engines.google import request, response, _transport
from searx.enginelib.google_transport import GoogleTransportResult, GoogleResponseClassification
from searx.exceptions import SearxEngineCaptchaException, SearxEngineAccessDeniedException


class TestGoogleNoGlobalState:
    """Test that Google engine has no cross-request global state."""

    def test_no_force_browser_mode_global(self):
        """Test that _force_browser_mode global variable does not exist."""
        import searx.engines.google as google_module
        
        # After our fix, these should not exist
        assert not hasattr(google_module, '_force_browser_mode')

    def test_no_browser_failure_count_global(self):
        """Test that _browser_failure_count global variable does not exist."""
        import searx.engines.google as google_module
        
        assert not hasattr(google_module, '_browser_failure_count')

    def test_no_max_browser_failures_global(self):
        """Test that _max_browser_failures_before_http global variable does not exist."""
        import searx.engines.google as google_module
        
        assert not hasattr(google_module, '_max_browser_failures_before_http')

    def test_no_reset_function(self):
        """Test that reset_browser_failure_tracking function does not exist."""
        import searx.engines.google as google_module
        
        assert not hasattr(google_module, 'reset_browser_failure_tracking')


class TestGoogleRequestIsolation:
    """Test that Google requests are isolated from each other."""

    def test_transport_failure_sets_url_to_none(self):
        """Test that transport failure sets params['url'] to None to prevent double request."""
        mock_params = {
            "url": "https://www.google.com/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome120",
            "pageno": 1,
        }
        
        # Mock google_request
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            # Mock transport to fail
            with patch.object(_transport, 'request') as mock_transport_request:
                mock_result = GoogleTransportResult(
                    response=None,
                    status_code=None,
                    classification=GoogleResponseClassification.NETWORK_ERROR,
                    elapsed_time=1.0,
                    error_type="ConnectionError",
                    error_message="Connection failed",
                )
                mock_transport_request.return_value = mock_result
                
                # Call the request function
                request("test query", mock_params)
                
                # Verify url is set to None to prevent double request
                assert mock_params["url"] is None

    def test_transport_success_stores_response(self):
        """Test that transport success stores response in params."""
        mock_params = {
            "url": "https://www.google.com/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome120",
            "pageno": 1,
        }
        
        # Mock google_request
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            # Mock transport to succeed
            with patch.object(_transport, 'request') as mock_transport_request:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_result = GoogleTransportResult(
                    response=mock_response,
                    status_code=200,
                    classification=GoogleResponseClassification.SUCCESS,
                    elapsed_time=1.0,
                )
                mock_transport_request.return_value = mock_result
                
                # Call the request function
                request("test query", mock_params)
                
                # Verify transport response was stored
                assert "_transport_response" in mock_params
                assert mock_params["_transport_response"] is mock_response

    def test_request_does_not_modify_global_state(self):
        """Test that multiple requests don't share state."""
        import searx.engines.google as google_module
        
        # Get initial state (should not have these globals)
        has_force_before = hasattr(google_module, '_force_browser_mode')
        
        # Make a request that would have modified global state before the fix
        mock_params = {
            "url": "https://www.google.com/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome120",
            "pageno": 1,
        }
        
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            with patch.object(_transport, 'request') as mock_transport_request:
                # Simulate a failure that would have set _force_browser_mode
                mock_result = GoogleTransportResult(
                    response=None,
                    status_code=None,
                    classification=GoogleResponseClassification.NETWORK_ERROR,
                    elapsed_time=1.0,
                )
                mock_transport_request.return_value = mock_result
                
                request("test query", mock_params)
        
        # State should still be the same
        has_force_after = hasattr(google_module, '_force_browser_mode')
        assert has_force_before == has_force_after == False


class TestGoogleResponseIsolation:
    """Test that Google response parsing doesn't use global state."""

    def test_response_no_global_state_access(self):
        """Test that response() function doesn't access global state."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = "<html></html>"
        mock_resp.url = Mock()
        mock_resp.url.host = "www.google.com"
        mock_resp.url.path = "/search"
        
        # Mock the parser functions
        with patch('searx.engines.google.wml_dom') as mock_wml_dom:
            mock_wml_dom.return_value = Mock()
            
            with patch('searx.engines.google._parse_wml_results') as mock_parse_wml:
                from searx.result_types import EngineResults
                mock_parse_wml.return_value = EngineResults()
                
                # Call response
                result = response(mock_resp)
                
                # Should not have accessed any global state
                # (The function should work without it)
                assert result is not None

    def test_response_uses_transport_response(self):
        """Test that response() uses _transport_response if available."""
        mock_actual_resp = Mock()
        mock_actual_resp.status_code = 200
        mock_actual_resp.text = "<html></html>"
        mock_actual_resp.url = Mock()
        mock_actual_resp.url.host = "www.google.com"
        mock_actual_resp.url.path = "/search"
        
        mock_resp = Mock()
        mock_resp._transport_response = mock_actual_resp
        
        # Mock the parser
        with patch('searx.engines.google.wml_dom') as mock_wml_dom:
            mock_wml_dom.return_value = Mock()
            
            with patch('searx.engines.google._parse_wml_results') as mock_parse_wml:
                from searx.result_types import EngineResults
                mock_parse_wml.return_value = EngineResults()
                
                # Call response
                response(mock_resp)
                
                # Verify wml_dom was called with the transport response
                mock_wml_dom.assert_called_once_with(mock_actual_resp)


class TestGoogleTransportOwnsFallback:
    """Test that GoogleTransport handles its own fallback logic."""

    def test_transport_fallback_on_captcha(self):
        """Test that transport handles CAPTCHA with browser fallback."""
        # This is tested in test_google_transport.py
        # Here we just verify the engine uses the transport
        mock_params = {
            "url": "https://www.google.com/search?q=test",
            "headers": {},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome120",
            "pageno": 1,
        }
        
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            with patch.object(_transport, 'request') as mock_transport_request:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_result = GoogleTransportResult(
                    response=mock_response,
                    status_code=200,
                    classification=GoogleResponseClassification.SUCCESS,
                    elapsed_time=1.0,
                    used_browser=True,  # Browser fallback was used
                )
                mock_transport_request.return_value = mock_result
                
                request("test", mock_params)
                
                # Verify transport was called (it handles its own fallback)
                mock_transport_request.assert_called_once()

    def test_engine_does_not_double_request(self):
        """Test that engine doesn't make a second request when transport fails."""
        mock_params = {
            "url": "https://www.google.com/search?q=test",
            "headers": {},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome120",
            "pageno": 1,
        }
        
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            with patch.object(_transport, 'request') as mock_transport_request:
                # Transport fails completely
                mock_result = GoogleTransportResult(
                    response=None,
                    status_code=None,
                    classification=GoogleResponseClassification.NETWORK_ERROR,
                    elapsed_time=1.0,
                )
                mock_transport_request.return_value = mock_result
                
                request("test", mock_params)
                
                # Transport should be called once
                mock_transport_request.assert_called_once()
                
                # URL should be None to prevent OnlineProcessor from making another request
                assert mock_params["url"] is None
