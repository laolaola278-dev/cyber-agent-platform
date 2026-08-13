"""zap-api-python implementation of the typed ZAP API client port."""

import asyncio
from collections.abc import Callable
from typing import Any

from app.exceptions import AssessmentExecutionError


class ZapV2ApiClient:
    """Async facade over the official synchronous zap-api-python client."""

    def __init__(self, *, api_url: str, api_key: str) -> None:
        self._api_url = api_url
        self._api_key = api_key
        self._client: Any | None = None

    def _zap(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise AssessmentExecutionError("ZAP API key is not configured")
            try:
                from zapv2 import ZAPv2
            except ImportError as error:
                raise AssessmentExecutionError("zaproxy client package is not installed") from error
            proxies = {"http": self._api_url, "https": self._api_url}
            self._client = ZAPv2(apikey=self._api_key, proxies=proxies)
        return self._client

    async def _call(self, operation: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(operation)

    async def version(self) -> str:
        return str(await self._call(lambda: self._zap().core.version))

    async def new_session(self, name: str, *, overwrite: bool) -> None:
        await self._call(lambda: self._zap().core.new_session(name, str(overwrite).lower()))

    async def remove_session(self, name: str) -> None:
        await self._call(lambda: self._zap().core.delete_session(name))

    async def new_context(self, name: str) -> str:
        return str(await self._call(lambda: self._zap().context.new_context(name)))

    async def include_in_context(self, name: str, regex: str) -> None:
        await self._call(lambda: self._zap().context.include_in_context(name, regex))

    async def exclude_from_context(self, name: str, regex: str) -> None:
        await self._call(lambda: self._zap().context.exclude_from_context(name, regex))

    async def access_url(self, url: str) -> None:
        await self._call(lambda: self._zap().core.access_url(url))

    async def wait_for_passive_scan(self, timeout_seconds: int) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            remaining = int(await self._call(lambda: self._zap().pscan.records_to_scan))
            if remaining <= 0:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise AssessmentExecutionError("ZAP passive scan timed out")
            await asyncio.sleep(0.2)

    async def spider(self, url: str, *, context_name: str, max_depth: int, max_urls: int) -> int:
        await self._call(lambda: self._zap().spider.set_option_max_depth(max_depth))
        scan_id = str(
            await self._call(
                lambda: self._zap().spider.scan(
                    url, maxchildren=max_urls, recurse=True, contextname=context_name
                )
            )
        )
        await self._wait_until(lambda: self._zap().spider.status(scan_id), timeout_seconds=300)
        results = await self._call(lambda: self._zap().spider.results(scan_id))
        return len(results) if isinstance(results, list) else 0

    async def active_scan(
        self, url: str, *, context_id: str, scan_policy: str, timeout_seconds: int
    ) -> None:
        scan_id = str(
            await self._call(
                lambda: self._zap().ascan.scan(
                    url,
                    recurse=True,
                    inscopeonly=True,
                    scanpolicyname=scan_policy,
                    contextid=context_id,
                )
            )
        )
        await self._wait_until(
            lambda: self._zap().ascan.status(scan_id), timeout_seconds=timeout_seconds
        )

    async def _wait_until(self, status: Callable[[], Any], *, timeout_seconds: int) -> None:
        async def wait() -> None:
            while int(await self._call(status)) < 100:  # noqa: ASYNC110
                await asyncio.sleep(0.2)

        try:
            await asyncio.wait_for(wait(), timeout=timeout_seconds)
        except TimeoutError as error:
            raise AssessmentExecutionError("ZAP scan timed out") from error

    async def alerts(self, *, base_url: str, limit: int) -> list[dict[str, object]]:
        values = await self._call(lambda: self._zap().core.alerts(baseurl=base_url, count=limit))
        return [dict(item) for item in values] if isinstance(values, list) else []
