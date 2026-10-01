#!/usr/bin/env python3
"""
Google Engine Diagnostic for SearXNG on Render
==============================================

This script diagnoses exactly where Google search fails when running
inside the Render/SearXNG environment.

It captures:
  1. DNS resolution
  2. TCP/TLS connectivity
  3. Exact URL requested (query sanitized)
  4. HTTP status code
  5. Response headers (blocking/rate-limiting)
  6. Response body size
  7. First 2-5 KB of response body
  8. Response type classification
  9. Parser results (if valid response)
  10. Request duration and exceptions

Usage on Render:
  cd /path/to/atis-searxng
  .venv/bin/python diagnose_google.py

The script will output a diagnostic report ending with a classification:
  A. DNS/network failure
  B. TCP/TLS failure
  C. HTTP 429/rate limiting
  D. HTTP 403/bot protection
  E. CAPTCHA/consent/interstitial
  F. Valid Google response but parser failure
  G. Other
"""

import json
import random
import re
import socket
import ssl
import time
import urllib.parse
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEST_QUERY = "SearXNG test query"
"""Test query to send to Google. Not sensitive."""

MAX_BODY_DUMP = 5000
"""Maximum bytes of response body to capture (first 5KB)."""

REQUEST_TIMEOUT = 30
"""Request timeout in seconds."""

# ---------------------------------------------------------------------------
# Import SearXNG components
# ---------------------------------------------------------------------------

try:
    import sys
    sys.path.insert(0, "/workspace/github__tmakiriyado1-arch__atis-searxng")
    
    from searx.enginelib.traits import EngineTraits
    from searx.result_types import EngineResults
    from searx.utils import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text
    from searx.search.processors import OnlineParams
    from searx.network import get as searx_get
    from lxml import html
    import babel
    import babel.core
    
    # Import from google engine directly
    from searx.engines.google import (
        nokia_useragents,
        get_google_info,
        detect_google_sorry,
        wml_dom,
        unwrap_google_url,
        time_range_dict,
        filter_mapping,
    )
    
    HAVE_SEARXNG = True
except ImportError as e:
    HAVE_SEARXNG = False
    IMPORT_ERROR = str(e)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def sanitize_url(url):
    """Sanitize URL by removing potentially sensitive query parameters."""
    if not url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        # Keep only the query parameter 'q' (the search query)
        # Remove all other parameters
        query_params = urllib.parse.parse_qs(parsed.query)
        sanitized_params = {}
        if 'q' in query_params:
            sanitized_params['q'] = query_params['q']
        sanitized_query = urllib.parse.urlencode(sanitized_params, doseq=True)
        return parsed._replace(query=sanitized_query).geturl()
    except Exception:
        # If parsing fails, return a sanitized version
        return re.sub(r'([?&])[^=&]+=[^=&]+', r'\1***', url)


def classify_response(status_code, headers, body, url):
    """Classify the type of response received from Google."""
    
    # Check for redirect to sorry page
    if url and ('sorry.google.com' in url or '/sorry/' in url):
        return "CAPTCHA/SORRY_PAGE"
    
    # Check Location header for redirects
    location = headers.get('Location', headers.get('location', ''))
    if location and ('sorry.google.com' in location or '/sorry/' in location):
        return "CAPTCHA/SORRY_PAGE"
    
    # Check status codes
    if status_code == 429:
        return "HTTP_429_RATE_LIMIT"
    if status_code == 403:
        return "HTTP_403_FORBIDDEN"
    if status_code == 400:
        return "HTTP_400_BAD_REQUEST"
    if status_code >= 500:
        return "HTTP_5XX_SERVER_ERROR"
    
    # Check body for CAPTCHA/consent indicators
    body_lower = body.lower()
    if any(x in body_lower for x in ['captcha', 'recaptcha', 'verify you are human', 'i\'m not a robot']):
        return "CAPTCHA_PAGE"
    if any(x in body_lower for x in ['consent.google', 'before you continue', 'accept all', 'cookie consent']):
        return "CONSENT_PAGE"
    if any(x in body_lower for x in ['access denied', 'bot detection', 'automated queries']):
        return "BOT_PROTECTION"
    
    # Check for interstitial pages
    if any(x in body_lower for x in ['our systems have detected unusual traffic', 
                                       'please verify', 
                                       'temporarily blocked']):
        return "INTERSTITIAL_BLOCK"
    
    # Check for rate limit indicators in body
    if any(x in body_lower for x in ['too many requests', 'rate limit', 'slow down']):
        return "RATE_LIMIT_PAGE"
    
    # Check if it looks like WML/XML results
    if body.lstrip().startswith('<?xml') or '<wml>' in body_lower:
        return "WML_RESULTS"
    
    # Check for HTML results
    if '<html' in body_lower or '<!doctype html' in body_lower:
        # Check for Google search result markers
        if any(x in body_lower for x in ['<div class="', 'search results', 'g ', 'rc']):
            return "HTML_RESULTS"
        return "HTML_PAGE"
    
    return "UNKNOWN"


def test_dns(hostname):
    """Test DNS resolution for a hostname."""
    result = {"status": "ok", "addresses": [], "error": None}
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for family, type_, proto, canonname, sockaddr in addr_info:
            result["addresses"].append(sockaddr[0])
        result["addresses"] = list(set(result["addresses"]))
    except socket.gaierror as e:
        result["status"] = "failed"
        result["error"] = str(e)
    return result


def test_tcp_tls(hostname, port=443):
    """Test TCP connectivity and TLS handshake."""
    result = {"status": "ok", "tcp": False, "tls": False, "error": None}
    
    try:
        # Test TCP
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((hostname, port))
        result["tcp"] = True
        
        # Test TLS
        context = ssl.create_default_context()
        tls_sock = context.wrap_socket(sock, server_hostname=hostname)
        result["tls"] = True
        tls_sock.close()
        sock.close()
        
    except socket.timeout:
        result["status"] = "failed"
        result["error"] = "Connection timed out"
    except ConnectionRefusedError:
        result["status"] = "failed"
        result["error"] = "Connection refused"
    except ssl.SSLError as e:
        result["status"] = "partial"
        result["tcp"] = True
        result["error"] = f"TLS handshake failed: {e}"
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
    
    return result


def build_google_url_and_params(query):
    """Build Google request URL and parameters matching the engine's behavior."""
    # Create a minimal OnlineParams-like dict
    params = {
        "query": query,
        "pageno": 1,
        "time_range": None,
        "safesearch": 0,
        "searxng_locale": "en",
        "language": None,
        "categories": None,
        "engines": None,
        "searxng_user": None,
        "url": None,
        "headers": {},
        "cookies": {},
    }
    
    # Create EngineTraits for google
    traits = EngineTraits()
    traits.all_locale = "ZZ"
    
    # Get google info
    google_info = get_google_info(params, traits)
    
    # Build URL
    start = (params["pageno"] - 1) * 10
    args = {
        "q": query,
        "sca_esv": "1",
        **google_info["params"],
    }
    if start:
        args["start"] = start
    
    url = f"https://www.google.com/wml/search?{urllib.parse.urlencode(args)}"
    
    # Set headers
    params["headers"]["User-Agent"] = random.choice(nokia_useragents)
    params["headers"]["Accept"] = "*/*"
    params["headers"]["Accept-Language"] = "en-US,en;q=0.5"
    params["cookies"]["CONSENT"] = "YES+"
    
    return url, params["headers"], params["cookies"]


def run_google_parser(dom, body):
    """Run the actual SearXNG Google parser on a response."""
    results = EngineResults()
    
    parser_results = {
        "result_nodes_count": 0,
        "parsed_results_count": 0,
        "titles_found": 0,
        "urls_found": 0,
        "content_found": 0,
        "first_3_results": [],
        "parser_errors": [],
    }
    
    try:
        # Count result nodes - using the actual XPath from google.py
        result_nodes = eval_xpath_list(dom, '//div[contains(@class, "zMzFAb")]')
        parser_results["result_nodes_count"] = len(result_nodes)
        
        # Parse each result
        for result in result_nodes:
            try:
                title_tag = eval_xpath_getindex(
                    result, './/a[contains(@class, "fuLhoc")]//span[contains(@class, "CVA68e")]', 0, default=None
                )
                if title_tag is None:
                    continue
                title = extract_text(title_tag)
                parser_results["titles_found"] += 1
                
                raw_url = eval_xpath_getindex(result, './/a[contains(@class, "fuLhoc")]/@href', 0, default=None)
                if raw_url is None:
                    continue
                parser_results["urls_found"] += 1
                url = unwrap_google_url(raw_url)
                
                content_elem = eval_xpath(result, './/div[contains(@class, "taTFJ")]//span[contains(@class, "FrIlee")]')
                content = extract_text(content_elem) if content_elem else ""
                parser_results["content_found"] += 1
                
                thumbnail = eval_xpath_getindex(result, './/img[contains(@src, "encrypted-tbn")]/@src', 0, default=None)
                
                # Store first 3 results
                if len(parser_results["first_3_results"]) < 3:
                    parser_results["first_3_results"].append({
                        "title": title or "",
                        "url": url or "",
                        "content": content or "",
                        "thumbnail": thumbnail or "",
                    })
                
                results.add(
                    results.types.MainResult(
                        url=url,
                        title=title or "",
                        content=content or "",
                        thumbnail=thumbnail or "",
                    )
                )
                parser_results["parsed_results_count"] += 1
                
            except Exception as e:
                parser_results["parser_errors"].append(str(e))
        
    except Exception as e:
        parser_results["parser_errors"].append(f"Parser crash: {e}")
    
    return parser_results


def get_dom_structure_sample(dom, body):
    """Extract DOM structure around first potential result for analysis."""
    structure = {}
    
    try:
        # Get first div that might be a result
        potential_results = eval_xpath_list(dom, '//div')
        if potential_results:
            first_div = potential_results[0]
            structure["first_div_classes"] = first_div.get('class', '')
            structure["first_div_id"] = first_div.get('id', '')
            
            # Get children info
            children = list(first_div)
            structure["first_div_children_count"] = len(children)
            structure["first_div_children_tags"] = [child.tag for child in children[:10]]
            
            # Try to find anchor tags
            anchors = eval_xpath_list(first_div, './/a')
            if anchors:
                structure["first_anchor_href"] = anchors[0].get('href', '')[:200]
                structure["first_anchor_classes"] = anchors[0].get('class', '')
    except Exception as e:
        structure["error"] = str(e)
    
    return structure


# ---------------------------------------------------------------------------
# Main diagnostic
# ---------------------------------------------------------------------------

def run_diagnostic():
    """Run the complete Google diagnostic."""
    
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "test_query": TEST_QUERY,
        "searxng_import_ok": HAVE_SEARXNG,
        "import_error": IMPORT_ERROR if not HAVE_SEARXNG else None,
    }
    
    # Step 0: Check if SearXNG is importable
    if not HAVE_SEARXNG:
        print("=" * 80)
        print("DIAGNOSTIC FAILED: Cannot import SearXNG")
        print("=" * 80)
        print(f"Import error: {IMPORT_ERROR}")
        print()
        print("CLASSIFICATION: G. Other (SearXNG import failure)")
        print("=" * 80)
        return report
    
    # Step 1: DNS resolution
    print("[1/7] Testing DNS resolution for www.google.com...")
    dns_result = test_dns("www.google.com")
    report["dns"] = dns_result
    
    if dns_result["status"] == "failed":
        print(f"  DNS FAILED: {dns_result['error']}")
    else:
        print(f"  DNS OK: {dns_result['addresses']}")
    
    # Step 2: TCP/TLS connectivity
    print("[2/7] Testing TCP/TLS connectivity to www.google.com:443...")
    tls_result = test_tcp_tls("www.google.com", 443)
    report["tcp_tls"] = tls_result
    
    if tls_result["status"] == "failed":
        print(f"  TCP/TLS FAILED: {tls_result['error']}")
    elif tls_result["status"] == "partial":
        print(f"  TCP/TLS PARTIAL: {tls_result['error']}")
    else:
        print(f"  TCP/TLS OK: TCP={tls_result['tcp']}, TLS={tls_result['tls']}")
    
    # Step 3: Build Google request
    print("[3/7] Building Google request...")
    
    try:
        request_url, request_headers, request_cookies = build_google_url_and_params(TEST_QUERY)
        
        report["request"] = {
            "url": sanitize_url(request_url),
            "headers": {k: v for k, v in request_headers.items() if k.lower() not in ['authorization', 'cookie', 'x-api-key']},
            "cookies": {k: "[REDACTED]" if k.lower() in ['authorization', 'session'] else v for k, v in request_cookies.items()},
            "user_agent": request_headers.get("User-Agent", "NOT SET"),
        }
        
        print(f"  Request URL: {report['request']['url']}")
        print(f"  User-Agent: {report['request']['user_agent']}")
        
    except Exception as e:
        report["request_build_error"] = str(e)
        print(f"  Request build FAILED: {e}")
        print()
        print("=" * 80)
        print("CLASSIFICATION: G. Other (Request build failure)")
        print("=" * 80)
        return report
    
    # Step 4: Make the actual HTTP request
    print("[4/7] Sending HTTP request to Google...")
    
    start_time = time.time()
    exception_occurred = None
    http_status = None
    http_headers = {}
    http_body = b""
    
    try:
        # Use SearXNG's network layer
        response = searx_get(
            report["request"]["url"],
            headers=report["request"]["headers"],
            cookies=request_cookies,
            timeout=REQUEST_TIMEOUT,
            impersonate="chrome99_android",
        )
        
        http_status = response.status_code
        http_headers = dict(response.headers)
        http_body = response.content
        
    except Exception as e:
        exception_occurred = str(e)
        exception_type = type(e).__name__
        report["request_exception"] = {
            "type": exception_type,
            "message": exception_occurred,
        }
    
    request_duration = time.time() - start_time
    report["request_duration"] = round(request_duration, 3)
    
    # Step 5: Analyze response
    print("[5/7] Analyzing response...")
    
    response_info = {
        "status_code": http_status,
        "headers": {},
        "body_size": len(http_body) if http_body else 0,
        "body_preview": "",
    }
    
    if http_status:
        # Capture relevant headers
        relevant_headers = ['Retry-After', 'Location', 'Content-Type', 'Server', 
                           'X-Robots-Tag', 'Cache-Control', 'Set-Cookie',
                           'Alt-Svc', 'Date', 'Content-Length']
        for h in relevant_headers:
            if h in http_headers:
                response_info["headers"][h] = http_headers[h]
            elif h.lower() in http_headers:
                response_info["headers"][h] = http_headers[h.lower()]
        
        # Capture body preview
        body_text = http_body.decode('utf-8', errors='replace') if http_body else ""
        response_info["body_preview"] = body_text[:MAX_BODY_DUMP]
        response_info["body_encoding"] = "utf-8"
        
        # Classify response
        response_classification = classify_response(
            http_status, 
            {k.lower(): v for k, v in http_headers.items()}, 
            body_text,
            report["request"]["url"]
        )
        response_info["classification"] = response_classification
        
        print(f"  HTTP Status: {http_status}")
        print(f"  Body size: {response_info['body_size']} bytes")
        print(f"  Classification: {response_classification}")
        print(f"  Content-Type: {response_info['headers'].get('Content-Type', 'N/A')}")
        
    elif exception_occurred:
        response_info["error"] = exception_occurred
        response_info["exception_type"] = report.get("request_exception", {}).get("type", "Unknown")
        print(f"  Request FAILED: {exception_occurred}")
    
    report["response"] = response_info
    
    # Step 6: If valid response, run parser
    print("[6/7] Running Google parser on response...")
    
    parser_results = {
        "ran": False,
        "error": None,
    }
    
    body_text = http_body.decode('utf-8', errors='replace') if http_body else ""
    
    # Check if we should try to parse
    should_parse = False
    if http_status and http_status in (200, 201, 202):
        should_parse = True
    elif http_status and http_status in (301, 302, 303, 307, 308):
        # Followed redirect, check if we have body
        should_parse = True
    
    if should_parse and body_text:
        try:
            dom = html.fromstring(body_text)
            parser_results = run_google_parser(dom, body_text)
            parser_results["ran"] = True
            
            # Also get DOM structure sample
            parser_results["dom_structure"] = get_dom_structure_sample(dom, body_text)
            
        except Exception as e:
            parser_results["error"] = str(e)
            parser_results["ran"] = False
    
    report["parser"] = parser_results
    
    if parser_results.get("ran"):
        print(f"  Parser ran successfully")
        print(f"  Result nodes found: {parser_results.get('result_nodes_count', 0)}")
        print(f"  Parsed results: {parser_results.get('parsed_results_count', 0)}")
        print(f"  Titles: {parser_results.get('titles_found', 0)}, URLs: {parser_results.get('urls_found', 0)}, Content: {parser_results.get('content_found', 0)}")
    elif parser_results.get("error"):
        print(f"  Parser FAILED: {parser_results['error']}")
    else:
        print(f"  Parser NOT RUN (status={http_status})")
    
    # Step 7: Final classification
    print("[7/7] Determining final classification...")
    
    final_classification = "G. Other"
    reason = ""
    
    # Check DNS failure
    if report["dns"]["status"] == "failed":
        final_classification = "A. DNS/network failure"
        reason = f"DNS resolution failed: {report['dns']['error']}"
    
    # Check TCP/TLS failure
    elif report["tcp_tls"]["status"] == "failed":
        final_classification = "B. TCP/TLS failure"
        reason = f"TCP/TLS failed: {report['tcp_tls']['error']}"
    
    # Check request exception
    elif report.get("request_exception"):
        exc = report["request_exception"]
        exc_msg = exc.get("message", "").lower()
        exc_type = exc.get("type", "")
        
        if "timeout" in exc_msg or "timed out" in exc_msg:
            final_classification = "B. TCP/TLS failure"
            reason = f"Request timed out: {exc['message']}"
        elif "connection" in exc_msg:
            final_classification = "B. TCP/TLS failure"
            reason = f"Connection error: {exc['message']}"
        elif "403" in exc_msg or "forbidden" in exc_msg or "access denied" in exc_msg:
            final_classification = "D. HTTP 403/bot protection"
            reason = f"Google returned HTTP 403: {exc['message']}. This failure occurs BEFORE SearXNG's XPath extraction. Changing XPath selectors cannot fix this."
        elif "429" in exc_msg or "rate limit" in exc_msg or "too many" in exc_msg:
            final_classification = "C. HTTP 429/rate limiting"
            reason = f"Google returned HTTP 429: {exc['message']}. This failure occurs BEFORE SearXNG's XPath extraction. Changing XPath selectors cannot fix this."
        elif "captcha" in exc_msg or "sorry" in exc_msg:
            final_classification = "E. CAPTCHA/consent/interstitial"
            reason = f"Google CAPTCHA/sorry: {exc['message']}. This failure occurs BEFORE SearXNG's XPath extraction."
        else:
            final_classification = "G. Other"
            reason = f"Request exception: {exc_type}: {exc['message']}"
    
    # Check HTTP status codes
    elif http_status == 429:
        final_classification = "C. HTTP 429/rate limiting"
        reason = f"Google returned HTTP 429"
        retry_after = response_info["headers"].get("Retry-After", "not specified")
        if retry_after:
            reason += f" (Retry-After: {retry_after})"
        reason += ". This failure occurs BEFORE SearXNG's XPath extraction. Changing XPath selectors cannot fix this."
    
    elif http_status == 403:
        final_classification = "D. HTTP 403/bot protection"
        reason = f"Google returned HTTP 403"
        location = response_info["headers"].get("Location", "")
        if location:
            reason += f" (Location: {location})"
        reason += ". This failure occurs BEFORE SearXNG's XPath extraction. Changing XPath selectors cannot fix this."
    
    elif http_status in (301, 302, 303, 307, 308):
        location = response_info["headers"].get("Location", "")
        if location and ("sorry" in location.lower() or "captcha" in location.lower()):
            final_classification = "E. CAPTCHA/consent/interstitial"
            reason = f"Redirect to: {location}. This failure occurs BEFORE SearXNG's XPath extraction."
        else:
            final_classification = "E. CAPTCHA/consent/interstitial"
            reason = f"Redirect detected (Location: {location}). This may be a CAPTCHA or consent page."
    
    elif response_info.get("classification") == "CAPTCHA/SORRY_PAGE":
        final_classification = "E. CAPTCHA/consent/interstitial"
        reason = "Response is a Google CAPTCHA/sorry page. This failure occurs BEFORE SearXNG's XPath extraction."
    
    elif response_info.get("classification") == "CAPTCHA_PAGE":
        final_classification = "E. CAPTCHA/consent/interstitial"
        reason = "Response contains CAPTCHA elements. This failure occurs BEFORE SearXNG's XPath extraction."
    
    elif response_info.get("classification") == "CONSENT_PAGE":
        final_classification = "E. CAPTCHA/consent/interstitial"
        reason = "Response is a Google consent page. This failure occurs BEFORE SearXNG's XPath extraction."
    
    elif response_info.get("classification") == "INTERSTITIAL_BLOCK":
        final_classification = "E. CAPTCHA/consent/interstitial"
        reason = "Response is a Google interstitial/block page. This failure occurs BEFORE SearXNG's XPath extraction."
    
    elif response_info.get("classification") == "BOT_PROTECTION":
        final_classification = "D. HTTP 403/bot protection"
        reason = "Response indicates bot protection. This failure occurs BEFORE SearXNG's XPath extraction."
    
    elif response_info.get("classification") == "RATE_LIMIT_PAGE":
        final_classification = "C. HTTP 429/rate limiting"
        reason = "Response indicates rate limiting. This failure occurs BEFORE SearXNG's XPath extraction."
    
    # Check if parser ran but found no results
    elif parser_results.get("ran") and parser_results.get("result_nodes_count", 0) == 0:
        final_classification = "F. Valid Google response but parser failure"
        reason = f"Parser found 0 result nodes. DOM structure sample: {json.dumps(parser_results.get('dom_structure', {}), indent=2)}"
        # Add XPath analysis
        reason += "\n\nXPath expressions tested:\n"
        reason += "  Result container: //div[contains(@class, \"zMzFAb\")]\n"
        reason += "  Title: .//a[contains(@class, \"fuLhoc\")]//span[contains(@class, \"CVA68e\")]\n"
        reason += "  URL: .//a[contains(@class, \"fuLhoc\")]/@href\n"
        reason += "  Content: .//div[contains(@class, \"taTFJ\")]//span[contains(@class, \"FrIlee\")]\n"
        reason += "\nThe DOM structure does not match the expected WML layout. Parser needs updating."
    
    elif parser_results.get("ran") and parser_results.get("parsed_results_count", 0) == 0:
        final_classification = "F. Valid Google response but parser failure"
        reason = f"Parser found {parser_results.get('result_nodes_count', 0)} result nodes but 0 parsed results."
        reason += f" Titles: {parser_results.get('titles_found', 0)}, URLs: {parser_results.get('urls_found', 0)}, Content: {parser_results.get('content_found', 0)}"
        reason += "\nParser errors: " + json.dumps(parser_results.get('parser_errors', []), indent=2)
    
    elif parser_results.get("ran") and parser_results.get("parsed_results_count", 0) > 0:
        final_classification = "SUCCESS"
        reason = f"Google search working. Parsed {parser_results.get('parsed_results_count')} results."
        reason += f" First result: {json.dumps(parser_results.get('first_3_results', [{}])[0], indent=2)}"
    
    # Default
    elif http_status and http_status >= 200 and http_status < 300:
        final_classification = "F. Valid Google response but parser failure"
        reason = f"HTTP {http_status} received but parser did not run. Response classification: {response_info.get('classification', 'UNKNOWN')}"
    
    else:
        final_classification = "G. Other"
        reason = f"Unexpected state. HTTP status: {http_status}, Parser ran: {parser_results.get('ran')}"
    
    report["classification"] = final_classification
    report["classification_reason"] = reason
    
    # Print final report
    print()
    print("=" * 80)
    print("DIAGNOSTIC REPORT")
    print("=" * 80)
    print(json.dumps(report, indent=2, default=str, ensure_ascii=False))
    print()
    print("=" * 80)
    print(f"FINAL CLASSIFICATION: {final_classification}")
    print("=" * 80)
    print(reason)
    print("=" * 80)
    
    return report


if __name__ == "__main__":
    run_diagnostic()
