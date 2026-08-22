"""Phase 28 -- EvidenceObjectStoreProvider (spec 15).

Content-addressed, immutable object storage for raw acquisition artifacts.
Objects are keyed by their SHA-256, never modified in place, and carry a
sidecar metadata record. Large raw pages/files live here -- NOT in the
database (only small metadata rows + hash references go to SQL).

Provider protocol allows future S3 / MinIO / Azure Blob implementations;
this phase ships the LocalFilesystemEvidenceStore for development/testing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class ObjectStoreError(RuntimeError):
    """Raised when an object cannot be stored or retrieved."""


@dataclass
class StoredObject:
    key: str  # content address (sha256)
    size: int
    stored_at: datetime
    metadata: dict[str, Any]


class EvidenceObjectStoreProvider(Protocol):
    """Contract for content-addressed immutable object storage."""

    async def put(self, data: bytes, *, metadata: dict[str, Any]) -> StoredObject: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def metadata(self, key: str) -> dict[str, Any]: ...

    async def list_keys(self) -> list[str]: ...

    async def delete(self, key: str) -> bool: ...


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalFilesystemEvidenceStore:
    """Development/test content-addressed store on the local filesystem.

    Layout::

        <root>/
          objects/ab/cdef...   (immutable content-addressed blobs)
          meta/abcdef....json  (sidecar metadata)
    """

    def __init__(self, root: Path, *, max_object_bytes: int = 20 * 1024 * 1024) -> None:
        self._root = Path(root)
        self._objects = self._root / "objects"
        self._meta = self._root / "meta"
        self._max_object_bytes = max_object_bytes
        self._objects.mkdir(parents=True, exist_ok=True)
        self._meta.mkdir(parents=True, exist_ok=True)

    def _object_path(self, key: str) -> Path:
        return self._objects / key[:2] / key[2:]

    def _meta_path(self, key: str) -> Path:
        return self._meta / f"{key}.json"

    async def put(self, data: bytes, *, metadata: dict[str, Any]) -> StoredObject:
        if not data:
            raise ObjectStoreError("cannot store empty object")
        if len(data) > self._max_object_bytes:
            raise ObjectStoreError(
                f"object {len(data)} bytes exceeds limit {self._max_object_bytes}"
            )
        key = sha256_hex(data)
        path = self._object_path(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            # atomic-ish write: temp file then rename
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
        meta_record = {
            "key": key,
            "size": len(data),
            "stored_at": datetime.now(UTC).isoformat(),
            "metadata": metadata,
        }
        meta_path = self._meta_path(key)
        if not meta_path.exists():
            meta_path.write_text(json.dumps(meta_record, ensure_ascii=False), encoding="utf-8")
        return StoredObject(
            key=key,
            size=len(data),
            stored_at=datetime.now(UTC),
            metadata=dict(metadata),
        )

    async def get(self, key: str) -> bytes:
        path = self._object_path(key)
        if not path.exists():
            raise ObjectStoreError(f"object {key} not found")
        return path.read_bytes()

    async def exists(self, key: str) -> bool:
        return self._object_path(key).exists()

    async def metadata(self, key: str) -> dict[str, Any]:
        meta_path = self._meta_path(key)
        if not meta_path.exists():
            raise ObjectStoreError(f"metadata for {key} not found")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    async def list_keys(self) -> list[str]:
        """All content-address keys currently stored (for GC scans).

        Phase 28.7 fix: blobs live at ``<root>/objects/<d0:2>/<d2:>`` where
        ``d`` is the full 64-char digest -- ``item.name`` alone is the digest
        WITHOUT its first two characters, and returning truncated keys broke
        reconciliation (referenced objects looked orphaned). Rebuild the full
        digest from the shard directory name + file name.
        """
        keys: list[str] = []
        if not self._objects.exists():
            return keys
        for shard in self._objects.iterdir():
            if shard.is_dir():
                keys.extend(
                    f"{shard.name}{item.name}"
                    for item in shard.iterdir()
                    if item.is_file()
                )
        return keys

    async def delete(self, key: str) -> bool:
        path = self._object_path(key)
        meta_path = self._meta_path(key)
        removed = False
        if path.exists():
            path.unlink()
            removed = True
        if meta_path.exists():
            meta_path.unlink()
        return removed

    # -- filesystem hygiene --------------------------------------------------

    @property
    def object_count(self) -> int:
        count = 0
        for _shard in self._objects.iterdir():
            if _shard.is_dir():
                count += len(list(_shard.iterdir()))
        return count


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class S3EvidenceStore:
    """Phase 28.4 -- S3-compatible (MinIO) content-addressed immutable store.

    Object layout: ``sha256/<prefix>/<digest>`` where ``prefix = digest[:2]``.
    Keys are content addresses (the SHA-256 digest itself); a repeated ``put``
    of identical bytes is idempotent and never creates a logical duplicate.
    ``get`` verifies the digest of the returned bytes against the key and
    refuses corrupted objects (integrity gate -- a mismatch raises).
    """

    provider_name = "s3"

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        max_object_bytes: int = 20 * 1024 * 1024,
        metrics: Any | None = None,
    ) -> None:
        from minio import Minio

        self._metrics = metrics
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket
        self._max_object_bytes = max_object_bytes

    @staticmethod
    def object_key(digest: str) -> str:
        return f"sha256/{digest[:2]}/{digest}"

    @staticmethod
    def digest_from_key(key: str) -> str:
        return key.rsplit("/", 1)[-1]

    async def _ensure_bucket(self) -> None:
        from minio.error import InvalidResponseError, S3Error

        try:
            # legacy MinIO requires an explicit location on make_bucket and
            # its bucket_exists can raise for a missing bucket, so creating
            # directly and tolerating "already exists" is the robust path
            self._client.make_bucket(self._bucket, location="us-east-1")
        except S3Error as error:
            if error.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise ObjectStoreError(f"bucket access failed: {error}") from error
        except InvalidResponseError:
            # legacy server quirk: bucket already present
            pass

    async def put(self, data: bytes, *, metadata: dict[str, Any]) -> StoredObject:
        if not data:
            raise ObjectStoreError("cannot store empty object")
        if len(data) > self._max_object_bytes:
            raise ObjectStoreError(
                f"object {len(data)} bytes exceeds limit {self._max_object_bytes}"
            )
        digest = sha256_hex(data)
        key = self.object_key(digest)
        from io import BytesIO

        from minio.error import InvalidResponseError, S3Error

        # NO user metadata is written to the object: legacy MinIO rejects any
        # object carrying MORE THAN ONE x-amz-meta-* header with a bogus
        # SignatureDoesNotMatch (verified empirically; single-key and zero-key
        # puts succeed). Immutable content-addressed objects do not need user
        # metadata -- url/final_url/content_type live in the durable artifact
        # row, and object age comes from Last-Modified in metadata().
        _ = metadata  # accepted for interface compatibility, not stored
        s3_metadata = {}
        await self._ensure_bucket()
        try:
            self._client.stat_object(self._bucket, key)
        except (S3Error, InvalidResponseError):
            self._client.put_object(
                self._bucket,
                key,
                BytesIO(data),
                length=len(data),
                metadata=s3_metadata,
            )
        else:
            pass  # immutable: already stored
        if self._metrics is not None:
            self._metrics.inc("evidence_blob_put_total")
            self._metrics.inc("evidence_blob_bytes", amount=len(data))
        return StoredObject(
            key=digest,
            size=len(data),
            stored_at=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )

    async def get(self, key: str) -> bytes:
        digest = self.digest_from_key(key)
        object_key = self.object_key(digest)
        from minio.error import InvalidResponseError, S3Error

        try:
            response = self._client.get_object(self._bucket, object_key)
            data = response.read()
            response.close()
            response.release_conn()
        except (S3Error, InvalidResponseError) as error:
            raise ObjectStoreError(f"object {key} not found: {error}") from error
        actual = sha256_hex(data)
        if actual != digest:
            raise ObjectStoreError(
                f"object {key} corrupted: sha256 mismatch (expected {digest}, got {actual})"
            )
        return data

    async def exists(self, key: str) -> bool:
        digest = self.digest_from_key(key)
        from minio.error import InvalidResponseError, S3Error

        try:
            self._client.stat_object(self._bucket, self.object_key(digest))
            return True
        except (S3Error, InvalidResponseError):
            return False

    async def metadata(self, key: str) -> dict[str, Any]:
        digest = self.digest_from_key(key)
        from minio.error import InvalidResponseError, S3Error

        try:
            stat = self._client.stat_object(self._bucket, self.object_key(digest))
        except (S3Error, InvalidResponseError) as error:
            raise ObjectStoreError(f"metadata for {key} not found: {error}") from error
        meta = dict(stat.metadata or {})
        # object age source for the orphan GC (Last-Modified is authoritative)
        if stat.last_modified is not None:
            meta["stored_at"] = stat.last_modified.isoformat()
        return meta

    async def list_keys(self) -> list[str]:
        """All stored digests (GC scan source)."""
        await self._ensure_bucket()
        keys: list[str] = []
        try:
            for item in self._client.list_objects(self._bucket, prefix="sha256/", recursive=True):
                keys.append(self.digest_from_key(item.object_name))
        except Exception as error:  # noqa: BLE001
            raise ObjectStoreError(f"object listing failed: {error}") from error
        return keys

    async def delete(self, key: str) -> bool:
        digest = self.digest_from_key(key)
        from minio.error import InvalidResponseError, S3Error

        try:
            self._client.remove_object(self._bucket, self.object_key(digest))
            return True
        except (S3Error, InvalidResponseError):
            return False

    async def health(self) -> bool:
        try:
            await self._ensure_bucket()
            return True
        except Exception:  # noqa: BLE001
            return False
