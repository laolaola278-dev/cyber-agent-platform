# ruff: noqa: E501
"""Phase 28 -- CAP-AQB v1 dataset (spec 26).

CAP Acquisition Quality Benchmark: >=100 synthetic/local scenarios covering
static/dynamic HTML, pagination, infinite scroll, JSON API, PDF/DOCX/XLSX,
duplicates, missing fields/time ranges, redirects (incl. to private IP),
DNS rebinding simulation, robots disallow, 401/403, login/captcha/paywall
markers, oversized documents, malformed HTML, timeout, rate limit, partial
completion and structure change. >=20% are failure/blocked/partial by design.

The "web" is an in-memory SyntheticWeb; no real third-party site is hit
(spec 26: no batch load-testing of real sites).
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from urllib.parse import urlparse


@dataclass
class SyntheticResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def _html(title: str, body: str, *, next_href: str | None = None) -> bytes:
    next_link = f'<a href="{next_href}" rel="next">next</a>' if next_href else ""
    return (
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1><p>{body}</p>{next_link}</body></html>"
    ).encode()


def _make_pdf_bytes() -> bytes:
    """Tiny but structurally valid PDF (minimal header + xref-free trailer)."""
    content = b"BT /F1 12 Tf 72 720 Td (CAP-AQB synthetic advisory) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    """Minimal valid DOCX via python-docx (installed)."""
    import docx  # type: ignore[import-not-found]

    buf = BytesIO()
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(buf)
    return buf.getvalue()


def _make_xlsx_bytes(rows: list[list[Any]]) -> bytes:
    import openpyxl  # type: ignore[import-not-found]

    buf = BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(buf)
    return buf.getvalue()


def _redirect(url: str, status: int = 302) -> SyntheticResponse:
    return SyntheticResponse(
        status=status,
        headers={"location": url},
        body=b"",
    )


@dataclass
class AQBScenario:
    scenario_id: str
    category: str
    url: str
    routes: dict[str, SyntheticResponse]
    expected: dict[str, Any] = field(default_factory=dict)
    resolver: Callable[[str], list[str]] | None = None
    outcome_class: str = "success"  # success | blocked | partial
    seed: int = 0


@dataclass
class SyntheticWeb:
    """In-memory web used by the benchmark (also serves robots.txt)."""

    routes: dict[str, SyntheticResponse]

    def get(self, url: str) -> SyntheticResponse:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.path.rstrip("/") == "/robots.txt":
            robots = self.routes.get(f"{origin}/robots.txt")
            if robots is not None:
                return robots
        response = self.routes.get(url)
        if response is None:
            return SyntheticResponse(status=404, body=b"not found")
        return response

    def client_factory(self) -> Any:
        import httpx

        class _FakeTransport(httpx.AsyncBaseTransport):
            def __init__(self, web: SyntheticWeb) -> None:
                self._web = web

            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                response = self._web.get(str(request.url))
                return httpx.Response(
                    status_code=response.status,
                    headers=response.headers,
                    content=response.body,
                    request=request,
                )

        return lambda: httpx.AsyncClient(
            transport=_FakeTransport(self),
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
        )


# -- builders ---------------------------------------------------------------


def _build_scenarios() -> list[AQBScenario]:
    scenarios: list[AQBScenario] = []
    origin = "https://bench.example"
    counter = 0

    def make(
        category: str,
        url: str,
        routes: dict[str, SyntheticResponse],
        *,
        expected: dict[str, Any] | None = None,
        resolver: Callable[[str], list[str]] | None = None,
        outcome: str = "success",
    ) -> AQBScenario:
        nonlocal counter
        counter += 1
        return AQBScenario(
            scenario_id=f"aqb-{counter:03d}",
            category=category,
            url=url,
            routes=routes,
            expected=expected or {},
            resolver=resolver,
            outcome_class=outcome,
            seed=counter,
        )

    # -- static HTML (15) ----------------------------------------------------
    for index in range(15):
        title = f"Security Advisory {index}"
        url = f"{origin}/advisory/{index}"
        scenarios.append(
            make(
                "static_html",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html(title, f"Body {index}")),
                },
                expected={"extracted": True, "title_present": True},
            )
        )

    # -- pagination (10) -----------------------------------------------------
    for index in range(10):
        base = f"{origin}/list/{index}"
        page1 = f"{base}?page=1"
        routes = {
            f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            page1: SyntheticResponse(
                200, {"content-type": "text/html"},
                _html(f"List {index}", "item 1", next_href=f"{base}?page=2"),
            ),
            f"{base}?page=2": SyntheticResponse(
                200, {"content-type": "text/html"},
                _html(f"List {index}", "item 2", next_href=f"{base}?page=3"),
            ),
            f"{base}?page=3": SyntheticResponse(
                200, {"content-type": "text/html"}, _html(f"List {index}", "item 3"),
            ),
        }
        scenarios.append(
            make(
                "pagination",
                page1,
                routes,
                expected={"pages_fetched": 3, "pagination_complete": True},
            )
        )

    # -- infinite scroll synthetic (5) ---------------------------------------
    for index in range(5):
        url = f"{origin}/feed/{index}"
        scenarios.append(
            make(
                "infinite_scroll",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200, {"content-type": "text/html"},
                        _html(f"Feed {index}", "infinite scroll synthetic feed"),
                    ),
                },
                expected={"extracted": True},
            )
        )

    # -- JSON API observed (10) ----------------------------------------------
    for index in range(10):
        url = f"{origin}/api/v1/records/{index}"
        payload = json.dumps({"id": index, "title": f"record {index}"}).encode()
        scenarios.append(
            make(
                "json_api",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nDisallow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "application/json"}, payload),
                },
                expected={"extracted": True, "json_parsed": True},
            )
        )

    # -- PDF (8) --------------------------------------------------------------
    for index in range(8):
        url = f"{origin}/docs/report-{index}.pdf"
        scenarios.append(
            make(
                "pdf",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200, {"content-type": "application/pdf"}, _make_pdf_bytes()
                    ),
                },
                expected={"extracted": True, "pdf_parsed": True},
            )
        )

    # -- DOCX (6) -------------------------------------------------------------
    for index in range(6):
        url = f"{origin}/docs/note-{index}.docx"
        scenarios.append(
            make(
                "docx",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200,
                        {
                            "content-type": (
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document"
                            )
                        },
                        _make_docx_bytes([f"Note {index}", "Body text"]),
                    ),
                },
                expected={"extracted": True, "docx_parsed": True},
            )
        )

    # -- XLSX (4) -------------------------------------------------------------
    for index in range(4):
        url = f"{origin}/data/table-{index}.xlsx"
        scenarios.append(
            make(
                "xlsx",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200,
                        {
                            "content-type": (
                                "application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet"
                            )
                        },
                        _make_xlsx_bytes([["id", "value"], [index, "x"]]),
                    ),
                },
                expected={"extracted": True, "xlsx_parsed": True},
            )
        )

    # -- duplicate (6) --------------------------------------------------------
    for index in range(6):
        url = f"{origin}/dup/{index}"
        body = _html(f"Dup {index}", "same content for all pages")
        scenarios.append(
            make(
                "duplicate",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, body),
                    f"{url}?page=2": SyntheticResponse(200, {"content-type": "text/html"}, body),
                },
                expected={"duplicate_detected": True},
            )
        )

    # -- dynamic HTML (10): JS-rendered -> replan to browser (synthetic) -----
    for index in range(10):
        url = f"{origin}/app/{index}"
        scenarios.append(
            make(
                "dynamic_html",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200,
                        {"content-type": "text/html"},
                        b"<!doctype html><html><head><title>App</title></head>"
                        b"<body><div id=app></div></body></html>",
                    ),
                },
                expected={"extracted": True},
            )
        )

    # ===================== failure / blocked / partial =====================

    # -- missing field (5, partial) ------------------------------------------
    for index in range(5):
        url = f"{origin}/missing/{index}"
        scenarios.append(
            make(
                "missing_field",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("M", "")),
                },
                expected={
                    "status": "PARTIAL",
                    "field_missing": True,
                    "expected_fields": ["title", "body", "date", "severity"],
                },
                outcome="partial",
            )
        )

    # -- missing time range (4, partial) -------------------------------------
    for index in range(4):
        url = f"{origin}/notime/{index}"
        scenarios.append(
            make(
                "missing_time_range",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("T", "no timestamps")),
                },
                expected={
                    "status": "PARTIAL",
                    "expected_time_range": [
                        "2026-01-01T00:00:00+00:00",
                        "2026-08-01T00:00:00+00:00",
                    ],
                },
                outcome="partial",
            )
        )

    # -- redirect ok (3) ------------------------------------------------------
    for index in range(3):
        final = f"{origin}/final/{index}"
        url = f"{origin}/jump/{index}"
        scenarios.append(
            make(
                "redirect",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: _redirect(final),
                    final: SyntheticResponse(200, {"content-type": "text/html"}, _html("Final", "ok")),
                },
                expected={"final_url": final},
            )
        )

    # -- redirect to private IP (3, SSRF blocked) ----------------------------
    for index in range(3):
        url = f"{origin}/private-redirect/{index}"
        routes = {
            f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
            url: _redirect("http://192.168.0.5/secret"),
        }
        scenarios.append(
            make(
                "redirect_private_ip",
                url,
                routes,
                expected={"status": "BLOCKED", "reason": "SSRF_BLOCKED"},
                outcome="blocked",
            )
        )

    # -- DNS rebinding (3, SSRF blocked) -------------------------------------
    for index in range(3):
        url = f"{origin}/rebind/{index}"

        def rebind_resolver(host: str) -> list[str]:
            return ["169.254.169.254"]

        scenarios.append(
            make(
                "dns_rebinding",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("R", "x")),
                },
                expected={"status": "BLOCKED", "reason": "SSRF_BLOCKED"},
                resolver=rebind_resolver,
                outcome="blocked",
            )
        )

    # -- robots disallow (4, blocked) ----------------------------------------
    for index in range(4):
        url = f"{origin}/disallowed/{index}"
        scenarios.append(
            make(
                "robots_disallow",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(
                        200, {}, b"User-agent: *\nDisallow: /disallowed/\n"
                    ),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("D", "x")),
                },
                expected={"status": "BLOCKED", "reason": "ROBOTS_DISALLOWED"},
                outcome="blocked",
            )
        )

    # -- 401 / 403 (6, blocked) ----------------------------------------------
    for index, status in enumerate([401, 403, 401, 403, 401, 403]):
        url = f"{origin}/restricted/{index}"
        scenarios.append(
            make(
                "http_401" if status == 401 else "http_403",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(status, {}, b"restricted"),
                },
                expected={"status": "BLOCKED", "reason": "AUTH_REQUIRED"},
                outcome="blocked",
            )
        )

    # -- login / captcha / paywall (6, blocked) ------------------------------
    login_marker = _html("Sign in", "please login to continue")
    captcha_marker = _html("Verify", "recaptcha verify you are human")
    paywall_marker = _html("Premium", "subscribe to continue reading")
    for index in range(2):
        scenarios.append(
            make(
                "login_page",
                f"{origin}/login/{index}",
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    f"{origin}/login/{index}": SyntheticResponse(200, {"content-type": "text/html"}, login_marker),
                },
                expected={"status": "BLOCKED", "reason": "LOGIN_PAGE"},
                outcome="blocked",
            )
        )
        scenarios.append(
            make(
                "captcha",
                f"{origin}/captcha/{index}",
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    f"{origin}/captcha/{index}": SyntheticResponse(200, {"content-type": "text/html"}, captcha_marker),
                },
                expected={"status": "BLOCKED", "reason": "CAPTCHA"},
                outcome="blocked",
            )
        )
        scenarios.append(
            make(
                "paywall",
                f"{origin}/paywall/{index}",
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    f"{origin}/paywall/{index}": SyntheticResponse(200, {"content-type": "text/html"}, paywall_marker),
                },
                expected={"status": "BLOCKED", "reason": "PAYWALL"},
                outcome="blocked",
            )
        )

    # -- oversized document (2, blocked) -------------------------------------
    for index in range(2):
        url = f"{origin}/big/{index}"
        scenarios.append(
            make(
                "oversized",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, b"x" * (12 * 1024 * 1024)),
                },
                expected={"status": "BLOCKED", "reason": "SIZE_LIMIT"},
                outcome="blocked",
            )
        )

    # -- malformed HTML (3, partial) -----------------------------------------
    for index in range(3):
        url = f"{origin}/broken/{index}"
        scenarios.append(
            make(
                "malformed_html",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(
                        200, {"content-type": "text/html"}, b"<html><body><unclosed " + b"a" * 200,
                    ),
                },
                expected={"status": "COMPLETE"},
                outcome="partial",
            )
        )

    # -- timeout (3, partial) -------------------------------------------------
    for index in range(3):
        url = f"{origin}/slow/{index}"
        scenarios.append(
            make(
                "timeout",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, b""),
                },
                expected={"status": "PARTIAL", "expected_record_count": 50},
                outcome="partial",
            )
        )

    # -- rate limit (2, partial) ---------------------------------------------
    for index in range(2):
        url = f"{origin}/ratelimit/{index}"
        scenarios.append(
            make(
                "rate_limit",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(429, {"retry-after": "60"}, b"slow down"),
                },
                expected={"status": "PARTIAL", "reason": "RATE_LIMITED"},
                outcome="partial",
            )
        )

    # -- partial completion (3, partial) -------------------------------------
    for index in range(3):
        url = f"{origin}/partial/{index}"
        scenarios.append(
            make(
                "partial_completion",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("P", "only partial data")),
                },
                expected={"status": "PARTIAL", "expected_record_count": 50},
                outcome="partial",
            )
        )

    # -- structure change (3, replan/partial) --------------------------------
    for index in range(3):
        url = f"{origin}/changed/{index}"
        scenarios.append(
            make(
                "structure_change",
                url,
                {
                    f"{origin}/robots.txt": SyntheticResponse(200, {}, b"User-agent: *\nAllow: /\n"),
                    url: SyntheticResponse(200, {"content-type": "text/html"}, _html("C", "layout changed")),
                },
                expected={
                    "status": "PARTIAL",
                    "expected_fields": ["title", "body", "date", "author"],
                },
                outcome="partial",
            )
        )

    return scenarios


def build_aqb_v1(*, seed: int = 42) -> list[AQBScenario]:
    """Deterministic dataset build (stable ordering)."""
    scenarios = _build_scenarios()
    rng = random.Random(seed)
    rng.shuffle(scenarios)
    return scenarios


def aqb_stats(scenarios: list[AQBScenario]) -> dict[str, Any]:
    from collections import Counter

    categories = Counter(s.category for s in scenarios)
    outcomes = Counter(s.outcome_class for s in scenarios)
    total = len(scenarios)
    return {
        "total": total,
        "categories": dict(categories),
        "outcomes": dict(outcomes),
        "failure_fraction": round(
            (outcomes["blocked"] + outcomes["partial"]) / max(total, 1), 4
        ),
    }
