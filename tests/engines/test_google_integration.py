# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests for Google engine with GoogleTransport.

These tests verify that GoogleTransport is properly wired into the Google engine.
"""

import pytest
from unittest.mock import Mock, patch

from searx.engines.google import request, _transport
from searx.enginelib.google_transport import GoogleTransport, GoogleTransportResult, GoogleResponseClassification


@pytest.fixture(autouse=True)
def mock_google_traits():
    """Mock the traits variable that is injected by SearXNG framework."""
    import searx.engines.google as google_module
    if not hasattr(google_module, 'traits'):
        # Create a mock traits object
        from searx.enginelib.traits import EngineTraits
        google_module.traits = EngineTraits()


class TestGoogleTransportIntegration:
    """Tests for GoogleTransport integration with Google engine."""

    def test_transport_instance_exists(self):
        """Test that the module-level transport instance exists."""
        assert _transport is not None
        assert isinstance(_transport, GoogleTransport)

    def test_request_calls_transport(self):
        """Test that request() calls GoogleTransport.request()."""
        mock_params = {
            "url": "https://www.google.com/wml/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome99_android",
            "pageno": 1,
        }
        
        # Mock google_request to avoid traits issue
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            # Mock the transport's request method
            with patch.object(_transport, 'request') as mock_transport_request:
                mock_result = GoogleTransportResult(
                    response=Mock(),
                    status_code=200,
                    classification=GoogleResponseClassification.SUCCESS,
                    elapsed_time=1.0,
                )
                mock_transport_request.return_value = mock_result
                
                # Call the request function
                request("test query", mock_params)
                
                # Verify transport.request was called
                mock_transport_request.assert_called_once()
                call_args = mock_transport_request.call_args
                
                # Verify arguments were passed correctly
                assert call_args[1]['url'] == mock_params['url']
                assert call_args[1]['headers'] == mock_params['headers']
                assert call_args[1]['cookies'] == mock_params['cookies']
                assert call_args[1]['timeout'] == mock_params['timeout']
                assert call_args[1]['impersonate'] == mock_params['impersonate']

    def test_request_stores_transport_response(self):
        """Test that request() stores transport response in params."""
        mock_params = {
            "url": "https://www.google.com/wml/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome99_android",
            "pageno": 1,
        }
        
        # Mock google_request to avoid traits issue
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            # Mock the transport's request method
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
                
                # Verify transport response was stored in params
                assert "_transport_response" in mock_params
                assert mock_params["_transport_response"] is mock_response

    def test_request_handles_transport_failure(self):
        """Test that request() handles transport failure gracefully."""
        mock_params = {
            "url": "https://www.google.com/wml/search?q=test",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "timeout": 30,
            "impersonate": "chrome99_android",
            "pageno": 1,
        }
        
        # Mock google_request to avoid traits issue
        with patch('searx.engines.google.google_request') as mock_google_request:
            mock_google_request.return_value = None
            
            # Mock the transport's request method to return None response
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
                
                # Verify transport response was NOT stored in params (because it's None)
                assert "_transport_response" not in mock_params

    def test_request_preserves_existing_behavior(self):
        """Test that request() still calls google_request()."""
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "timeout": 30,
            "impersonate": "",
            "pageno": 1,
        }
        
        with patch('searx.engines.google.google_request') as mock_google_request:
            with patch.object(_transport, 'request') as mock_transport_request:
                mock_google_request.return_value = None
                mock_transport_request.return_value = GoogleTransportResult(
                    response=Mock(),
                    status_code=200,
                    classification=GoogleResponseClassification.SUCCESS,
                    elapsed_time=1.0,
                )
                
                request("test", mock_params)
                
                # Verify google_request was still called
                mock_google_request.assert_called_once_with("test", mock_params)
