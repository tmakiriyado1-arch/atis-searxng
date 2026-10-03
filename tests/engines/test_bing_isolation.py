# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Bing engine request isolation and query integrity.

These tests verify:
1. Query preservation - exact query reaches Bing unchanged
2. Request isolation - each request gets unique correlation ID
3. Cache prevention headers are set
"""

import pytest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from searx.engines.bing import request


class TestBingQueryPreservation:
    """Test that Bing receives the exact query unchanged."""

    def test_query_preserved_exactly(self):
        """Test that the exact query is preserved in the request."""
        test_query = "Theotechnic College"
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request(test_query, mock_params)
        
        # Verify the URL contains the exact query
        assert "q=Theotechnic+College" in mock_params["url"]
        assert "q=Theotechnic%20College" in mock_params["url"] or "q=Theotechnic+College" in mock_params["url"]

    def test_query_with_spaces_encoded(self):
        """Test that spaces in query are properly encoded."""
        test_query = "Union Pacific Big Boy 4014"
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request(test_query, mock_params)
        
        # Verify the URL contains the encoded query
        assert mock_params["url"].startswith("https://www.bing.com/search?")
        # The query should be URL-encoded
        assert "Union" in mock_params["url"]
        assert "Pacific" in mock_params["url"]
        assert "Big" in mock_params["url"]
        assert "Boy" in mock_params["url"]
        assert "4014" in mock_params["url"]

    def test_different_queries_stay_isolated(self):
        """Test that different requests maintain their own query values."""
        queries = [
            "Theotechnic College",
            "Union Pacific Big Boy 4014",
            "Theotechnic College",
        ]
        results = []
        
        for query in queries:
            mock_params = {
                "url": "",
                "headers": {},
                "cookies": {},
                "safesearch": 0,
                "searxng_locale": "en-US",
            }
            request(query, mock_params)
            results.append(mock_params["url"])
        
        # First and third should have equivalent query
        assert "Theotechnic" in results[0]
        assert "Theotechnic" in results[2]
        
        # Second should have Union Pacific
        assert "Union" in results[1]
        assert "Pacific" in results[1]


class TestBingRequestCorrelation:
    """Test that Bing requests get unique correlation IDs."""

    def test_unique_cvid_per_request(self):
        """Test that each Bing request gets a unique cvid parameter."""
        cvids = []
        
        for i in range(3):
            mock_params = {
                "url": "",
                "headers": {},
                "cookies": {},
                "safesearch": 0,
                "searxng_locale": "en-US",
            }
            request(f"test query {i}", mock_params)
            
            # Extract cvid from URL
            import re
            cvid_match = re.search(r'cvid=([^&]+)', mock_params["url"])
            assert cvid_match is not None, f"cvid not found in URL: {mock_params['url']}"
            cvids.append(cvid_match.group(1))
        
        # All cvids should be unique
        assert len(set(cvids)) == 3, f"Expected 3 unique cvids, got {len(set(cvids))}: {cvids}"
        
        # Each cvid should be a valid UUID format
        for cvid in cvids:
            # UUID v4 format: 8-4-4-4-12 hex digits
            assert len(cvid) == 36
            assert cvid.count('-') == 4

    def test_cvid_and_form_params_present(self):
        """Test that cvid and form parameters are included in the request."""
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request("test query", mock_params)
        
        # Verify cvid is in the URL
        assert "cvid=" in mock_params["url"]
        
        # Verify form=QBRE is in the URL
        assert "form=QBRE" in mock_params["url"]


class TestBingCachePrevention:
    """Test that Bing requests include cache prevention headers."""

    def test_cache_control_headers_added(self):
        """Test that cache prevention headers are added to the request."""
        mock_params = {
            "url": "",
            "headers": {"User-Agent": "test"},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request("test query", mock_params)
        
        # Verify cache prevention headers are set
        assert "Cache-Control" in mock_params["headers"]
        assert mock_params["headers"]["Cache-Control"] == "no-cache, no-store"
        
        assert "Pragma" in mock_params["headers"]
        assert mock_params["headers"]["Pragma"] == "no-cache"
        
        assert "Accept" in mock_params["headers"]
        assert "text/html" in mock_params["headers"]["Accept"]

    def test_existing_headers_preserved(self):
        """Test that existing headers are not overwritten."""
        mock_params = {
            "url": "",
            "headers": {"User-Agent": "custom-agent", "X-Custom": "value"},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request("test query", mock_params)
        
        # Verify existing headers are preserved
        assert mock_params["headers"]["User-Agent"] == "custom-agent"
        assert mock_params["headers"]["X-Custom"] == "value"
        
        # And new headers are added
        assert "Cache-Control" in mock_params["headers"]
        assert "Pragma" in mock_params["headers"]


class TestBingSafesearch:
    """Test that safesearch parameter is properly included."""

    def test_safesearch_off(self):
        """Test safesearch=0 maps to 'off'."""
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 0,
            "searxng_locale": "en-US",
        }
        
        request("test", mock_params)
        
        assert "adlt=off" in mock_params["url"]

    def test_safesearch_moderate(self):
        """Test safesearch=1 maps to 'moderate'."""
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 1,
            "searxng_locale": "en-US",
        }
        
        request("test", mock_params)
        
        assert "adlt=moderate" in mock_params["url"]

    def test_safesearch_strict(self):
        """Test safesearch=2 maps to 'strict'."""
        mock_params = {
            "url": "",
            "headers": {},
            "cookies": {},
            "safesearch": 2,
            "searxng_locale": "en-US",
        }
        
        request("test", mock_params)
        
        assert "adlt=strict" in mock_params["url"]
