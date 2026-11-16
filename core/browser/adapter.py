"""Offline-first, SSRF-safe, prompt-injection-fenced browser interaction layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlencode
from urllib.request import Request, urlopen

from core.ai.safety import strip_unsafe_control_chars
from core.tools.filesystem.files import _path_context

MAX_LOCAL_HTML_BYTES = 262_144
MAX_REMOTE_BYTES = 262_144
MAX_TEXT_PREVIEW_CHARS = 6000
MAX_LINKS = 50
DEFAULT_HTTP_TIMEOUT_SECONDS = 8.0

_ALLOWED_LOCAL_EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".txt", ".md"})
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "session",
        "csrf",
        "pin",
        "otp",
        "cvv",
        "cvc",
        "card_number",
        "ssn",
    }
)
_CAPTCHA_MARKERS = (
    "recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "verify you are human",
    "are you human",
    "captcha",
    "attention required! | cloudflare",
    "bot verification",
)


class BrowserSecurityError(PermissionError):
    """Raised when a browser target or action violates offline, SSRF, or security rules."""


@dataclass(frozen=True)
class ParsedWebPage:
    source: str
    title: str
    text: str
    links: list[dict[str, str]] = field(default_factory=list)
    forms: list[dict[str, Any]] = field(default_factory=list)
    captcha_detected: bool = False
    auth_wall_detected: bool = False
    warnings: list[str] = field(default_factory=list)


class _SafeHTMLInspector(HTMLParser):
    """Parse title, visible text, links, and form metadata while stripping scripts/styles."""

    def __init__(self, base_url: str = ""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._skip_depth = 0
        self._in_title = False
        self._in_link = False
        self._current_href = ""
        self._current_link_text: list[str] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self._current_form: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        attr_map = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag_l in {"script", "style", "noscript", "template"}:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if tag_l == "title":
            self._in_title = True
        elif tag_l == "a":
            href = attr_map.get("href", "").strip()
            if href and not href.lower().startswith(("javascript:", "data:", "vbscript:")):
                resolved = urljoin(self.base_url, href) if self.base_url else href
                self._in_link = True
                self._current_href = resolved
                self._current_link_text = []
        elif tag_l == "form":
            self._current_form = {
                "action": urljoin(self.base_url, attr_map.get("action", ""))
                if self.base_url
                else attr_map.get("action", ""),
                "method": attr_map.get("method", "get").upper(),
                "fields": [],
            }
        elif tag_l in {"input", "textarea", "select"} and self._current_form is not None:
            field_name = attr_map.get("name", "") or attr_map.get("id", "")
            field_type = attr_map.get("type", "text").lower()
            if field_name:
                self._current_form["fields"].append(
                    {
                        "name": field_name,
                        "type": field_type,
                        "sensitive": field_type == "password"
                        or any(tok in field_name.lower() for tok in _SENSITIVE_FIELD_NAMES),
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in {"script", "style", "noscript", "template"}:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if tag_l == "title":
            self._in_title = False
        elif tag_l == "a" and self._in_link:
            label = " ".join("".join(self._current_link_text).split())
            if len(self.links) < MAX_LINKS:
                self.links.append({"text": label[:160], "href": self._current_href[:512]})
            self._in_link = False
            self._current_href = ""
            self._current_link_text = []
        elif tag_l == "form" and self._current_form is not None:
            self.forms.append(self._current_form)
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._in_link:
            self._current_link_text.append(cleaned)
        self.text_parts.append(cleaned)


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_public_http_url(url: str) -> str:
    """Validate URL scheme and reject SSRF targets (localhost, metadata, private IPs)."""
    if not isinstance(url, str) or not url.strip():
        raise BrowserSecurityError("URL must be a non-empty string.")
    cleaned = strip_unsafe_control_chars(url, allow_newlines=False).strip()
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise BrowserSecurityError(
            f"Unsupported URL scheme '{scheme or 'none'}'; only http/https are permitted for web targets."
        )
    if parsed.username or parsed.password:
        raise BrowserSecurityError("Embedded URL credentials are not permitted.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise BrowserSecurityError("URL must include a hostname.")
    if host in {"localhost", "localhost.localdomain", "0.0.0.0"} or host.endswith(
        (".local", ".internal", ".localhost")
    ):
        raise BrowserSecurityError(f"Blocked internal/loopback hostname: '{host}'.")

    # Literal IP check
    try:
        ip_literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip_literal = None
    if ip_literal is not None and _is_forbidden_ip(ip_literal):
        raise BrowserSecurityError(f"Blocked private or loopback IP address: {host}")

    return cleaned


def _verify_resolved_host_is_public(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip()
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BrowserSecurityError(
            f"Unable to resolve external hostname '{host}' (RIYA is offline or host unavailable)."
        ) from exc

    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = str(sockaddr[0]).split("%", 1)[0]
        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if _is_forbidden_ip(ip_obj):
            raise BrowserSecurityError(
                f"SSRF guard blocked resolution of '{host}' to private/local IP {ip_text}."
            )


def _detect_anti_bot_or_auth_wall(raw_html: str, parsed_text: str) -> tuple[bool, bool]:
    combined = (raw_html[:32000] + "\n" + parsed_text[:8000]).lower()
    captcha = any(marker in combined for marker in _CAPTCHA_MARKERS)
    auth_wall = 'type="password"' in combined or "type='password'" in combined
    return captcha, auth_wall


def parse_html_safely(raw_html: str, *, source: str, base_url: str = "") -> ParsedWebPage:
    inspector = _SafeHTMLInspector(base_url=base_url)
    inspector.feed(raw_html)
    inspector.close()

    title = strip_unsafe_control_chars(" ".join(inspector.title_parts), allow_newlines=False).strip()
    raw_text = " ".join(inspector.text_parts)
    text = strip_unsafe_control_chars(raw_text, allow_newlines=False).strip()
    if len(text) > MAX_TEXT_PREVIEW_CHARS:
        text = text[:MAX_TEXT_PREVIEW_CHARS] + "...[truncated]"

    captcha, auth_wall = _detect_anti_bot_or_auth_wall(raw_html, text)
    warnings: list[str] = []
    if captcha:
        warnings.append("CAPTCHA or anti-bot gate detected; human action required.")
    if auth_wall:
        warnings.append("Authentication/password form detected; RIYA will not auto-authenticate.")

    return ParsedWebPage(
        source=source,
        title=title,
        text=text,
        links=inspector.links,
        forms=inspector.forms,
        captcha_detected=captcha,
        auth_wall_detected=auth_wall,
        warnings=warnings,
    )


def format_untrusted_page(page: ParsedWebPage) -> str:
    """Wrap inspected page data in explicit untrusted-data boundaries."""
    payload = {
        "source": page.source,
        "title": page.title,
        "warnings": page.warnings,
        "link_count": len(page.links),
        "form_count": len(page.forms),
        "forms": page.forms,
        "text_excerpt": page.text,
    }
    return (
        f"<untrusted_page_content source=\"{page.source}\">\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "</untrusted_page_content>"
    )


class BrowserController:
    """Offline-first modular browser interaction controller."""

    def __init__(
        self,
        *,
        allow_online: bool = False,
        allowed_domains: tuple[str, ...] | list[str] | None = None,
    ):
        self.allow_online = bool(allow_online)
        self.allowed_domains = frozenset(
            d.strip().lower() for d in (allowed_domains or ()) if str(d).strip()
        )
        self._last_local_path: str | None = None

    def open_local_page(self, path: str) -> ParsedWebPage:
        ctx = _path_context(path)
        if not ctx.resolved.is_file():
            raise FileNotFoundError(f"Local page file not found: {ctx.resolved}")
        if ctx.resolved.suffix.lower() not in _ALLOWED_LOCAL_EXTENSIONS:
            raise BrowserSecurityError(
                f"Unsupported local page file extension '{ctx.resolved.suffix}'."
            )
        size = ctx.resolved.stat().st_size
        if size > MAX_LOCAL_HTML_BYTES:
            raise ValueError(f"Local page exceeds maximum size ({MAX_LOCAL_HTML_BYTES} bytes).")
        raw = ctx.resolved.read_text(encoding="utf-8", errors="replace")
        self._last_local_path = str(ctx.resolved)
        return parse_html_safely(raw, source=str(ctx.resolved))

    def inspect_local_page(self, path: str) -> str:
        page = self.open_local_page(path)
        return format_untrusted_page(page)

    def extract_local_links(self, path: str) -> str:
        page = self.open_local_page(path)
        return json.dumps(
            {
                "source": page.source,
                "title": page.title,
                "links": page.links,
            },
            ensure_ascii=False,
        )

    def fetch_url(self, url: str) -> str:
        safe_url = validate_public_http_url(url)
        if not self.allow_online:
            raise BrowserSecurityError(
                "Online web browsing is disabled in RIYA offline mode."
            )
        host = (urlparse(safe_url).hostname or "").lower()
        if self.allowed_domains and host not in self.allowed_domains:
            raise BrowserSecurityError(
                f"Domain '{host}' is not in the permitted browser domain allowlist."
            )
        _verify_resolved_host_is_public(safe_url)

        req = Request(
            safe_url,
            method="GET",
            headers={
                "User-Agent": "RIYA-Local-Assistant/0.5 (Offline-First)",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
            },
        )
        try:
            with urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as resp:
                status = getattr(resp, "status", 200)
                if status in {401, 403}:
                    raise BrowserSecurityError(
                        f"Access denied / authentication required (HTTP {status}); "
                        "RIYA does not bypass login gates or access controls."
                    )
                raw_bytes = resp.read(MAX_REMOTE_BYTES + 1)
                if len(raw_bytes) > MAX_REMOTE_BYTES:
                    raw_bytes = raw_bytes[:MAX_REMOTE_BYTES]
                raw_html = raw_bytes.decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise BrowserSecurityError(
                    f"Access blocked (HTTP {exc.code}); RIYA will not bypass authentication or anti-bot gates."
                ) from exc
            raise RuntimeError(f"HTTP error {exc.code} fetching page.") from exc
        except URLError as exc:
            raise BrowserSecurityError(
                f"Network unreachable or offline while fetching '{host}': {exc.reason}"
            ) from exc

        page = parse_html_safely(raw_html, source=safe_url, base_url=safe_url)
        if page.captcha_detected:
            raise BrowserSecurityError(
                "CAPTCHA or anti-bot gate detected on target page; human interaction required."
            )
        return format_untrusted_page(page)

    def submit_form(self, url: str, fields_json: str) -> str:
        safe_url = validate_public_http_url(url)
        if not self.allow_online:
            raise BrowserSecurityError(
                "Online form submission is disabled in RIYA offline mode."
            )
        host = (urlparse(safe_url).hostname or "").lower()
        if self.allowed_domains and host not in self.allowed_domains:
            raise BrowserSecurityError(
                f"Domain '{host}' is not in the permitted browser domain allowlist."
            )
        try:
            fields = json.loads(fields_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"fields_json must be a valid JSON object: {exc.msg}") from exc
        if not isinstance(fields, dict):
            raise ValueError("fields_json must decode to a JSON object.")

        sanitized_fields: dict[str, str] = {}
        for k, v in fields.items():
            key_str = str(k).strip()
            if any(secret in key_str.lower() for secret in _SENSITIVE_FIELD_NAMES):
                raise BrowserSecurityError(
                    f"Submission of credential/secret field '{key_str}' is blocked for safety."
                )
            sanitized_fields[key_str] = strip_unsafe_control_chars(str(v), allow_newlines=False)

        _verify_resolved_host_is_public(safe_url)
        encoded = urlencode(sanitized_fields).encode("utf-8")
        req = Request(
            safe_url,
            data=encoded,
            method="POST",
            headers={
                "User-Agent": "RIYA-Local-Assistant/0.5 (Offline-First)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urlopen(req, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as resp:
                raw_bytes = resp.read(MAX_REMOTE_BYTES)
                raw_html = raw_bytes.decode("utf-8", errors="replace")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise BrowserSecurityError(
                    f"Form submission rejected (HTTP {exc.code}); human sign-in or approval required."
                ) from exc
            raise RuntimeError(f"HTTP error {exc.code} submitting form.") from exc
        except URLError as exc:
            raise BrowserSecurityError(
                f"Network unreachable or offline submitting form to '{host}': {exc.reason}"
            ) from exc

        page = parse_html_safely(raw_html, source=safe_url, base_url=safe_url)
        if page.captcha_detected:
            raise BrowserSecurityError(
                "CAPTCHA challenge returned after submission; human action required."
            )
        return format_untrusted_page(page)


_DEFAULT_BROWSER = BrowserController(allow_online=False)


def browser_open_local(path: str) -> str:
    """Open and safely parse a local HTML or text page inside allowed roots."""
    return _DEFAULT_BROWSER.inspect_local_page(path)


def browser_inspect_page(path: str) -> str:
    """Inspect a local HTML page structure cleanly without network access."""
    return _DEFAULT_BROWSER.inspect_local_page(path)


def browser_extract_links(path: str) -> str:
    """Extract sanitized links from a local HTML page."""
    return _DEFAULT_BROWSER.extract_local_links(path)


def browser_fetch_url(url: str) -> str:
    """Fetch a public HTTP/HTTPS page (disabled by default in offline mode)."""
    return _DEFAULT_BROWSER.fetch_url(url)


def browser_submit_form(url: str, fields_json: str) -> str:
    """Submit non-sensitive form fields to an approved HTTP/HTTPS URL (offline-blocked by default)."""
    return _DEFAULT_BROWSER.submit_form(url, fields_json)
