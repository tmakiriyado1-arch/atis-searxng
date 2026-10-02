# SPDX-License-Identifier: AGPL-3.0-or-later
"""This is the implementation of the Google WEB engine.  Some of this
implementations (manly the :py:obj:`get_google_info`) are shared by other
engines:

- :ref:`google images engine`
- :ref:`google news engine`
- :ref:`google videos engine`
- :ref:`google scholar engine`
- :ref:`google autocomplete`

This implementation uses Playwright for browser-based scraping since Google
requires JavaScript to render search results. The WML endpoint is deprecated
and blocked by Google's bot detection.

For environments without Playwright (e.g., some hosted instances), the engine
will fall back to HTTP requests with Chrome impersonation, but this is less
reliable as Google may still block automated requests.
"""

import random
import typing as t
from urllib.parse import unquote, urlencode

import babel
import babel.core
import babel.languages
from lxml import html

from searx.enginelib.traits import EngineTraits
from searx.exceptions import SearxEngineCaptchaException, SearxEngineAccessDeniedException
from searx import logger
from searx.locales import get_official_locales, language_tag, region_tag
from searx.result_types import EngineResults
from searx.utils import (
    eval_xpath,
    eval_xpath_getindex,
    eval_xpath_list,
    extract_text,
)

from searx.enginelib.google_transport import GoogleTransport, GoogleResponseClassification

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

_transport = GoogleTransport(use_browser_fallback=True)

# Track if we should force browser mode due to repeated failures
_force_browser_mode = False
_browser_failure_count = 0
_max_browser_failures_before_http = 3

# Default traits for Google engine (used when not provided)
# This is created lazily to avoid circular imports
traits: EngineTraits | None = None


def _get_traits() -> EngineTraits:
    """Get or create the default EngineTraits for Google."""
    global traits
    if traits is None:
        traits = EngineTraits()
        traits.all_locale = "ZZ"
    return traits


def reset_browser_failure_tracking() -> None:
    """Reset browser failure tracking. Useful for testing or when issues are resolved."""
    global _force_browser_mode, _browser_failure_count
    _force_browser_mode = False
    _browser_failure_count = 0
    logger.info("[GOOGLE] Browser failure tracking reset")

about = {
    "website": "https://www.google.com",
    "wikidata_id": "Q9366",
    "official_api_documentation": "https://developers.google.com/custom-search/",
    "use_official_api": False,
    "require_api_key": False,
    "results": "XML",
}

# engine dependent config
categories = ["general", "web"]
paging = True
max_page = 50
"""Google supports up to 50 pages of results, see the `Google max_page discussion`_.

.. _Google max_page discussion: https://github.com/searxng/searxng/issues/2982
"""
time_range_support = True
language_support = True
safesearch = True

time_range_dict = {"day": "d", "week": "w", "month": "m", "year": "y"}

# Filter results. 0: None, 1: Moderate, 2: Strict
filter_mapping = {0: "off", 1: "medium", 2: "high"}

# https://github.com/searxng/searxng/issues/6359
nokia_useragents = (
    "Nokia7610/2.0 (5.0509.0) SymbianOS/7.0s Series60/2.1 Profile/MIDP-2.0 Configuration/CLDC-1.0",
    "Nokia7610/2.0 (7.0642.0) SymbianOS/7.0s Series60/2.1 Profile/MIDP-2.0 Configuration/CLDC-1.0",
    "Nokia6230/2.0 (05.50) Profile/MIDP-2.0 Configuration/CLDC-1.1",
    "Nokia6230i/2.0 (03.80) Profile/MIDP-2.0 Configuration/CLDC-1.1",
    "Nokia6280/2.0 (03.60) Profile/MIDP-2.0 Configuration/CLDC-1.1",
    "NokiaN72/2.0617.1.0.3 Series60/2.8 Profile/MIDP-2.0 Configuration/CLDC-1.1",
)


# specific xpath variables
# ------------------------

# Suggestions are links placed in a *card-section*, we extract only the text
# from the links not the links itself.
suggestion_xpath = '//table[contains(@class, "HExoMb")]//a[contains(@class, "ZWRArf")]'


def get_google_info(params: "OnlineParams", eng_traits: EngineTraits) -> dict[str, t.Any]:
    """Composing various (language) properties for the google engines (:ref:`google
    API`).

    This function is called by the various google engines (:ref:`google web
    engine`, :ref:`google images engine`, :ref:`google news engine` and
    :ref:`google videos engine`).

    :param dict param: Request parameters of the engine.  At least
        a ``searxng_locale`` key should be in the dictionary.

    :param eng_traits: Engine's traits fetched from google preferences
        (:py:obj:`searx.enginelib.traits.EngineTraits`)

    :rtype: dict
    :returns:
        Py-Dictionary with the key/value pairs:

        language:
            The language code that is used by google (e.g. ``lang_en`` or
            ``lang_zh-TW``)

        country:
            The country code that is used by google (e.g. ``US`` or ``TW``)

        locale:
            A instance of :py:obj:`babel.core.Locale` build from the
            ``searxng_locale`` value.

        params:
            Py-Dictionary with additional request arguments (can be passed to
            :py:func:`urllib.parse.urlencode`).

            - ``hl`` parameter: specifies the interface language of user interface.
            - ``ie`` parameter: sets the character encoding scheme that should
              be used to interpret the query string ('utf8').
            - ``oe`` parameter: sets the character encoding scheme that should
              be used to decode the XML result ('utf8').

        headers:
            Py-Dictionary with additional HTTP headers (can be passed to
            request's headers)

            - ``Accept: '*/*``

    """

    ret_val: dict[str, t.Any] = {
        "language": None,
        "country": None,
        "params": {},
        "headers": {},
        "cookies": {},
        "locale": None,
    }

    sxng_locale = params.get("searxng_locale", "all")
    try:
        locale = babel.Locale.parse(sxng_locale, sep="-")
    except babel.core.UnknownLocaleError:
        locale = None

    eng_lang = eng_traits.get_language(sxng_locale) or "lang_en"
    lang_code = eng_lang.split("_")[-1]  # lang_zh-TW --> zh-TW / lang_en --> en
    country = eng_traits.get_region(sxng_locale, eng_traits.all_locale)

    # Test zh_hans & zh_hant --> in the topmost links in the result list of list
    # TW and HK you should a find wiktionary.org zh_hant link.  In the result
    # list of zh-CN should not be no hant link instead you should find
    # zh.m.wikipedia.org/zh somewhere in the top.

    # '!go 日 :zh-TW' --> https://zh.m.wiktionary.org/zh-hant/%E6%97%A5
    # '!go 日 :zh-CN' --> https://zh.m.wikipedia.org/zh/%E6%97%A5

    ret_val["language"] = eng_lang
    ret_val["country"] = country
    ret_val["locale"] = locale

    # hl parameter:
    #   The hl parameter specifies the interface language (host language) of
    #   your user interface. To improve the performance and the quality of your
    #   search results, you are strongly encouraged to set this parameter
    #   explicitly.
    #   https://developers.google.com/custom-search/docs/xml_results#hlsp
    # The Interface Language:
    #   https://developers.google.com/custom-search/docs/xml_results_appendices#interfaceLanguages

    # https://github.com/searxng/searxng/issues/2515#issuecomment-1607150817
    ret_val["params"]["hl"] = f"{lang_code}"

    # lr parameter:
    #   The lr (language restrict) parameter restricts search results to
    #   documents written in a particular language.
    #   https://developers.google.com/custom-search/docs/xml_results#lrsp
    #   Language Collection Values:
    #   https://developers.google.com/custom-search/docs/xml_results_appendices#languageCollections
    #
    # To select 'all' languages an empty 'lr' value is used.
    #
    # Different to other google services, Google Scholar supports to select more
    # than one language. The languages are separated by a pipe '|' (logical OR).
    # By example: &lr=lang_zh-TW%7Clang_de selects articles written in
    # traditional chinese OR german language.

    ret_val["params"]["lr"] = eng_lang
    if sxng_locale == "all":
        ret_val["params"]["lr"] = ""

    # cr parameter:
    #   The cr parameter restricts search results to documents originating in a
    #   particular country.
    #   https://developers.google.com/custom-search/docs/xml_results#crsp

    # specify a region (country) only if a region is given in the selected
    # locale --> https://github.com/searxng/searxng/issues/2672

    if country is not None:
        ret_val["params"]["cr"] = ""
        if len(sxng_locale.split("-")) > 1:
            ret_val["params"]["cr"] = "country" + country

    # gl parameter: (mandatory by Google News)
    #   The gl parameter value is a two-letter country code. For WebSearch
    #   results, the gl parameter boosts search results whose country of origin
    #   matches the parameter value. See the Country Codes section for a list of
    #   valid values.
    #   Specifying a gl parameter value in WebSearch requests should improve the
    #   relevance of results. This is particularly true for international
    #   customers and, even more specifically, for customers in English-speaking
    #   countries other than the United States.
    #   https://developers.google.com/custom-search/docs/xml_results#glsp

    # https://github.com/searxng/searxng/issues/2515#issuecomment-1606294635
    # ret_val['params']['gl'] = country

    # ie parameter:
    #   The ie parameter sets the character encoding scheme that should be used
    #   to interpret the query string. The default ie value is latin1.
    #   https://developers.google.com/custom-search/docs/xml_results#iesp

    ret_val["params"]["ie"] = "utf8"

    # oe parameter:
    #   The oe parameter sets the character encoding scheme that should be used
    #   to decode the XML result. The default oe value is latin1.
    #   https://developers.google.com/custom-search/docs/xml_results#oesp

    ret_val["params"]["oe"] = "utf8"

    # num parameter:
    #   The num parameter identifies the number of search results to return.
    #   The default num value is 10, and the maximum value is 20. If you request
    #   more than 20 results, only 20 results will be returned.
    #   https://developers.google.com/custom-search/docs/xml_results#numsp

    # HINT: seems to have no effect (tested in google WEB & Images)
    # ret_val['params']['num'] = 20

    # HTTP headers

    ret_val["headers"]["Accept"] = "*/*"

    # Cookies

    # - https://github.com/searxng/searxng/pull/1679#issuecomment-1235432746
    # - https://github.com/searxng/searxng/issues/1555
    ret_val["cookies"]["CONSENT"] = "YES+"

    return ret_val


def detect_google_sorry(resp: "SXNG_Response"):
    """Detect Google's bot-protection responses (CAPTCHA / sorry pages).

    Google may block requests in several ways:

    1. Redirect to sorry.google.com (standard CAPTCHA).
    2. HTTP 302 redirect to ``/sorry/index?...`` on the same host -- when the
       HTTP client doesn't follow the redirect, the response body is a short
       HTML stub with a link to the sorry page.
    3. Short HTML response (<2000 bytes) containing "/sorry/" -- a meta-refresh
       or JS redirect variant.
    4. HTTP 403 Forbidden with access denied / bot detection messages.
    """
    global _force_browser_mode, _browser_failure_count

    if resp.url.host == "sorry.google.com" or resp.url.path.startswith("/sorry"):
        raise SearxEngineCaptchaException()

    if resp.status_code == 302:
        raise SearxEngineCaptchaException()

    if len(resp.text) < 2000 and "/sorry/" in resp.text:
        raise SearxEngineCaptchaException()
    
    # Check for JavaScript requirement pages (Google returns these when JS is disabled)
    body_lower = resp.text.lower()
    if any(x in body_lower for x in ['enablejs', 'httpservice/retry/enablejs', 'enable javascript', 'please enable javascript', 'turn on javascript', 'javascript required']):
        # Force browser mode for future requests
        _force_browser_mode = True
        raise SearxEngineCaptchaException()
    
    # Check for access denied patterns in the response body
    if resp.status_code == 403 and any(x in body_lower for x in ['access denied', 'forbidden', 'permission']):
        # Force browser mode for future requests
        _force_browser_mode = True
        raise SearxEngineAccessDeniedException(suspended_time=3600, message="Google WML endpoint blocked, forcing browser mode")
    
    # If we get a 403 without clear CAPTCHA indicators, also force browser mode
    if resp.status_code == 403:
        _force_browser_mode = True
        raise SearxEngineAccessDeniedException(suspended_time=3600, message="Google returned 403, forcing browser mode")


def unwrap_google_url(raw_url: str) -> str:
    # remove redirector from url
    if raw_url.startswith("/url?q="):
        return unquote(raw_url[7:].split("&sa=U")[0])
    return raw_url


def wml_dom(resp: "SXNG_Response"):
    detect_google_sorry(resp)
    text = resp.text
    if text.lstrip().startswith("<?xml"):
        text = text.split("?>", 1)[-1]
    return html.fromstring(text)


def google_request(
    query: str,
    params: "OnlineParams",
    extra_args: dict[str, t.Any] | None = None,
    *,
    eng_traits: EngineTraits | None = None,
    use_time_range: bool = True,
    use_safesearch: bool = True,
    safesearch_map: dict[int, str] | None = None,
    use_locales: bool = True,
) -> None:
    google_info = get_google_info(params, eng_traits or _get_traits())
    if not use_locales:
        google_info["params"].pop("lr")
        google_info["params"].pop("cr")

    start = (params["pageno"] - 1) * 10
    args: dict[str, t.Any] = {
        "q": query,
        "sca_esv": "1",
        **google_info["params"],
        **(extra_args or {}),
    }
    if start:
        args["start"] = start
    if use_time_range and params["time_range"] in time_range_dict:
        args["tbs"] = "qdr:" + time_range_dict[params["time_range"]]
    if use_safesearch and params["safesearch"]:
        args["safe"] = (safesearch_map or filter_mapping)[params["safesearch"]]

    # Use standard Google search instead of deprecated WML endpoint
    # WML endpoint returns 403 consistently now
    params["url"] = f"https://www.google.com/search?{urlencode(args)}"
    # Use Chrome impersonation with modern User-Agent to bypass Google bot detection
    params["headers"]["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    params["impersonate"] = "chrome120"


def request(query: str, params: "OnlineParams") -> None:
    global _force_browser_mode, _browser_failure_count
    
    google_request(query, params)
    
    # If we're forcing browser mode due to repeated WML failures, skip HTTP
    if _force_browser_mode:
        logger.info("[GOOGLE] Forced browser mode active due to previous failures")
        # We'll let the response() function handle the browser-based request
        return
    
    # Use GoogleTransport to make the HTTP call
    transport_result = _transport.request(
        url=params["url"],
        headers=params["headers"],
        cookies=params.get("cookies"),
        timeout=params.get("timeout"),
        impersonate=params.get("impersonate"),
    )
    # Log the classification (safe, no sensitive data)
    logger.info(
        "[GOOGLE_TRANSPORT] classification=%s status=%s duration_ms=%.0f",
        transport_result.classification.value,
        transport_result.status_code,
        transport_result.elapsed_time * 1000,
    )
    # Store response for processor to use
    # If transport failed, we still let the processor try its own call
    if transport_result.response is not None:
        params["_transport_response"] = transport_result.response

        # Log whether browser fallback was used
        if transport_result.used_browser:
            logger.info(
                "[GOOGLE_TRANSPORT] Browser fallback used, classification=%s",
                transport_result.classification.value,
            )
    else:
        # Transport failed completely, mark for browser mode
        _browser_failure_count += 1
        if _browser_failure_count >= _max_browser_failures_before_http:
            logger.warning("[GOOGLE] Multiple transport failures, forcing browser mode")
            _force_browser_mode = True

def _parse_wml_results(dom) -> EngineResults:
    """Parse results from WML/old Google format."""
    results = EngineResults()
    
    # parse results using WML XPath selectors
    for result in eval_xpath_list(dom, '//div[contains(@class, "zMzFAb")]'):
        try:
            title_tag = eval_xpath_getindex(
                result, './/a[contains(@class, "fuLhoc")]//span[contains(@class, "CVA68e")]', 0, default=None
            )
            if title_tag is None:
                logger.debug("ignoring item from the result_xpath list: missing title")
                continue
            title = extract_text(title_tag)

            raw_url = eval_xpath_getindex(result, './/a[contains(@class, "fuLhoc")]/@href', 0, default=None)
            if raw_url is None:
                logger.debug(
                    'ignoring item from the result_xpath list: missing url of title "%s"',
                    title,
                )
                continue

            url = unwrap_google_url(raw_url)
            content = extract_text(
                eval_xpath(result, './/div[contains(@class, "taTFJ")]//span[contains(@class, "FrIlee")]')
            )
            thumbnail = eval_xpath_getindex(result, './/img[contains(@src, "encrypted-tbn")]/@src', 0, default=None)
            results.add(
                results.types.MainResult(
                    url=url,
                    title=title or "",
                    content=content or "",
                    thumbnail=thumbnail or "",
                )
            )

        except Exception as e:  # pylint: disable=broad-except
            logger.error(e, exc_info=True)
            continue

    # parse suggestion
    for suggestion in eval_xpath_list(dom, suggestion_xpath):
        results.add(results.types.LegacyResult(suggestion=extract_text(suggestion)))

    return results


def _parse_html_results(dom, resp_text: str) -> EngineResults:
    """Parse results from standard Google HTML (requires JavaScript rendering).
    
    Google's modern search results are dynamically generated with JavaScript.
    When using browser-based requests, we need different selectors.
    """
    results = EngineResults()
    
    # Try multiple XPath patterns for Google's dynamically generated results
    # Pattern 1: Look for result containers with data-hveid attribute (modern Google)
    result_nodes = eval_xpath_list(dom, '//div[@data-hveid]')
    
    # Pattern 2: Look for div.g class (traditional Google result class)
    if not result_nodes:
        result_nodes = eval_xpath_list(dom, '//div[contains(@class, "g") and contains(@class, "rc")]')
    
    # Pattern 3: Look for any div with jscontroller attribute
    if not result_nodes:
        result_nodes = eval_xpath_list(dom, '//div[@jscontroller]')
    
    # Pattern 4: Look for main result container
    if not result_nodes:
        main_container = eval_xpath_getindex(dom, '//div[@id="search"]//div[@id="main"]', 0, default=None)
        if main_container is not None:
            result_nodes = eval_xpath_list(main_container, './/div[contains(@class, "g")]')
    
    # Pattern 5: Look for result items in the center column
    if not result_nodes:
        center_col = eval_xpath_getindex(dom, '//div[@id="cnt"]', 0, default=None)
        if center_col is not None:
            result_nodes = eval_xpath_list(center_col, './/div[@class="mnr-cxt"]//div')
    
    logger.info("[GOOGLE_PARSER] Found %d result nodes with current selectors", len(result_nodes))
    
    # If we still have no results, check if the page contains error messages
    if not result_nodes:
        body_lower = resp_text.lower()
        if 'access denied' in body_lower or 'forbidden' in body_lower or '403' in body_lower:
            logger.warning("[GOOGLE_PARSER] Access denied page detected in HTML response")
            raise SearxEngineAccessDeniedException(suspended_time=3600, message="Google access denied")
        if 'captcha' in body_lower or 'verify' in body_lower:
            logger.warning("[GOOGLE_PARSER] CAPTCHA page detected in HTML response")
            raise SearxEngineCaptchaException()
        # Check for JavaScript requirement pages
        if any(x in body_lower for x in ['enablejs', 'httpservice/retry/enablejs', 'enable javascript', 'please enable javascript', 'turn on javascript', 'javascript required']):
            logger.warning("[GOOGLE_PARSER] JavaScript required page detected in HTML response")
            raise SearxEngineCaptchaException()
    
    for result in result_nodes:
        try:
            # Extract title - try multiple patterns
            title_tag = eval_xpath_getindex(result, './/h3', 0, default=None)
            if title_tag is None:
                title_tag = eval_xpath_getindex(result, './/div[@role="heading"]', 0, default=None)
            if title_tag is None:
                title_tag = eval_xpath_getindex(result, './/a', 0, default=None)
            
            if title_tag is None:
                continue
            
            title = extract_text(title_tag).strip()
            if not title:
                continue
            
            # Extract URL
            url_tag = eval_xpath_getindex(result, './/a[@href]', 0, default=None)
            if url_tag is None:
                continue
            
            raw_url = url_tag.get('href')
            if not raw_url:
                continue
            
            # Unwrap Google redirect URLs
            url = unwrap_google_url(raw_url)
            
            # Extract content/snippet
            content_tag = eval_xpath_getindex(result, './/div[contains(@class, "VwiC3b")]', 0, default=None)
            if content_tag is None:
                content_tag = eval_xpath_getindex(result, './/div[contains(@class, "IsZvec")]', 0, default=None)
            if content_tag is None:
                # Try to find any div with text that looks like a snippet
                divs = eval_xpath_list(result, './/div')
                for div in divs:
                    text = extract_text(div).strip()
                    if len(text) > 20 and len(text) < 500:  # Reasonable snippet length
                        content_tag = div
                        break
            
            content = extract_text(content_tag) if content_tag is not None else ""
            
            # Extract thumbnail if available
            img_tag = eval_xpath_getindex(result, './/img[@src]', 0, default=None)
            thumbnail = img_tag.get('src') if img_tag is not None else None
            
            results.add(
                results.types.MainResult(
                    url=url,
                    title=title or "",
                    content=content or "",
                    thumbnail=thumbnail or "",
                )
            )
            
        except Exception as e:  # pylint: disable=broad-except
            logger.error("[GOOGLE_PARSER] Error parsing result: %s", e, exc_info=True)
            continue
    
    return results


def response(resp: "SXNG_Response") -> EngineResults:
    global _force_browser_mode, _browser_failure_count
    
    # Check if we have a cached transport response
    if hasattr(resp, '_transport_response') and resp._transport_response is not None:
        # Use the transport response
        actual_resp = resp._transport_response
    else:
        actual_resp = resp
    
    try:
        # First try WML parsing (for backward compatibility)
        dom = wml_dom(actual_resp)
        results = _parse_wml_results(dom)
        
        # If we got results from WML parsing, return them
        if len(results.result_container) > 0:
            return results
        
        # WML parsing failed or returned no results
        # Try HTML parsing for standard Google results
        logger.info("[GOOGLE_PARSER] WML parsing returned no results, trying HTML parsing")
        dom = html.fromstring(actual_resp.text)
        results = _parse_html_results(dom, actual_resp.text)
        
        # If HTML parsing also failed and we're not in browser mode, force it
        if len(results.result_container) == 0 and not _force_browser_mode:
            _browser_failure_count += 1
            logger.warning(
                "[GOOGLE_PARSER] Both WML and HTML parsing failed, failure count: %d",
                _browser_failure_count
            )
            if _browser_failure_count >= _max_browser_failures_before_http:
                _force_browser_mode = True
                logger.warning("[GOOGLE] Forcing browser mode due to repeated parsing failures")
        
        return results
        
    except SearxEngineCaptchaException:
        _force_browser_mode = True
        raise
    except SearxEngineAccessDeniedException:
        _force_browser_mode = True
        raise
    except Exception as e:
        logger.error("[GOOGLE_PARSER] Error in response parsing: %s", e, exc_info=True)
        # Return empty results rather than crashing
        return EngineResults()


# get supported languages from their site


skip_countries = [
    # official language of google-country not in google-languages
    "AL",  # Albanien (sq)
    "AZ",  # Aserbaidschan  (az)
    "BD",  # Bangladesch (bn)
    "BN",  # Brunei Darussalam (ms)
    "BT",  # Bhutan (dz)
    "ET",  # Äthiopien (am)
    "GE",  # Georgien (ka, os)
    "GL",  # Grönland (kl)
    "KH",  # Kambodscha (km)
    "LA",  # Laos (lo)
    "LK",  # Sri Lanka (si, ta)
    "ME",  # Montenegro (sr)
    "MK",  # Nordmazedonien (mk, sq)
    "MM",  # Myanmar (my)
    "MN",  # Mongolei (mn)
    "MV",  # Malediven (dv) // dv_MV is unknown by babel
    "MY",  # Malaysia (ms)
    "NP",  # Nepal (ne)
    "TJ",  # Tadschikistan (tg)
    "TM",  # Turkmenistan (tk)
    "UZ",  # Usbekistan (uz)
]


def fetch_traits(engine_traits: EngineTraits):
    """Fetch languages from Google."""
    # pylint: disable=import-outside-toplevel, too-many-branches

    from searx.network import get  # see https://github.com/searxng/searxng/issues/762

    resp = get("https://www.google.com/preferences", timeout=5)
    if not resp.ok:
        raise RuntimeError("Response from Google preferences is not OK.")

    dom = html.fromstring(resp.text.replace('<?xml version="1.0" encoding="UTF-8"?>', ""))

    # supported language codes

    lang_map = {"no": "nb"}
    for x in eval_xpath_list(dom, "//select[@name='hl']/option"):
        eng_lang = x.get("value")
        try:
            locale = babel.Locale.parse(lang_map.get(eng_lang, eng_lang), sep="-")
        except babel.UnknownLocaleError:
            print("INFO:  google UI language %s (%s) is unknown by babel" % (eng_lang, x.text.split("(")[0].strip()))
            continue
        sxng_lang = language_tag(locale)

        conflict = engine_traits.languages.get(sxng_lang)
        if conflict:
            if conflict != eng_lang:
                print("CONFLICT: babel %s --> %s, %s" % (sxng_lang, conflict, eng_lang))
            continue
        engine_traits.languages[sxng_lang] = "lang_" + eng_lang

    # alias languages
    engine_traits.languages["zh"] = "lang_zh-CN"

    # supported region codes

    for x in eval_xpath_list(dom, "//select[@name='gl']/option"):
        eng_country = x.get("value")

        if eng_country in skip_countries:
            continue
        if eng_country == "ZZ":
            engine_traits.all_locale = "ZZ"
            continue

        sxng_locales = get_official_locales(eng_country, engine_traits.languages.keys(), regional=True)

        if not sxng_locales:
            print("ERROR: can't map from google country %s (%s) to a babel region." % (x.get("data-name"), eng_country))
            continue

        for sxng_locale in sxng_locales:
            engine_traits.regions[region_tag(sxng_locale)] = eng_country

    # alias regions
    engine_traits.regions["zh-CN"] = "HK"
