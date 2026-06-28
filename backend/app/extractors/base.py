from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class ExtractorError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthContext:
    cookie: str | None = None


class BaseExtractor(ABC):
    @abstractmethod
    def supports(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_info(self, url: str, auth: AuthContext | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def download(self, task: dict[str, Any], auth: AuthContext | None = None, progress_hook=None) -> str:
        raise NotImplementedError
