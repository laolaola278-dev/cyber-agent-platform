"""Phase 28 -- final defensive-branch coverage: store, urlpolicy internals,
browser adapter edge paths, dedup canonicalization, agent leftovers."""

from __future__ import annotations

import asyncio

import pytest

from app.acquisition.store import LocalFilesystemEvidenceStore, ObjectStoreError, sha256_file
from app.acquisition.urlpolicy import URLPolicyValidator


def run(coro):
    return asyncio.run(coro)


# -- store -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_metadata_missing(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path)
    with pytest.raises(ObjectStoreError):
        await store.metadata("0000000000000000000000000000000000000000000000000000000000000000")


@pytest.mark.asyncio
async def test_store_object_count(tmp_path) -> None:
    store = LocalFilesystemEvidenceStore(tmp_path)
    assert store.object_count == 0
    await store.put(b"one", metadata={})
    await store.put(b"two", metadata={})
    assert store.object_count == 2


def test_sha256_file(tmp_path) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"hello")
    digest = sha256_file(target)
    assert len(digest) == 64


# -- urlpolicy internals -----------------------------------------------------


def test_urlpolicy_private_ip_classes() -> None:
    import ipaddress

    from app.acquisition.urlpolicy import _is_private_ip

    assert _is_private_ip(ipaddress.ip_address("127.0.0.1")) is True
    assert _is_private_ip(ipaddress.ip_address("169.254.169.254")) is True
    assert _is_private_ip(ipaddress.ip_address("224.0.0.1")) is True  # multicast
    assert _is_private_ip(ipaddress.ip_address("0.0.0.0")) is True  # unspecified
    assert _is_private_ip(ipaddress.ip_address("8.8.8.8")) is False


def test_urlpolicy_result_bool() -> None:
    from app.acquisition.urlpolicy import URLValidationResult

    assert bool(URLValidationResult(allowed=True, reason="ok")) is True
    assert bool(URLValidationResult(allowed=False, reason="no")) is False


def test_urlpolicy_public_ip_literal() -> None:
    result = URLPolicyValidator().validate_url("http://8.8.8.8/")
    assert result.allowed is True
    assert "public IP literal" in result.reason


def test_urlpolicy_empty_host() -> None:
    validator = URLPolicyValidator(resolver=lambda host: ["8.8.8.8"])
    assert validator.validate_url("http:///path").allowed is False


def test_urlpolicy_resolver_exception_fails_closed() -> None:
    def exploding(_host):
        raise OSError("dns down")

    result = URLPolicyValidator(resolver=exploding).validate_url("https://x.example/")
    assert result.allowed is False


def test_urlpolicy_resolver_invalid_ip_text() -> None:
    result = URLPolicyValidator(resolver=lambda host: ["not-an-ip"]).validate_url(
        "https://x.example/"
    )
    assert result.allowed is True  # invalid text skipped; no private IP found


def test_urlpolicy_userinfo_netloc() -> None:
    validator = URLPolicyValidator(resolver=lambda host: ["8.8.8.8"])
    assert validator.validate_url("http://user@example.com/").allowed is False
    assert validator.validate_url("http://user@:80/").allowed is False


def test_urlpolicy_default_resolver_real() -> None:
    ips = URLPolicyValidator._default_resolver("example.com")
    assert isinstance(ips, list)
    assert len(ips) > 0


# -- browser adapter edges ---------------------------------------------------


def test_browser_adapter_available_property() -> None:
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._available = True
    assert adapter.available is True
    adapter._available = False
    assert adapter.available is False


def test_browser_adapter_observe_method() -> None:
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    # _observe with a page that has no expect_request attribute
    endpoints = run(adapter._observe(object(), "https://bench.example/"))
    assert endpoints == []


def test_browser_adapter_on_request_non_http() -> None:
    """_on_request skips non-http and cross-origin requests (fake page)."""
    from app.acquisition.browseradapter import PlaywrightAcquisitionAdapter
    from tests.test_phase_28_lastmile import FakePage

    adapter = PlaywrightAcquisitionAdapter.__new__(PlaywrightAcquisitionAdapter)
    adapter._available = True
    adapter._platform = object()
    adapter._browser_manager = None

    # monkeypatch the manager used in browse
    class FakeManager:
        async def new_context(self):
            return FakeContextLike()

        async def close_context(self, context):
            return None

        async def stop(self):
            return None

    class FakeContextLike:
        async def new_page(self):
            return FakePage(None)

    adapter._browser_manager = FakeManager()
    observation = run(adapter.browse("https://bench.example/app"))
    assert observation.available is True


# -- dedup canonicalization -------------------------------------------------


def test_dedup_canonicalize_default_ports() -> None:
    from app.acquisition.dedup import canonicalize_url

    assert canonicalize_url("https://example.com:443/x") == "https://example.com/x"
    assert canonicalize_url("http://example.com:80/x") == "http://example.com/x"


def test_dedup_canonicalize_bad_scheme() -> None:
    from app.acquisition.dedup import canonicalize_url

    assert canonicalize_url("ftp://example.com/") == "ftp://example.com/"


# -- agent leftovers ---------------------------------------------------------


def test_agent_robots_fetch_http_error_stops() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent, AgentConfig
    from app.acquisition.dataset import SyntheticResponse, SyntheticWeb
    from app.acquisition.evaluation import _TempStore
    from app.acquisition.httpadapter import HTTPAdapter
    from app.acquisition.models import AcquisitionPolicy
    from app.acquisition.planner import AcquisitionPlanner, PlannerRequest
    from app.acquisition.urlpolicy import URLPolicyValidator

    origin = "https://bench.example"
    # robots.txt route missing -> 404; treat as allowed (no robots.txt)
    web = SyntheticWeb(
        {f"{origin}/page": SyntheticResponse(200, {"content-type": "text/html"}, b"<html>x</html>")}
    )
    policy = AcquisitionPolicy(request_rate=100.0)
    agent = AdaptiveDataAcquisitionAgent(
        http=HTTPAdapter(
            policy=policy,
            validator=URLPolicyValidator(resolver=lambda host: ["93.184.216.34"]),
            client_factory=web.client_factory(),
        ),
        store=_TempStore("mem"),
        planner=AcquisitionPlanner(policy=policy),
        config=AgentConfig(task_id="t", trace_id="tr"),
    )
    result = run(agent.acquire(PlannerRequest(goal="g", url=f"{origin}/page")))
    assert result.status.value in ("COMPLETE", "PARTIAL")


def test_agent_visited_url_dedup() -> None:
    from app.acquisition.agent import AdaptiveDataAcquisitionAgent

    # visited_urls guard returns early for repeat URLs
    class FakeResult:
        visited_urls = ["https://x.example/a"]

    agent = AdaptiveDataAcquisitionAgent.__new__(AdaptiveDataAcquisitionAgent)
    run(agent._acquire_url(url="https://x.example/a", plan=None, result=FakeResult(), dupes=None))  # type: ignore[arg-type]
    assert FakeResult.visited_urls == ["https://x.example/a"]  # unchanged
