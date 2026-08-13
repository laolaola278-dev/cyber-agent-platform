"""Phase 28.4 -- Durable object storage (MinIO) certification.

Real S3 protocol against a live MinIO server (no mocks). Certifies:
content-addressed immutable put, duplicate-content idempotency, digest
verification on get, corruption rejection, listing and deletion.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.acquisition.store import ObjectStoreError, S3EvidenceStore, sha256_hex

pytestmark = [pytest.mark.postgres, pytest.mark.object_store]

S3_ENDPOINT = os.environ.get("CAP283_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS = os.environ.get("CAP283_S3_ACCESS", "capadmin")
S3_SECRET = os.environ.get("CAP283_S3_SECRET", "capadmin123")
S3_BUCKET = os.environ.get("CAP283_S3_BUCKET", "cap-evidence284")


async def _probe() -> bool:
    try:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        return await store.health()
    except Exception:  # noqa: BLE001
        return False


_skip = pytest.mark.skipif(not asyncio.run(_probe()), reason="MinIO not reachable")


@_skip
class TestObjectStore:
    @pytest.mark.asyncio
    async def test_put_is_content_addressed_and_immutable(self) -> None:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        data = b"phase 28.4 durable blob"
        first = await store.put(data, metadata={"url": "http://example.com/a"})
        assert first.key == sha256_hex(data)
        second = await store.put(data, metadata={"url": "http://example.com/b"})
        assert second.key == first.key  # same digest -> same object

    @pytest.mark.asyncio
    async def test_duplicate_content_does_not_duplicate_objects(self) -> None:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        data = b"duplicate-content-check"
        await store.put(data, metadata={})
        await store.put(data, metadata={})
        keys = await store.list_keys()
        assert keys.count(sha256_hex(data)) == 1

    @pytest.mark.asyncio
    async def test_get_returns_exact_bytes_and_verifies_digest(self) -> None:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        data = bytes(range(256))
        obj = await store.put(data, metadata={})
        retrieved = await store.get(obj.key)
        assert retrieved == data
        assert sha256_hex(retrieved) == obj.key

    @pytest.mark.asyncio
    async def test_corruption_is_rejected(self) -> None:
        """Overwrite an object key with different bytes: get() must refuse."""
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        data = b"original bytes that are longer than twelve"
        obj = await store.put(data, metadata={})
        # mutate the stored object: write different content under the same key
        from io import BytesIO

        store._client.put_object(
            store._bucket,
            store.object_key(obj.key),
            BytesIO(b"TAMPERED"),
            length=8,
        )
        with pytest.raises(ObjectStoreError) as exc:
            await store.get(obj.key)
        assert "sha256 mismatch" in str(exc.value)

    @pytest.mark.asyncio
    async def test_list_and_delete(self) -> None:
        store = S3EvidenceStore(
            endpoint=S3_ENDPOINT,
            access_key=S3_ACCESS,
            secret_key=S3_SECRET,
            bucket=S3_BUCKET,
        )
        obj = await store.put(b"list-delete-probe", metadata={})
        assert await store.exists(obj.key) is True
        keys = await store.list_keys()
        assert obj.key in keys
        assert await store.delete(obj.key) is True
        assert await store.exists(obj.key) is False
