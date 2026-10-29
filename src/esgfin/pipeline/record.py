"""runs/<run_id>/run.json：每次執行的完整紀錄（SPEC 3.2、3.3）。

記錄 git commit、模型代碼、提示版本、所有參數、起訖時間、每階段 token 與耗時、
哪些階段或條目使用人工輸入、從哪個來源 run 複製。每個階段結束後立即寫檔，中斷也不遺失。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from esgfin.constants import REPORT_STATUS_PENDING, REPORT_STATUSES
from esgfin.llm.service import LLMCallRecord
from esgfin.schemas.base import StrictModel, read_model, write_model
from esgfin.schemas.settings import Settings

StageStatus = Literal[
    "completed",  # 本次執行
    "skipped_existing",  # 輸出已存在且有效（--resume）
    "copied",  # 從來源 run 複製（--from-stage）
    "override",  # 使用 overrides/ 的人工檔
    "failed",
    "not_implemented",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class StageRecord(StrictModel):
    status: StageStatus
    started_at: str | None = None
    ended_at: str | None = None
    duration_s: float = 0.0
    llm_calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    cached_tokens: int = 0
    transient_retries: int = 0
    validation_retries: int = 0
    key_order_issue_calls: int = 0  # 回應 key 順序與 schema 不同的呼叫數
    parse_cache_hit: bool | None = None  # S1：是否直接使用解析快取
    error: str | None = None
    # 階段結束時的程式版本（可追溯每個輸出是哪一版程式產生）
    git_commit: str | None = None
    git_dirty: bool | None = None
    # S5 細項
    items_total: int | None = None
    items_succeeded: int | None = None
    items_failed: int | None = None
    items_skipped: int | None = None

    def add_call(self, rec: LLMCallRecord) -> None:
        self.llm_calls += 0 if rec.cache_hit else 1
        self.cache_hits += 1 if rec.cache_hit else 0
        self.input_tokens += rec.input_tokens
        self.output_tokens += rec.output_tokens
        self.thought_tokens += rec.thought_tokens
        self.cached_tokens += rec.cached_tokens
        self.transient_retries += rec.transient_retries
        self.validation_retries += 1 if rec.validation_attempt > 0 else 0
        self.key_order_issue_calls += 1 if rec.key_order_issues else 0


class OverrideRecord(StrictModel):
    """人工取代紀錄（實驗二需要）。"""

    stage: str
    file: str  # 相對於專案根目錄
    sha256: str
    items: list[str] | None = None  # S5：被取代的條目代碼
    inherited_from_run: str | None = None  # 由 --from-stage 從來源 run 複製時，原始取代發生的 run


class ReportRunRecord(StrictModel):
    report_id: str
    file: str
    status: Literal[REPORT_STATUSES] = REPORT_STATUS_PENDING
    error: str | None = None
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    overrides: list[OverrideRecord] = Field(default_factory=list)
    copied_stages: list[str] = Field(default_factory=list)


class PromptInfo(StrictModel):
    version: int
    file: str
    sha256: str


class GitInfo(StrictModel):
    commit: str | None
    dirty: bool | None


class ResumeEvent(StrictModel):
    at: str
    git: GitInfo
    strict: bool = False


class RunRecord(StrictModel):
    run_id: str
    mode: Literal["new", "from_stage"]
    started_at: str
    ended_at: str | None = None
    source_run_id: str | None = None
    from_stage: str | None = None
    git: GitInfo  # run 開始時
    strict: bool = False  # 以 --strict 開始（正式實驗）
    provider: str
    model: str
    prompts: dict[str, PromptInfo]
    settings: dict[str, Any]
    reports_config: dict[str, dict[str, Any]]  # report_id → reports.yaml 該筆設定
    fingerprint: str  # 會影響結果的設定雜湊；--resume 比對用
    selected_reports: list[str]
    reports: dict[str, ReportRunRecord] = Field(default_factory=dict)
    site_stage: StageRecord | None = None
    resumes: list[ResumeEvent] = Field(default_factory=list)
    estimated_cost_usd: float | None = None


def git_info(root: Path) -> GitInfo:
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return GitInfo(commit=commit, dirty=None if status is None else bool(status))


def fingerprint_components(
    provider: str,
    settings: Settings,
    prompts: dict[str, PromptInfo],
    reports_config: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """會影響結果的所有輸入。--resume 時逐項比對，指出哪裡不同。"""
    affecting = settings.result_affecting()
    return {
        "provider": provider,
        "model": affecting["llm"]["model"],
        "llm_params": {k: v for k, v in affecting["llm"].items() if k != "model"},
        "prompts": {k: v.model_dump() for k, v in sorted(prompts.items())},
        "pipeline_settings": {k: v for k, v in affecting.items() if k not in ("llm", "prompts")},
        "reports_config": reports_config,
    }


def fingerprint_of(components: dict[str, Any]) -> str:
    blob = json.dumps(components, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def components_from_record(rec: RunRecord) -> dict[str, Any]:
    settings = Settings.model_validate(rec.settings)
    return fingerprint_components(rec.provider, settings, rec.prompts, rec.reports_config)


def describe_differences(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    names = {
        "provider": "模型廠商",
        "model": "模型代碼",
        "llm_params": "模型參數",
        "prompts": "提示版本或內容",
        "pipeline_settings": "各階段參數",
        "reports_config": "報告書 metadata（reports.yaml）",
    }
    diffs = []
    for k, label in names.items():
        if old.get(k) != new.get(k):
            diffs.append(f"{label}：原 run = {json.dumps(old.get(k), ensure_ascii=False)}；目前 = {json.dumps(new.get(k), ensure_ascii=False)}")
    return diffs


class RunRecorder:
    """持有 RunRecord，並在每次變動後寫回 run.json。"""

    def __init__(self, record: RunRecord, path: Path):
        self.record = record
        self.path = path

    @classmethod
    def load(cls, path: Path) -> "RunRecorder":
        return cls(read_model(path, RunRecord), path)

    def save(self) -> None:
        write_model(self.path, self.record)

    def report(self, report_id: str) -> ReportRunRecord:
        return self.record.reports[report_id]

    def estimate_cost(self, settings: Settings) -> None:
        p = settings.pricing
        if p.input_usd_per_million_tokens is None or p.output_usd_per_million_tokens is None:
            self.record.estimated_cost_usd = None
            return
        stages = [s for r in self.record.reports.values() for s in r.stages.values()]
        inp = sum(s.input_tokens for s in stages)
        out = sum(s.output_tokens + s.thought_tokens for s in stages)
        self.record.estimated_cost_usd = round(
            inp / 1e6 * p.input_usd_per_million_tokens + out / 1e6 * p.output_usd_per_million_tokens, 4
        )
