"""
Object storage.

Two backends behind one interface:

  local  Development only. Container filesystems are ephemeral, so anything
         written here is lost on the next deploy.
  s3     Any S3-compatible provider — AWS S3, Cloudflare R2, MinIO, Wasabi.
         Selected by `STORAGE_BACKEND=s3` plus `S3_ENDPOINT_URL` where the
         provider is not AWS. No provider is hard-coded.

There is deliberately **no fallback**. Earlier revisions returned
`LocalObjectStorage` for every unrecognised backend value, which meant a
production deployment configured for S3 silently wrote to a disk that would
vanish, while the API reported every upload as successful. `get_object_storage()`
now raises instead, and every write is followed by an existence check so a
`CreativeAsset` row is never marked `completed` for bytes that are not there.

Keys are ownership-bearing:

    organizations/{organization_id}/clients/{client_id}/...

`key_belongs_to_organization()` lets callers verify a key against the requesting
tenant before serving bytes, so a corrupted or forged `storage_key` cannot be
used to read another tenant's asset.
"""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """Base class for storage failures surfaced to callers."""


class StorageConfigurationError(StorageError):
    """The configured backend cannot be constructed (missing/invalid settings)."""


class StorageUnavailableError(StorageError):
    """The backend was reachable in principle but the operation failed."""


class ObjectStorage(ABC):
    #: Identifies the backend in logs, health checks and tests.
    backend: str = "unknown"
    #: True when assets survive a redeploy.
    durable: bool = False

    @abstractmethod
    async def upload(self, data: bytes, key: str, content_type: str) -> str:
        """Persist bytes and return the storage key. Raises on failure."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        ...

    @abstractmethod
    async def get_url(self, key: str) -> str:
        """
        A URL the application can hand to a browser.

        Local returns the authenticated app endpoint. S3 returns a presigned GET
        so bytes are never proxied through the API for large video files.
        """

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes | None:
        """Return the object's bytes, or None when it does not exist."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    async def content_type(self, key: str) -> str | None:
        ...

    async def health_check(self) -> None:
        """Raise if the backend is not usable. Used by the readiness probe."""


# --------------------------------------------------------------------------
# Key helpers — ownership is encoded in the key
# --------------------------------------------------------------------------

_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9._\-]")


def _segment(value: object) -> str:
    """
    Sanitise one path segment.

    Slashes and dot-segments are removed rather than escaped, so a caller
    passing an attacker-controlled value cannot inject extra path levels or
    climb out of the tenant prefix.
    """
    text = _KEY_UNSAFE.sub("_", str(value))
    while ".." in text:
        text = text.replace("..", "_")
    return text.strip(".") or "_"


def build_key(
    *,
    organization_id: UUID | str,
    client_id: UUID | str | None,
    kind: str,
    filename: str,
    campaign_id: UUID | str | None = None,
) -> str:
    parts = ["organizations", _segment(organization_id)]
    if client_id:
        parts += ["clients", _segment(client_id)]
    if campaign_id:
        parts += ["campaigns", _segment(campaign_id)]
    parts.append(_segment(kind))

    # Keep the extension readable while still sanitising both halves.
    stem, _, extension = str(filename).rpartition(".")
    name = f"{_segment(stem)}.{_segment(extension)}" if stem else _segment(filename)
    parts.append(name)
    return "/".join(parts)


def key_belongs_to_organization(key: str | None, organization_id: UUID | str) -> bool:
    """
    Verify a key is inside the tenant's prefix.

    Callers already scope the database query by organization; this is the second
    check that stops a bad `storage_key` value from reaching across tenants.
    """
    if not key:
        return False
    return key.lstrip("/").startswith(f"organizations/{organization_id}/")


# --------------------------------------------------------------------------
# Local
# --------------------------------------------------------------------------


class LocalObjectStorage(ObjectStorage):
    backend = "local"
    durable = False

    def __init__(self, root: str | Path | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_local_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = settings.api_public_url.rstrip("/")

    def _path(self, key: str) -> Path:
        clean = key.lstrip("/").replace("..", "")
        path = (self.root / clean).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError("INVALID_STORAGE_KEY")
        return path

    async def upload(self, data: bytes, key: str, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            path.write_bytes(data)
            path.with_suffix(path.suffix + ".ctype").write_text(content_type, encoding="utf-8")

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise StorageUnavailableError(f"Local write failed for {key}: {exc}") from exc
        return key.lstrip("/")

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def _del() -> None:
            if path.exists():
                path.unlink()
            ctype = path.with_suffix(path.suffix + ".ctype")
            if ctype.exists():
                ctype.unlink()

        await asyncio.to_thread(_del)

    async def get_url(self, key: str) -> str:
        return f"{self.public_base}/api/v1/creative/files/{key}"

    async def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists() or not path.is_file():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        path = self._path(key)
        return path.exists() and path.is_file()

    async def content_type(self, key: str) -> str | None:
        path = self._path(key)
        ctype = path.with_suffix(path.suffix + ".ctype")
        if ctype.exists():
            return ctype.read_text(encoding="utf-8").strip()
        return None


# --------------------------------------------------------------------------
# S3-compatible
# --------------------------------------------------------------------------


class S3ObjectStorage(ObjectStorage):
    backend = "s3"
    durable = True

    def __init__(self, settings: Settings | None = None, *, client=None) -> None:
        settings = settings or get_settings()
        self.bucket = (settings.s3_bucket or "").strip()
        if not self.bucket:
            raise StorageConfigurationError("S3_BUCKET is required when STORAGE_BACKEND=s3.")
        self.signed_url_expiry = max(60, int(settings.s3_signed_url_expiry_seconds or 900))
        self._client = client if client is not None else self._build_client(settings)

    @staticmethod
    def _build_client(settings: Settings):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise StorageConfigurationError(
                "boto3 is required for STORAGE_BACKEND=s3. Install it: pip install boto3"
            ) from exc

        endpoint = (settings.s3_endpoint_url or "").strip() or None
        access_key = (settings.s3_access_key_id or "").strip() or None
        secret_key = (settings.s3_secret_access_key or "").strip() or None

        # Credentials may also arrive from an instance role / IRSA, so only the
        # half-configured case is an error.
        if bool(access_key) != bool(secret_key):
            raise StorageConfigurationError(
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together "
                "(or omit both to use an instance role)."
            )

        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        )
        return boto3.client(
            "s3",
            region_name=(settings.s3_region or "auto").strip() or "auto",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=config,
        )

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        return str(code) in {"404", "NoSuchKey", "NotFound"} or status == 404

    async def upload(self, data: bytes, key: str, content_type: str) -> str:
        clean = key.lstrip("/")

        def _put() -> None:
            self._client.put_object(
                Bucket=self.bucket, Key=clean, Body=data, ContentType=content_type
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            logger.error("S3 upload failed bucket=%s key=%s", self.bucket, clean, exc_info=exc)
            raise StorageUnavailableError(f"Upload failed for {clean}: {exc}") from exc
        return clean

    async def get_bytes(self, key: str) -> bytes | None:
        clean = key.lstrip("/")

        def _get() -> bytes:
            return self._client.get_object(Bucket=self.bucket, Key=clean)["Body"].read()

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            if self._is_missing(exc):
                return None
            logger.error("S3 read failed bucket=%s key=%s", self.bucket, clean, exc_info=exc)
            raise StorageUnavailableError(f"Read failed for {clean}: {exc}") from exc

    async def exists(self, key: str) -> bool:
        clean = key.lstrip("/")

        def _head() -> None:
            self._client.head_object(Bucket=self.bucket, Key=clean)

        try:
            await asyncio.to_thread(_head)
            return True
        except Exception as exc:
            if self._is_missing(exc):
                return False
            logger.error("S3 head failed bucket=%s key=%s", self.bucket, clean, exc_info=exc)
            raise StorageUnavailableError(f"Existence check failed for {clean}: {exc}") from exc

    async def delete(self, key: str) -> None:
        clean = key.lstrip("/")

        def _del() -> None:
            self._client.delete_object(Bucket=self.bucket, Key=clean)

        try:
            await asyncio.to_thread(_del)
        except Exception as exc:
            if self._is_missing(exc):
                return
            raise StorageUnavailableError(f"Delete failed for {clean}: {exc}") from exc

    async def get_url(self, key: str) -> str:
        """Presigned GET. Expires quickly because it bypasses app authorization."""
        clean = key.lstrip("/")

        def _sign() -> str:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": clean},
                ExpiresIn=self.signed_url_expiry,
            )

        try:
            return await asyncio.to_thread(_sign)
        except Exception as exc:
            raise StorageUnavailableError(f"Could not sign URL for {clean}: {exc}") from exc

    async def content_type(self, key: str) -> str | None:
        clean = key.lstrip("/")

        def _head() -> str | None:
            return self._client.head_object(Bucket=self.bucket, Key=clean).get("ContentType")

        try:
            return await asyncio.to_thread(_head)
        except Exception as exc:
            if self._is_missing(exc):
                return None
            raise StorageUnavailableError(f"Metadata read failed for {clean}: {exc}") from exc

    async def health_check(self) -> None:
        def _head_bucket() -> None:
            self._client.head_bucket(Bucket=self.bucket)

        try:
            await asyncio.to_thread(_head_bucket)
        except Exception as exc:
            raise StorageUnavailableError(
                f"Bucket {self.bucket!r} is not reachable: {exc}"
            ) from exc


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------

LOCAL_ALIASES = {"local", "filesystem", "fs"}
S3_ALIASES = {"s3", "r2", "minio", "spaces", "wasabi", "s3-compatible"}

_storage: ObjectStorage | None = None


def get_object_storage() -> ObjectStorage:
    """
    Build the configured backend, or raise.

    Never degrades to local storage: a silent degrade would report uploads as
    successful while writing to a disk that disappears on the next deploy.
    """
    global _storage
    if _storage is not None:
        return _storage

    settings = get_settings()
    backend = (settings.storage_backend or "local").strip().lower()

    if backend in LOCAL_ALIASES:
        if settings.is_production:
            raise StorageConfigurationError(
                "STORAGE_BACKEND=local is not allowed in production: container "
                "filesystems are ephemeral, so every generated asset would be lost "
                "on redeploy. Set STORAGE_BACKEND=s3."
            )
        _storage = LocalObjectStorage()
    elif backend in S3_ALIASES:
        _storage = S3ObjectStorage(settings)
    else:
        raise StorageConfigurationError(
            f"Unknown STORAGE_BACKEND={backend!r}. Supported: "
            f"{', '.join(sorted(LOCAL_ALIASES | S3_ALIASES))}."
        )
    return _storage


def set_object_storage(storage: ObjectStorage | None) -> None:
    """Test seam; also used to rebuild after a configuration change."""
    global _storage
    _storage = storage
