"""可替換的模型介面（SPEC 1：正式實驗會換不同廠商的模型比較）。

provider 只負責「送出一次請求、拿回原始文字」；快取、限速、重試、
pydantic 驗證都在 service.py，與廠商無關。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, Union


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class DocumentPart:
    """直接給模型的 PDF（例如 extract.input_mode = text+pdf）。"""

    data: bytes
    mime_type: str = "application/pdf"
    label: str = ""  # 供紀錄用，例如 "realtek_2024_ESG p74-75"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


Part = Union[TextPart, DocumentPart]


@dataclass(frozen=True)
class LLMParams:
    model: str
    thinking_level: str
    max_output_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"model": self.model, "thinking_level": self.thinking_level, "max_output_tokens": self.max_output_tokens}


@dataclass(frozen=True)
class LLMRequest:
    prompt_name: str
    prompt_version: int
    system_instruction: str | None
    parts: tuple[Part, ...]
    response_schema: dict[str, Any]  # JSON Schema，key 用中文，順序 = 生成順序


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.thought_tokens += other.thought_tokens
        self.cached_tokens += other.cached_tokens


@dataclass
class LLMRawResponse:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model_version: str | None = None
    response_id: str | None = None


class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest, params: LLMParams) -> LLMRawResponse:
        """送出一次請求。可重試的錯誤丟 TransientLLMError，其餘丟 PermanentLLMError。"""
        ...


class LLMError(Exception):
    pass


class TransientLLMError(LLMError):
    """429、5xx、逾時、連線錯誤：可退避重試。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PermanentLLMError(LLMError):
    """400、401、403 等：重試無用。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
