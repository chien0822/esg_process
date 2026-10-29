"""階段定義（SPEC 3.2）與輸出檔有效性判斷。

每份報告書依序跑 S0–S7；S8 網頁是全域階段，在所有報告書之後執行一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from esgfin.schemas.base import read_jsonl, read_model
from esgfin.schemas.check import CheckReport, P1Check
from esgfin.schemas.final import FinalReport, Metrics
from esgfin.schemas.manifest import Manifest
from esgfin.schemas.meta import ReportMeta
from esgfin.schemas.p1 import P1Items
from esgfin.schemas.p2 import P2Result
from esgfin.schemas.pages import PageRecord


@dataclass(frozen=True)
class StageSpec:
    key: str  # --from-stage 用的名稱
    code: str  # S0…S8
    label: str
    outputs: tuple[str, ...]  # 相對於 runs/<run_id>/<report_id>/
    uses_llm: bool
    per_item: bool = False  # S5：一個條目一個檔案


P2_DIR = "05_p2"

REPORT_STAGES: tuple[StageSpec, ...] = (
    StageSpec("explore", "S0", "探索", ("00_meta.json",), False),
    StageSpec("parse", "S1", "解析", ("01_pages.jsonl",), False),
    StageSpec("locate", "S2", "定位", ("02_manifest.json",), True),
    StageSpec("extract", "S3", "方法一擷取", ("03_p1_items.json",), True),
    StageSpec("p1check", "S4", "完整度檢查", ("04_p1_check.json",), False),
    StageSpec("reason", "S5", "P2 推理", (P2_DIR,), True, per_item=True),
    StageSpec("check", "S6", "檢查模組", ("06_check_report.json",), False),
    StageSpec("aggregate", "S7", "彙整", ("07_final.json", "07_metrics.json"), False),
)
SITE_STAGE = StageSpec("site", "S8", "網頁", ("site/index.html",), False)
ALL_STAGES: tuple[StageSpec, ...] = REPORT_STAGES + (SITE_STAGE,)
STAGE_KEYS: tuple[str, ...] = tuple(s.key for s in ALL_STAGES)
STAGE_BY_KEY: dict[str, StageSpec] = {s.key: s for s in ALL_STAGES}

# 單一 JSON 輸出檔 → schema
OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "00_meta.json": ReportMeta,
    "02_manifest.json": Manifest,
    "03_p1_items.json": P1Items,
    "04_p1_check.json": P1Check,
    "06_check_report.json": CheckReport,
    "07_final.json": FinalReport,
    "07_metrics.json": Metrics,
}
JSONL_MODELS: dict[str, type[BaseModel]] = {"01_pages.jsonl": PageRecord}


def stage_index(key: str) -> int:
    return STAGE_KEYS.index(key)


def validate_output_file(path: Path, name: str) -> str | None:
    """回傳 None 表示有效；否則回傳原因。name 為輸出檔名（決定 schema）。"""
    if not path.exists():
        return "檔案不存在"
    try:
        if name in OUTPUT_MODELS:
            read_model(path, OUTPUT_MODELS[name])
        elif name in JSONL_MODELS:
            if not read_jsonl(path, JSONL_MODELS[name]):
                return "檔案沒有任何一筆資料"
        else:
            return f"未知的輸出檔 {name}"
    except (ValidationError, ValueError, OSError) as e:
        return f"未通過 schema 驗證：{str(e)[:300]}"
    return None


def p2_item_path(report_dir: Path, code: str) -> Path:
    return report_dir / P2_DIR / f"{code}.json"


def p2_failure_path(report_dir: Path, code: str) -> Path:
    return report_dir / P2_DIR / f"{code}.failed.json"


def p2_item_valid(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        read_model(path, P2Result)
    except (ValidationError, ValueError, OSError):
        return False
    return True


def stage_complete(report_dir: Path, spec: StageSpec) -> bool:
    """階段輸出是否完整有效（可跳過）。S5 需要每個條目都有有效的成功檔。"""
    if spec.per_item:
        items_path = report_dir / "03_p1_items.json"
        if validate_output_file(items_path, "03_p1_items.json") is not None:
            return False
        items = read_model(items_path, P1Items).items
        return all(p2_item_valid(p2_item_path(report_dir, it.code)) for it in items)
    return all(validate_output_file(report_dir / name, name) is None for name in spec.outputs)
