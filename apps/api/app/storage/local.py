from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.storage.base import ObjectStorage, StoredObject


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: str | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_local_path)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    async def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> StoredObject:
        path = self._path(key)
        path.write_bytes(data)
        return StoredObject(key=key, url=str(path), content_type=content_type, size=len(data))

    async def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def public_url(self, key: str) -> str | None:
        path = self._path(key)
        return str(path) if path.exists() else None
