"""LLM 回應快取（cache/llm/）。

key = 雜湊(provider + 模型代碼 + 參數 + 提示名稱與版本 + 完整輸入 + schema)。
只寫入「通過 pydantic 驗證」的回應；失敗的回應不進快取（由 service.py 保證）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from esgfin.schemas.base import atomic_write_text

from .base import DocumentPart, LLMParams, LLMRequest, TextPart, TokenUsage


def cache_key(provider: str, request: LLMRequest, params: LLMParams) -> str:
    parts: list[dict[str, Any]] = []
    for p in request.parts:
        if isinstance(p, TextPart):
            parts.append({"text": p.text})
        elif isinstance(p, DocumentPart):
            parts.append({"document_sha256": p.sha256, "mime_type": p.mime_type})
    payload = {
        "provider": provider,
        "params": params.as_dict(),
        "prompt_name": request.prompt_name,
        "prompt_version": request.prompt_version,
        "system_instruction": request.system_instruction,
        "parts": parts,
        "response_schema": request.response_schema,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LLMCache:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(
        self,
        key: str,
        *,
        provider: str,
        request: LLMRequest,
        params: LLMParams,
        text: str,
        usage: TokenUsage,
        latency_s: float,
        model_version: str | None,
    ) -> None:
        entry = {
            "key": key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "params": params.as_dict(),
            "prompt_name": request.prompt_name,
            "prompt_version": request.prompt_version,
            "model_version": model_version,
            "usage": usage.__dict__,
            "latency_s": latency_s,
            "text": text,
        }
        atomic_write_text(self._path(key), json.dumps(entry, ensure_ascii=False, indent=2))

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
