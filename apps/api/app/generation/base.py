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
    demo: bool = False


class ImageGenerationProvider(ABC):
    name: str

    @abstractmethod
    def configured(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def generate_image(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def generate_variations(self, *, prompt: str, count: int = 3, meta: dict | None = None) -> GenerationResult:
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
    async def generate_video(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def generate_variation(self, *, prompt: str, meta: dict | None = None) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self, job_id: str) -> GenerationResult:
        raise NotImplementedError

    @abstractmethod
    async def get_result(self, job_id: str) -> GenerationResult:
        raise NotImplementedError
