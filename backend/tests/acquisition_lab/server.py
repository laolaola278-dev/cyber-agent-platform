"""Synthetic Acquisition Lab -- local-only HTTP server for production-path
certification (Phase 28.1).

Serves ONLY on 127.0.0.1. Every route mirrors a real-world acquisition
surface: static/dynamic pages, pagination, infinite scroll, XHR endpoints,
redirects, robots disallow, login/captcha/paywall markers and large bodies.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_RECORD_TEMPLATE = {
    "id": "",
    "title": "",
    "cve": "",
    "published_at": "2026-08-01T00:00:00+00:00",
    "severity": "HIGH",
}


def _page_html(page: int, total_pages: int = 3, records_per_page: int = 10) -> bytes:
    rows = []
    for index in range(records_per_page):
        number = (page - 1) * records_per_page + index + 1
        rows.append(
            f"<tr><td class='rec-title'>Advisory {number}</td>"
            f"<td>CVE-2026-{1000 + number}</td>"
            f"<td class='rec-date'>2026-08-0{(number % 9) + 1}</td></tr>"
        )
    next_link = (
        f"<a href='/pagination?page={page + 1}' rel='next'>next</a>"
        if page < total_pages
        else ""
    )
    return (
        "<!doctype html><html><head><title>Advisories page "
        f"{page}</title></head><body>"
        f"<table id='records'>{''.join(rows)}</table>{next_link}"
        "</body></html>"
    ).encode()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence request logs
        return None

    def _send(
        self, status: int, body: bytes, content_type: str, headers: dict | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Lab", "cap-synthetic-acquisition-lab")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/static":
            body = (
                b"<!doctype html><html><head><title>Static advisory</title></head>"
                b"<body><h1>Public advisory</h1><p>CVE-2026-1001 fixed in "
                b"version 2.4.1.</p></body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/dynamic":
            # JS shell: real content only appears after client-side rendering.
            body = (
                b"<!doctype html><html><head><title>Dynamic advisory</title></head>"
                b"<body><div id='app'></div>"
                b"<script>"
                b"window.addEventListener('DOMContentLoaded', () => {"
                b"document.getElementById('app').innerHTML = "
                b"'<h1>Rendered advisory</h1><p>CVE-2026-1002 rendered by JS.</p>';"
                b"});"
                b"</script></body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/pagination":
            if "mode" in query and query["mode"][0] == "timeout":
                import time

                time.sleep(8)  # longer than the lab client timeout
                return self._send(200, b"", "text/html")
            page = int(query.get("page", ["1"])[0])
            if page == 2 and getattr(self.server, "_fail_page2", False):
                import time

                time.sleep(8)
                return self._send(200, b"", "text/html")
            return self._send(200, _page_html(page), "text/html")

        if path == "/infinite":
            body = (
                "<!doctype html><html><head><title>Infinite list</title></head>"
                "<body><ul id='items'>"
                + "".join(f"<li class='item'>row {i}</li>" for i in range(1, 11))
                + "</ul><div id='sentinel'></div>"
                "<script>"
                "const sentinel = document.getElementById('sentinel');"
                "new IntersectionObserver((entries) => {"
                "if (entries[0].isIntersecting) {"
                "for (let i = 11; i <= 20; i++) {"
                "const li = document.createElement('li');"
                "li.className = 'item'; li.textContent = 'row ' + i;"
                "document.getElementById('items').appendChild(li);"
                "}}}).observe(sentinel);"
                "</script></body></html>"
            ).encode()
            return self._send(200, body, "text/html")

        if path == "/xhr":
            body = (
                b"<!doctype html><html><head><title>XHR page</title></head>"
                b"<body><div id='data'></div>"
                b"<script>"
                b"fetch('/api/records?page=1').then(r => r.json()).then(d => {"
                b"document.getElementById('data').textContent = "
                b"JSON.stringify(d.records.length);});"
                b"</script></body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/api/records":
            page = int(query.get("page", ["1"])[0])
            records = []
            for index in range(10):
                number = (page - 1) * 10 + index + 1
                record = dict(_RECORD_TEMPLATE)
                record["id"] = f"adv-{number}"
                record["title"] = f"Advisory {number}"
                record["cve"] = f"CVE-2026-{1000 + number}"
                records.append(record)
            payload = json.dumps({"records": records, "page": page}).encode()
            return self._send(200, payload, "application/json")

        if path == "/redirect":
            return self._send(302, b"", "text/html", {"Location": "/static"})

        if path == "/robots.txt":
            return self._send(
                200,
                b"User-agent: *\nDisallow: /private\nAllow: /\n",
                "text/plain",
            )

        if path == "/private":
            return self._send(200, b"<html>secret</html>", "text/html")

        if path == "/login":
            body = (
                b"<!doctype html><html><head><title>Sign in</title></head>"
                b"<body><form action='/login' method='post'>"
                b"<input name='user' type='text'><input name='pass' type='password'>"
                b"</form></body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/captcha":
            body = (
                b"<!doctype html><html><head><title>Verify</title></head>"
                b"<body><div class='g-recaptcha'>verify you are human</div>"
                b"</body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/paywall":
            body = (
                b"<!doctype html><html><head><title>Subscribe</title></head>"
                b"<body><div class='paywall'>This article is behind a "
                b"subscription.</div></body></html>"
            )
            return self._send(200, body, "text/html")

        if path == "/large":
            return self._send(200, b"x" * (8 * 1024 * 1024), "application/octet-stream")

        return self._send(404, b"not found", "text/plain")


class AcquisitionLabServer:
    """Threaded local HTTP server for acquisition certification."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread: threading.Thread | None = None
        self._server._fail_page2 = False  # type: ignore[attr-defined]

    @property
    def origin(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def set_fail_page2(self, enabled: bool) -> None:
        """Make /pagination?page=2 stall past the lab timeout (resume tests)."""
        self._server._fail_page2 = enabled  # type: ignore[attr-defined]

    def start(self) -> AcquisitionLabServer:
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
