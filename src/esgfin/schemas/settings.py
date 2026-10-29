"""config/settings.yaml（SPEC 4.2）。"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, field_validator

from .base import StrictModel
from .pages import ParserName


class LLMSettings(StrictModel):
    provider: Literal["gemini"] = "gemini"
    model: str
    # gemini-3.8-flash 只支援 low / medium / high，不可用 minimal
    thinking_level: Literal["low", "medium", "high"] = "medium"
    max_output_tokens: int | None = None  # None = 使用 API 預設
    validation_retries: int = Field(2, ge=0)  # pydantic 驗證失敗時附錯誤重問的次數
    timeout_s: float = Field(600, gt=0)


class RateLimitSettings(StrictModel):
    requests_per_minute: int | None = Field(None, ge=1)  # None = 不限
    tokens_per_minute: int | None = Field(None, ge=1)  # 以估計的輸入 token 計


class RetrySettings(StrictModel):
    """429 與 5xx、連線錯誤的指數退避重試。"""

    max_attempts: int = Field(5, ge=1)
    initial_backoff_s: float = Field(2.0, ge=0)
    multiplier: float = Field(2.0, ge=1)
    max_backoff_s: float = Field(60.0, ge=0)
    jitter: bool = True


class PricingSettings(StrictModel):
    """只用來估算花費，填官方價目表的數字；未填則不估算。"""

    input_usd_per_million_tokens: float | None = None
    output_usd_per_million_tokens: float | None = None  # 思考 token 以輸出價計


class ParseSettings(StrictModel):
    parser: ParserName = "docling"  # 【待確認 15-6】M1 比較後決定
    # 有文字層的頁數比例低於此值即視為掃描檔，停止該份（ver1 不做 OCR）
    min_text_page_ratio: float = Field(default=0.5, gt=0, le=1)


class LocateSettings(StrictModel):
    toc_scan_pages: int = Field(20, ge=1)
    section_page_buffer: int = Field(1, ge=0)


class ExtractSettings(StrictModel):
    input_mode: Literal["text", "text+pdf"] = "text"


class CheckSettings(StrictModel):
    citation_page_buffer: int = Field(1, ge=0)
    fuzzy_pass_ratio: float = Field(0.9, ge=0, le=1)  # 模糊通過門檻，M5 再定


class ContextCardSettings(StrictModel):
    enabled: bool = False

    @field_validator("enabled")
    @classmethod
    def _not_in_ver1(cls, v: bool) -> bool:
        if v:
            raise ValueError("公司脈絡卡在 ver1 未實作，context_card.enabled 必須為 false")
        return v


class SiteSettings(StrictModel):
    port: int = Field(8765, ge=1, le=65535)


class Settings(StrictModel):
    llm: LLMSettings
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    retry: RetrySettings = Field(default_factory=RetrySettings)
    pricing: PricingSettings = Field(default_factory=PricingSettings)
    parse: ParseSettings = Field(default_factory=ParseSettings)
    locate: LocateSettings = Field(default_factory=LocateSettings)
    extract: ExtractSettings = Field(default_factory=ExtractSettings)
    check: CheckSettings = Field(default_factory=CheckSettings)
    context_card: ContextCardSettings = Field(default_factory=ContextCardSettings)
    site: SiteSettings = Field(default_factory=SiteSettings)
    # 提示名稱 → 版本號，對應 prompts/<名稱>_v<版本>.md
    prompts: dict[str, int] = Field(default_factory=dict)

    # 不影響結果的設定：--resume 比對時忽略
    RESULT_NEUTRAL_KEYS: ClassVar[tuple[str, ...]] = ("rate_limit", "retry", "pricing", "site")

    def result_affecting(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        for k in self.RESULT_NEUTRAL_KEYS:
            data.pop(k, None)
        return data
