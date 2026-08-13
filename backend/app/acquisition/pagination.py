"""Phase 28 -- Pagination detection (spec 11).

Recognizes: next-link, page parameter, cursor (only when the page itself
exposes it), load-more, infinite scroll. Every strategy carries hard budgets
(max_pages / max_records / max_duration / max_requests) so loops always
terminate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from app.acquisition.models import PaginationStrategy

_NEXT_TEXT_RE = re.compile(r"(next|next page|load more|more|older|»)", re.I)
_CURSOR_RE = re.compile(r"(cursor|next_?cursor|page_?token|continuation|after|since_id)", re.I)
_PAGE_PARAM_RE = re.compile(r"(page|p|page_?number|offset|start|limit)", re.I)


@dataclass
class PageLinks:
    """Links discovered in one fetched page."""

    hrefs: list[str] = field(default_factory=list)
    next_href: str | None = None
    page_params: list[str] = field(default_factory=list)


def extract_page_links(
    html: str,
    base_url: str,
    *,
    max_links: int = 300,
) -> PageLinks:
    """Pull candidate hrefs and next-link candidates from rendered/static HTML."""
    hrefs: list[str] = []
    next_href: str | None = None
    for match in re.finditer(r"<a\b[^>]*>", html):
        tag = match.group(0)
        href_match = re.search(r'href=["\']([^"\']+)["\']', tag)
        if href_match is None:
            continue
        href = href_match.group(1)
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        if len(hrefs) >= max_links:
            break
        hrefs.append(href)
        if _NEXT_TEXT_RE.search(tag.lower()) and next_href is None:
            next_href = href
    return PageLinks(hrefs=hrefs, next_href=next_href)


def detect_strategy(
    *,
    page_url: str,
    html: str | None = None,
    page_links: PageLinks | None = None,
    budgets: dict[str, Any] | None = None,
) -> PaginationStrategy:
    """Detect the pagination strategy for a page."""
    budgets = budgets or {}
    parsed = urlparse(page_url)
    params = parse_qs(parsed.query)

    strategy = PaginationStrategy(
        max_pages=int(budgets.get("max_pages", 5)),
        max_records=int(budgets.get("max_records", 200)),
        max_duration=float(budgets.get("max_duration", 120.0)),
        max_requests=int(budgets.get("max_requests", 20)),
        base_url=page_url,
    )

    links = page_links or extract_page_links(html or "", page_url)
    if links.next_href:
        resolved = _resolve_href(links.next_href, page_url)
        if resolved:
            strategy.kind = "next_link"
            strategy.next_url = resolved
            return strategy

    # page parameter already present in the URL -> paginate on it
    for key in params:
        if _PAGE_PARAM_RE.fullmatch(key):
            strategy.kind = "page_param"
            strategy.page_param = key
            return strategy

    # cursor-style params observed on the page itself
    for key in params:
        if _CURSOR_RE.search(key):
            strategy.kind = "cursor"
            strategy.page_param = key
            return strategy

    if html and ("infinite scroll" in html.lower() or "load more" in html.lower()):
        strategy.kind = "load_more"
        return strategy

    strategy.kind = "none"
    return strategy


def next_page_url(strategy: PaginationStrategy, current_url: str, page_number: int) -> str | None:
    """Build the next page URL for page_param/cursor strategies (bounded)."""
    if page_number >= strategy.max_pages:
        return None
    if strategy.kind == "next_link" and strategy.next_url:
        return strategy.next_url
    if strategy.kind in ("page_param", "cursor") and strategy.page_param:
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params[strategy.page_param] = [str(page_number + 1)]
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(params, doseq=True),
                parsed.fragment,
            )
        )
    return None


def _resolve_href(href: str, base_url: str) -> str | None:
    try:
        from urllib.parse import urljoin

        resolved = urljoin(base_url, href)
        parsed = urlparse(resolved)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return resolved
    except ValueError:
        return None
