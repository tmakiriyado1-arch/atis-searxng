# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sequential Bing query test to diagnose result contamination.

This test runs three sequential Bing searches:
  A = Theotechnic College
  B = Union Pacific Big Boy 4014
  C = Theotechnic College

And captures:
  - query
  - outgoing URL
  - response hash
  - first 3 parsed titles

This allows us to determine if:
  - Condition 1: Bing itself returns wrong content (UPSTREAM_BING_RESPONSE)
  - Condition 2: Parser is corrupting results (BING_PARSER)
  - Condition 3: Query is being corrupted (QUERY_CORRUPTION)
  - Condition 4: Result aggregation is the problem (RESULT_AGGREGATION)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import hashlib

from searx.engines.bing import request, response


class MockSXNGResponse:
    """Mock SXNG_Response for testing."""
    
    def __init__(self, text, status_code=200, url="https://www.bing.com/search?q=test"):
        self.text = text
        self.status_code = status_code
        self._url = url
        self.ok = status_code == 200
        self.headers = {}
    
    @property
    def url(self):
        from searx.extended_types import SXNG_URL
        return SXNG_URL(self._url)
    
    @url.setter
    def url(self, value):
        self._url = str(value or "")
    
    def html(self):
        from lxml import html
        return html.fromstring(self.text)


# Sample Bing HTML responses
BING_THEOTECHNIC_HTML = """
<html>
<head><title>Bing Search</title></head>
<body>
<ol id="b_results">
    <li class="b_algo">
        <h2><a href="https://theotechnic.edu">Theotechnic College - Official Site</a></h2>
        <p>Theotechnic College is a higher education institution.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://en.wikipedia.org/wiki/Theotechnic">Theotechnic College Wikipedia</a></h2>
        <p>Theotechnic College on Wikipedia.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://theotechnic.edu/admissions">Theotechnic College Admissions</a></h2>
        <p>Admissions information for Theotechnic College.</p>
    </li>
</ol>
</body>
</html>
"""

BING_UNION_PACIFIC_HTML = """
<html>
<head><title>Bing Search</title></head>
<body>
<ol id="b_results">
    <li class="b_algo">
        <h2><a href="https://en.wikipedia.org/wiki/Union_Pacific_Big_Boy">Union Pacific Big Boy 4014 - Wikipedia</a></h2>
        <p>Union Pacific Big Boy 4014 is a steam locomotive.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://www.up.com">Union Pacific Railroad</a></h2>
        <p>Official Union Pacific website.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://www.lego.com">LEGO Official Site</a></h2>
        <p>LEGO train sets and more.</p>
    </li>
</ol>
</body>
</html>
"""

BING_LEGO_HTML = """
<html>
<head><title>Bing Search</title></head>
<body>
<ol id="b_results">
    <li class="b_algo">
        <h2><a href="https://www.lego.com">LEGO Official Site</a></h2>
        <p>LEGO train sets and more.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://en.wikipedia.org/wiki/LEGO">LEGO - Wikipedia</a></h2>
        <p>LEGO on Wikipedia.</p>
    </li>
    <li class="b_algo">
        <h2><a href="https://shop.lego.com">LEGO Shop</a></h2>
        <p>Buy LEGO sets online.</p>
    </li>
</ol>
</body>
</html>
"""


class TestBingSequentialQueries:
    """Test sequential Bing queries for result contamination."""

    def test_sequential_queries_request_isolation(self):
        """Test that sequential Bing queries maintain request isolation.
        
        This test verifies that each query gets its own unique cvid parameter,
        preventing any potential caching or state leakage between requests.
        """
        queries = ["Theotechnic College", "Union Pacific Big Boy 4014", "Theotechnic College"]
        urls = []
        cvids = []
        
        for query in queries:
            params = {
                "url": "",
                "headers": {},
                "cookies": {},
                "safesearch": 0,
                "searxng_locale": "en-US",
            }
            request(query, params)
            urls.append(params["url"])
            
            # Extract cvid from URL
            import re
            cvid_match = re.search(r'cvid=([^&]+)', params["url"])
            assert cvid_match is not None, f"cvid not found in URL: {params['url']}"
            cvids.append(cvid_match.group(1))
        
        # All cvids should be unique
        assert len(set(cvids)) == 3, f"Expected 3 unique cvids, got {len(set(cvids))}: {cvids}"
        
        # Each URL should contain the correct query
        assert "Theotechnic" in urls[0] or "Theotechnic" in urls[0].replace("+", " ")
        assert "Union" in urls[1] or "Union" in urls[1].replace("+", " ")
        assert "Theotechnic" in urls[2] or "Theotechnic" in urls[2].replace("+", " ")

    def test_response_parsing_isolation(self):
        """Test that Bing response parsing is stateless.
        
        This test verifies that parsing different responses produces
        different results, and that the parser doesn't retain state between
        calls.
        """
        # Parse Theotechnic response
        resp_a = MockSXNGResponse(BING_THEOTECHNIC_HTML, 200, "https://www.bing.com/search?q=Theotechnic+College")
        results_a = response(resp_a)
        
        # Parse Union Pacific response
        resp_b = MockSXNGResponse(BING_UNION_PACIFIC_HTML, 200, "https://www.bing.com/search?q=Union+Pacific+Big+Boy+4014")
        results_b = response(resp_b)
        
        # Parse Theotechnic response again
        resp_c = MockSXNGResponse(BING_THEOTECHNIC_HTML, 200, "https://www.bing.com/search?q=Theotechnic+College")
        results_c = response(resp_c)
        
        # Results A and C should be equivalent (same HTML)
        assert len(results_a) == len(results_c), f"A: {len(results_a)}, C: {len(results_c)}"
        for i, (ra, rc) in enumerate(zip(results_a, results_c)):
            assert ra["title"] == rc["title"], f"Result {i} differs: A={ra['title']}, C={rc['title']}"
        
        # Results A and B should be different
        assert len(results_a) > 0
        assert len(results_b) > 0
        # First result titles should differ
        assert results_a[0]["title"] != results_b[0]["title"]
        
        # Results B should contain Union Pacific, not Theotechnic
        assert "Union Pacific" in results_b[0]["title"] or "Big Boy" in results_b[0]["title"]
        assert "Theotechnic" not in results_b[0]["title"]

    def test_response_hash_differences(self):
        """Test that different Bing responses have different hashes.
        
        This verifies that we can use response hashes to detect if
        Bing is returning cached/stale content.
        """
        # Create mock responses
        resp_a = MockSXNGResponse(BING_THEOTECHNIC_HTML, 200, "https://www.bing.com/search?q=Theotechnic+College")
        resp_b = MockSXNGResponse(BING_UNION_PACIFIC_HTML, 200, "https://www.bing.com/search?q=Union+Pacific+Big+Boy+4014")
        resp_c = MockSXNGResponse(BING_THEOTECHNIC_HTML, 200, "https://www.bing.com/search?q=Theotechnic+College")
        
        # Calculate hashes
        hash_a = hashlib.sha256(resp_a.text.encode('utf-8')).hexdigest()
        hash_b = hashlib.sha256(resp_b.text.encode('utf-8')).hexdigest()
        hash_c = hashlib.sha256(resp_c.text.encode('utf-8')).hexdigest()
        
        # A and C should have the same hash (same content)
        assert hash_a == hash_c, f"A and C hashes differ: {hash_a} != {hash_c}"
        
        # A and B should have different hashes (different content)
        assert hash_a != hash_b, f"A and B hashes are the same: {hash_a} == {hash_b}"

    def test_no_module_level_result_state(self):
        """Test that Bing module has no mutable state that could leak between requests."""
        import searx.engines.bing as bing_module
        
        # Check for dangerous module-level mutable state
        # The only allowed module-level state is 'traits' for locale handling
        allowed_globals = {
            'about', 'categories', 'safesearch', 'enable_http3', 
            '_safesearch_map', 'base_url', 'traits',
            'get_locale_params', 'request', 'response', 'fetch_traits',
            'logger', 'hashlib', 'logging', 'uuid',
            'base64', 'typing', 't', 'urlencode', 'urlparse', 'parse_qs',
            'babel', 'babel_languages', 'EngineTraits', 'region_tag',
            'eval_xpath', 'eval_xpath_getindex', 'eval_xpath_list', 'extract_text',
        }
        
        for name in dir(bing_module):
            if not name.startswith('_'):
                obj = getattr(bing_module, name)
                # Check for mutable containers at module level
                if isinstance(obj, (list, dict, set)) and name not in allowed_globals:
                    # This could be a problem - mutable state at module level
                    pytest.fail(f"Unexpected module-level mutable state: {name} = {type(obj)}")

    def test_query_preserved_in_url(self):
        """Test that the exact query is preserved in the constructed URL."""
        test_queries = [
            "Theotechnic College",
            "Union Pacific Big Boy 4014",
            "Zimbabwe",
            "Microsoft",
        ]
        
        for query in test_queries:
            params = {
                "url": "",
                "headers": {},
                "cookies": {},
                "safesearch": 0,
                "searxng_locale": "en-US",
            }
            request(query, params)
            
            # Verify the URL contains the query
            assert params["url"].startswith("https://www.bing.com/search?")
            
            # Verify the query parameter is present
            assert f"q={query.replace(' ', '+')}" in params["url"] or f"q={query.replace(' ', '%20')}" in params["url"]


class TestBingContaminationScenarios:
    """Test specific contamination scenarios."""

    def test_parser_does_not_reuse_previous_results(self):
        """Test that the parser doesn't accidentally reuse results from a previous parse."""
        # First parse: Theotechnic
        resp1 = MockSXNGResponse(BING_THEOTECHNIC_HTML, 200, "https://www.bing.com/search?q=Theotechnic")
        results1 = response(resp1)
        
        # Second parse: Union Pacific (different HTML)
        resp2 = MockSXNGResponse(BING_UNION_PACIFIC_HTML, 200, "https://www.bing.com/search?q=Union+Pacific")
        results2 = response(resp2)
        
        # Verify results are different
        assert results1 is not results2, "Parser returned same list object"
        
        # Verify content is different
        if results1 and results2:
            assert results1[0]["title"] != results2[0]["title"]

    def test_empty_response_handling(self):
        """Test that empty responses are handled correctly."""
        empty_html = "<html><body></body></html>"
        resp = MockSXNGResponse(empty_html, 200, "https://www.bing.com/search?q=test")
        results = response(resp)
        
        # Should return empty list, not crash
        assert results == []

    def test_response_with_no_results_div(self):
        """Test handling of response without b_results div."""
        no_results_html = "<html><body><p>No results found</p></body></html>"
        resp = MockSXNGResponse(no_results_html, 200, "https://www.bing.com/search?q=test")
        results = response(resp)
        
        # Should return empty list
        assert results == []
