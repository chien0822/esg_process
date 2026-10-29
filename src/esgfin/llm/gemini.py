"""Google Gemini 實作，使用 google-genai SDK 的 Interactions API。

依官方文件（2026-10 查閱）：
- Interactions API 是新專案的建議介面（generateContent 仍支援）
- structured output：response_format = {"type": "text", "mime_type": "application/json", "schema": ...}
- generation_config.thinking_level：gemini-3.8-flash 只接受 low / medium / high
- generation_config.temperature 在 SDK 中已標為 deprecated，因此不設定
- PDF：input 內放 {"type": "document", "data": <base64>, "mime_type": "application/pdf"}
- usage：total_input_tokens / total_output_tokens / total_thought_tokens / total_cached_tokens
SDK 內建重試已關閉（attempts=1），重試統一由 retry.py 處理，才能正確記錄重試次數。
"""

from __future__ import annotations

import base64
from typing import Any

from .base import (
    DocumentPart,
    LLMParams,
    LLMRawResponse,
    LLMRequest,
    PermanentLLMError,
    TextPart,
    TokenUsage,
    TransientLLMError,
)

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, *, timeout_s: float = 600):
        from google import genai
        from google.genai import types

        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_s * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    @staticmethod
    def build_body(request: LLMRequest, params: LLMParams) -> dict[str, Any]:
        inputs: list[dict[str, Any]] = []
        for p in request.parts:
            if isinstance(p, TextPart):
                inputs.append({"type": "text", "text": p.text})
            elif isinstance(p, DocumentPart):
                inputs.append(
                    {"type": "document", "data": base64.b64encode(p.data).decode("ascii"), "mime_type": p.mime_type}
                )
        gen_config: dict[str, Any] = {"thinking_level": params.thinking_level}
        if params.max_output_tokens is not None:
            gen_config["max_output_tokens"] = params.max_output_tokens
        body: dict[str, Any] = {
            "model": params.model,
            "input": inputs,
            "response_format": {"type": "text", "mime_type": "application/json", "schema": request.response_schema},
            "generation_config": gen_config,
            "store": False,
        }
        if request.system_instruction:
            body["system_instruction"] = request.system_instruction
        return body

    def generate(self, request: LLMRequest, params: LLMParams) -> LLMRawResponse:
        from google.genai._gaos.lib.compat_errors import APIConnectionError, APIError

        try:
            interaction = self._client.interactions.create(**self.build_body(request, params))
        except APIConnectionError as e:  # 含逾時
            raise TransientLLMError(f"連線錯誤：{e}") from e
        except APIError as e:
            code = getattr(e, "status_code", None)
            cls = TransientLLMError if code in TRANSIENT_STATUS else PermanentLLMError
            raise cls(f"HTTP {code}：{e}", status_code=code) from e

        status = getattr(interaction, "status", None)
        if status != "completed":
            # 例如 incomplete（可能撞到輸出上限）或 failed：視為永久錯誤，交由上層記錄
            raise PermanentLLMError(f"interaction 狀態為 {status}；errors={getattr(interaction, 'errors', None)}")
        text = interaction.output_text
        if not text:
            raise PermanentLLMError("回應沒有文字輸出")
        u = interaction.usage
        usage = TokenUsage(
            input_tokens=(u.total_input_tokens or 0) if u else 0,
            output_tokens=(u.total_output_tokens or 0) if u else 0,
            thought_tokens=(u.total_thought_tokens or 0) if u else 0,
            cached_tokens=(u.total_cached_tokens or 0) if u else 0,
        )
        return LLMRawResponse(text=text, usage=usage, model_version=str(interaction.model or params.model), response_id=interaction.id)
