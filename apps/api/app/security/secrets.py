"""Secret storage abstraction. Never expose decrypted platform tokens to the frontend."""

from __future__ import annotations

import base64
import hashlib
from abc import ABC, abstractmethod

from cryptography.fernet import Fernet

from app.core.config import get_settings


class SecretStore(ABC):
    @abstractmethod
    def store(self, plaintext: str) -> str:
        """Encrypt and return a secret reference / ciphertext blob."""

    @abstractmethod
    def retrieve(self, secret_ref: str) -> str:
        """Decrypt a previously stored secret."""


class LocalFernetSecretStore(SecretStore):
    def __init__(self, key: str | None = None) -> None:
        settings = get_settings()
        raw = (key or settings.encryption_key).encode("utf-8")
        digest = hashlib.sha256(raw).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def store(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def retrieve(self, secret_ref: str) -> str:
        return self._fernet.decrypt(secret_ref.encode("utf-8")).decode("utf-8")


def get_secret_store() -> SecretStore:
    return LocalFernetSecretStore()
