"""LLM 呼叫服務：快取 → 限速 → 重試 → pydantic 驗證 → 驗證失敗附錯誤重問。

- 只有通過 pydantic 驗證的回應才寫入快取；失敗的回應只記在呼叫紀錄與失敗檔
- 每次呼叫（含快取命中）都產生一筆 LLMCallRecord，交給 sink 寫進 llm_calls.jsonl
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from esgfin.schemas.base import StrictModel
from esgfin.schemas.p2 import LLMAttempt
from esgfin.schemas.settings import RetrySettings

from .base import (
    DocumentPart,
    LLMError,
    LLMParams,
    LLMProvider,
    LLMRequest,
    TextPart,
    TokenUsage,
)
from .cache import LLMCache, cache_key
from .ratelimit import RateLimiter
from .retry import RetryOutcome, call_with_retry

M = TypeVar("M", bound=BaseModel)

FEEDBACK_TEMPLATE = (
    "你上一次的回應沒有通過 JSON schema 驗證，錯誤如下：\n{errors}\n\n"
    "請依同一個 schema 重新輸出完整的 JSON，不要只輸出修正的部分。"
)


def llm_schema(model: type[BaseModel]) -> dict[str, Any]:
    """送給模型的 JSON Schema：中文 key，屬性順序 = pydantic 欄位宣告順序。"""
    return model.model_json_schema(by_alias=True, mode="validation")


def estimate_tokens(request: LLMRequest) -> int:
    """限速用的粗估：中文約 1 字 1 token；PDF 每份先以 2,000 token 計。"""
    n = len(request.system_instruction or "")
    for p in request.parts:
        n += len(p.text) if isinstance(p, TextPart) else 2000
    return n


def key_order_issues(data: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, path: str = "$") -> list[str]:
    """回報原始 JSON 中物件 key 順序與 schema 屬性順序不一致的位置。

    用來監測「先推理後結論」是否成立（Gemini 是否保留屬性順序）。
    """
    root = root or schema
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        schema = root.get("$defs", {}).get(name, {})
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            b = branch
            if "$ref" in b:
                b = root.get("$defs", {}).get(b["$ref"].split("/")[-1], {})
            if isinstance(data, dict) and "properties" in b:
                return key_order_issues(data, b, root, path)
            if isinstance(data, list) and b.get("type") == "array":
                return key_order_issues(data, b, root, path)
        return []
    issues: list[str] = []
    if isinstance(data, dict) and "properties" in schema:
        expected = [k for k in schema["properties"] if k in data]
        actual = [k for k in data if k in schema["properties"]]
        if expected != actual:
            issues.append(f"{path}: 預期 {expected}，實際 {actual}")
        for k, sub in schema["properties"].items():
            if k in data:
                issues.extend(key_order_issues(data[k], sub, root, f"{path}.{k}"))
    elif isinstance(data, list) and "items" in schema:
        for i, v in enumerate(data):
            issues.extend(key_order_issues(v, schema["items"], root, f"{path}[{i}]"))
    return issues


class LLMCallRecord(StrictModel):
    """llm_calls.jsonl 的一筆。"""

    timestamp: str
    tag: dict[str, str]
    provider: str
    model: str
    params: dict[str, Any]
    prompt_name: str
    prompt_version: int
    cache_key: str
    cache_hit: bool
    validation_attempt: int  # 0 = 首次；之後為附錯誤重問
    ok: bool
    error: str | None = None
    transient_retries: int = 0
    rate_limit_wait_s: float = 0.0
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0
    key_order_issues: list[str] = []
    model_version: str | None = None
    document_parts: list[str] = []


@dataclass
class StructuredResult(Generic[M]):
    value: M | None
    attempts: list[LLMAttempt] = field(default_factory=list)  # 失敗的嘗試
    records: list[LLMCallRecord] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def first_pass(self) -> bool:
        """首次回應就通過驗證（快取命中也算，因為快取只存通過的回應）。"""
        return self.ok and not self.attempts


class LLMService:
    def __init__(
        self,
        provider: LLMProvider,
        params: LLMParams,
        cache: LLMCache,
        limiter: RateLimiter,
        retry: RetrySettings,
        *,
        validation_retries: int,
        sink: Callable[[LLMCallRecord], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = provider
        self.params = params
        self.cache = cache
        self.limiter = limiter
        self.retry = retry
        self.validation_retries = validation_retries
        self.sink = sink
        self._sleep = sleep

    def _emit(self, rec: LLMCallRecord, result: StructuredResult[Any]) -> None:
        result.records.append(rec)
        if self.sink:
            self.sink(rec)

    def structured(self, request: LLMRequest, output_model: type[M], *, tag: dict[str, str] | None = None) -> StructuredResult[M]:
        tag = tag or {}
        result: StructuredResult[M] = StructuredResult(value=None)
        current = request
        for attempt in range(self.validation_retries + 1):
            key = cache_key(self.provider.name, current, self.params)
            base = dict(
                timestamp=datetime.now(timezone.utc).isoformat(),
                tag=tag,
                provider=self.provider.name,
                model=self.params.model,
                params=self.params.as_dict(),
                prompt_name=current.prompt_name,
                prompt_version=current.prompt_version,
                cache_key=key,
                validation_attempt=attempt,
                document_parts=[p.label or p.sha256[:12] for p in current.parts if isinstance(p, DocumentPart)],
            )

            cached = self.cache.get(key)
            if cached is not None:
                try:
                    value = output_model.model_validate_json(cached["text"])
                except (ValidationError, KeyError, TypeError):
                    self.cache.delete(key)  # 快取檔損壞或 schema 已變：當作沒命中
                else:
                    self._emit(LLMCallRecord(**base, cache_hit=True, ok=True), result)
                    result.value = value
                    return result

            outcome = RetryOutcome()
            waited = self.limiter.acquire(estimate_tokens(current))
            t0 = time.monotonic()
            try:
                raw = call_with_retry(
                    lambda: self.provider.generate(current, self.params), self.retry, outcome, sleep=self._sleep
                )
            except LLMError as e:
                msg = f"{type(e).__name__}: {e}"
                self._emit(
                    LLMCallRecord(
                        **base, cache_hit=False, ok=False, error=msg,
                        transient_retries=outcome.transient_retries, rate_limit_wait_s=waited,
                        latency_s=time.monotonic() - t0,
                    ),
                    result,
                )
                result.attempts.append(LLMAttempt(attempt=attempt, raw_text=None, error=msg))
                result.error = msg
                return result  # API 錯誤（重試用盡或永久錯誤）不再附錯誤重問
            latency = time.monotonic() - t0

            usage_fields = dict(
                transient_retries=outcome.transient_retries,
                rate_limit_wait_s=waited,
                latency_s=latency,
                input_tokens=raw.usage.input_tokens,
                output_tokens=raw.usage.output_tokens,
                thought_tokens=raw.usage.thought_tokens,
                cached_tokens=raw.usage.cached_tokens,
                model_version=raw.model_version,
            )
            try:
                value = output_model.model_validate_json(raw.text)
            except ValidationError as e:
                err = _format_validation_error(e)
                self._emit(LLMCallRecord(**base, cache_hit=False, ok=False, error=err, **usage_fields), result)
                result.attempts.append(LLMAttempt(attempt=attempt, raw_text=raw.text, error=err))
                result.error = err
                current = _with_feedback(request, err)
                continue

            order = key_order_issues(json.loads(raw.text), current.response_schema)
            self._emit(LLMCallRecord(**base, cache_hit=False, ok=True, key_order_issues=order, **usage_fields), result)
            self.cache.put(
                key, provider=self.provider.name, request=current, params=self.params,
                text=raw.text, usage=raw.usage, latency_s=latency, model_version=raw.model_version,
            )
            result.value = value
            result.error = None
            return result
        return result


def _format_validation_error(e: ValidationError) -> str:
    lines = []
    for err in e.errors(include_url=False)[:20]:
        loc = ".".join(str(x) for x in err["loc"]) or "$"
        lines.append(f"- {loc}: {err['msg']}")
    return "\n".join(lines)


def _with_feedback(original: LLMRequest, errors: str) -> LLMRequest:
    """以原始請求為底附上最近一次的錯誤（不累積多輪，避免提示越來越長）。"""
    return LLMRequest(
        prompt_name=original.prompt_name,
        prompt_version=original.prompt_version,
        system_instruction=original.system_instruction,
        parts=original.parts + (TextPart(FEEDBACK_TEMPLATE.format(errors=errors)),),
        response_schema=original.response_schema,
    )


def total_usage(records: list[LLMCallRecord]) -> TokenUsage:
    u = TokenUsage()
    for r in records:
        u.add(TokenUsage(r.input_tokens, r.output_tokens, r.thought_tokens, r.cached_tokens))
    return u
