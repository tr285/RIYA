"""RIYA browser interaction package (offline-first, SSRF & prompt-injection fenced)."""

from core.browser.adapter import (
    BrowserController,
    BrowserSecurityError,
    ParsedWebPage,
    browser_extract_links,
    browser_fetch_url,
    browser_inspect_page,
    browser_open_local,
    browser_submit_form,
    format_untrusted_page,
    parse_html_safely,
    validate_public_http_url,
)

__all__ = [
    "BrowserController",
    "BrowserSecurityError",
    "ParsedWebPage",
    "browser_extract_links",
    "browser_fetch_url",
    "browser_inspect_page",
    "browser_open_local",
    "browser_submit_form",
    "format_untrusted_page",
    "parse_html_safely",
    "validate_public_http_url",
]
