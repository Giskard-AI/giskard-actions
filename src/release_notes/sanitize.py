"""Neutralize links, images and HTML in model-written Markdown before it is published.

Stdlib-only: also imported by ``scripts/ci/codex_pr_assessment.py``, which runs on the
runner's system Python without the package's dependencies.
"""

import html
import re
from collections.abc import Callable
from urllib.parse import urlparse

LINK_REMOVED = "[external-link-removed]"

_INVISIBLE_RE = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)
_ESCAPED_URL_PUNCTUATION_RE = re.compile(r"\\(?=[:/])")
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\](?:\([^)]*\)|\[[^\]]*\])?")
_URL_RE = re.compile(r"(?i)(?:https?:(?://)?|(?<![\w:/])//|(?<![\w./-])www\.)[^\s)<>\]\"'`]+")


def _unescape_entities(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, html.unescape(text)
    return text


def _url_host_and_path(url: str) -> tuple[str, str]:
    candidate = url if re.match(r"(?i)https?://", url) else "https://" + url.lstrip("/:")
    parsed = urlparse(candidate)
    return (parsed.hostname or "").lower(), parsed.path


def strip_untrusted_links(text: str, is_allowed: Callable[[str, str], bool]) -> str:
    """Drop images, HTML and invisible characters; replace URLs ``is_allowed(host, path)`` rejects.

    Covers scheme-relative (``//host``), bare ``www.`` autolinks, any scheme case, and URLs
    hidden behind HTML entities or Markdown backslash escapes.
    """
    text = _INVISIBLE_RE.sub("", text)
    text = _unescape_entities(text)
    text = _ESCAPED_URL_PUNCTUATION_RE.sub("", text)
    text = _HTML_COMMENT_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _MARKDOWN_IMAGE_RE.sub("", text)

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            host, path = _url_host_and_path(url)
        except ValueError:
            return LINK_REMOVED
        return url if host and is_allowed(host, path) else LINK_REMOVED

    return _URL_RE.sub(replace_url, text)


def github_repo_link_filter(owner_repo: str | None) -> Callable[[str, str], bool]:
    """Allow only ``https://github.com/<owner_repo>/...`` links."""
    prefix = f"/{owner_repo.strip('/').lower()}" if owner_repo else None

    def is_allowed(host: str, path: str) -> bool:
        if prefix is None or host not in {"github.com", "www.github.com"}:
            return False
        lowered = path.lower()
        return lowered == prefix or lowered.startswith(prefix + "/")

    return is_allowed
