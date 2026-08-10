from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationResult:
    success: bool
    status: str
    provider: str
    message: str
    assets: list[dict[str, Any]] = field(default_factory=list)
    external_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    demo: bool = False
    # Raw media when provider returns bytes/base64 inline (image sync providers)
    media_bytes: bytes | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    # Remote URL to download (async providers)
    download_url: str | None = None


class ImageGenerationProvider(ABC):
    name: str

    @abstractmethod
    def configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def generate_image(
        self,
        *,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        meta: dict | None = None,
    ) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def generate_variations(
        self, *, prompt: str, count: int = 3, meta: dict | None = None
    ) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, job_id: str) -> GenerationResult:
        raise NotImplementedError


class VideoGenerationProvider(ABC):
    name: str

    @abstractmethod
    def configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def generate_video(
        self,
        *,
        prompt: str,
        duration_seconds: int = 10,
        aspect_ratio: str = "9:16",
        meta: dict | None = None,
    ) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, provider_job_id: str) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def get_result(self, provider_job_id: str) -> GenerationResult:
        raise NotImplementedError
