"""S8：由各報告書最新一次成功的 07_final.json 產生 site/index.html（SPEC 12）。

版面依 SPEC 12：儲存格格式 12.2、視覺 12.3、null 顯示「—」、頁尾免責聲明。
欄位、等級欄顯示規則、推理說明展開區、說明區（方向圖例、證據等級）依 SPEC 17.13（2026-11-19），與 12.1、12.4 衝突時以 17.13 為準。
只改顯示，不改 07_final.json 內容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from esgfin.constants import (
    BALANCE_SHEET,
    IMPACT_NO,
    INCOME_STATEMENT,
    REASONING_CHECK_FAILED,
    REASONING_FAILED,
    REASONING_OK,
    REPORT_STATUS_OK,
)
from esgfin.pipeline.paths import ProjectPaths
from esgfin.pipeline.record import RunRecord
from esgfin.schemas.base import atomic_write_text, read_model
from esgfin.schemas.final import FinalItem, FinalReport
from esgfin.schemas.settings import Settings

TEMPLATES = Path(__file__).parent / "templates"


@dataclass
class ReportView:
    report_id: str
    company_full_name: str
    report_year: int | None
    industry: str
    status: str
    error: str | None
    run_id: str
    analyzed_at: str
    final: FinalReport | None
    note: str | None = None  # 例如「較新的 run 失敗」


def _load_runs(paths: ProjectPaths) -> list[RunRecord]:
    runs: list[RunRecord] = []
    if not paths.runs_dir.exists():
        return runs
    for d in sorted(paths.runs_dir.iterdir()):
        f = d / "run.json"
        if f.exists():
            try:
                runs.append(read_model(f, RunRecord))
            except (ValidationError, ValueError):
                continue
    return runs


def collect_latest(paths: ProjectPaths) -> list[ReportView]:
    """每份報告書：最新一次成功的結果；若沒有成功過，顯示最新一次的失敗原因。"""
    latest_attempt: dict[str, tuple[RunRecord, str, str | None]] = {}
    latest_success: dict[str, tuple[RunRecord, FinalReport]] = {}
    for run in _load_runs(paths):  # run_id 為時間戳，依序由舊到新
        for rid, r in run.reports.items():
            latest_attempt[rid] = (run, r.status, r.error)
            if r.status == REPORT_STATUS_OK:
                final_path = paths.run_report_dir(run.run_id, rid) / "07_final.json"
                try:
                    latest_success[rid] = (run, read_model(final_path, FinalReport))
                except (OSError, ValidationError, ValueError):
                    pass

    views: list[ReportView] = []
    for rid in sorted(latest_attempt):
        run, status, error = latest_attempt[rid]
        cfg = run.reports_config.get(rid, {})
        ind = cfg.get("industry") or {}
        common = dict(
            report_id=rid,
            company_full_name=cfg.get("company_full_name", rid),
            report_year=cfg.get("report_year"),
            industry=f"{ind.get('section_code', '')} {ind.get('section_name', '')}".strip(),
        )
        if rid in latest_success:
            srun, final = latest_success[rid]
            note = None
            if srun.run_id != run.run_id:
                note = f"較新的執行 {run.run_id} 未成功（{status}{'：' + error if error else ''}），此處顯示 {srun.run_id} 的結果"
            views.append(ReportView(**common, status=REPORT_STATUS_OK, error=None, run_id=srun.run_id,
                                    analyzed_at=srun.started_at[:10], final=final, note=note))
        else:
            views.append(ReportView(**common, status=status, error=error, run_id=run.run_id,
                                    analyzed_at=run.started_at[:10], final=None))
    return views


NULL_TEXT = "—"
TAG_RAW = "（原文）"
WHERE_NOT_MENTIONED = "未提及"  # P2 提示：原文沒有提到時「內容」寫「未提及」（prompts/p2_reasoning_v1.md）

# 說明區（SPEC 17.13 第 4 點，2026-11-19 使用者修訂；取代 12.4 的文字），原文照用，不得改寫
DIRECTION_LEGEND: tuple[str, ...] = (
    "所有方向都是與這個事件沒發生相比的推估，科目排序依影響的直接程度與證據強度排列。",
    "資產負債表：上升＝該科目在報導日的帳面金額較高；下降＝較低。",
    "綜合損益表：費用科目上升＝費用增加，會使獲利減少；損益科目上升＝利益增加或損失減少，下降＝損失增加或利益減少。",
    "不確定＝找得到影響路徑，但依報告書內容無法判斷金額變大或變小。",
)
EVIDENCE_GRADE_LEGEND: tuple[str, ...] = (
    "A：報告書直接點名科目類型或給出金額",
    "B：報告書描述了經濟後果，但沒有點名科目",
    "C：報告書沒有直接提及，由系統推論，並列出推論所依賴的前提。",
)

# 推理說明展開區的證據等級短說明（SPEC 17.13 第 3 點；鍵＝constants.EVIDENCE_GRADES）
EVIDENCE_GRADE_SHORT: dict[str, str] = {
    "A": "報告書直接點名科目類型或給出金額",
    "B": "報告書描述經濟後果，未點名科目",
    "C": "報告書未直接提及，由系統推論",
}

_CJK = r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef"
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")


def clean_raw(text: str) -> str:
    """原文用語的顯示：去掉中文字（含全形標點）之間因 Docling 換行產生的空白，其餘連續空白縮成一格。只用於顯示。"""
    return " ".join(_CJK_GAP.sub("", text).split())


def show(v) -> str:
    """null 或空字串顯示為「—」（SPEC 12.1）。"""
    return NULL_TEXT if v is None or (isinstance(v, str) and not v.strip()) else str(v)


def _has(v) -> bool:
    return v is not None and (not isinstance(v, str) or bool(v.strip()))


@dataclass(frozen=True)
class CellLine:
    """儲存格的一行：文字與小字標示（無標示為 None）。"""

    text: str
    tag: str | None = None


def level_cell(level, raw) -> list[CellLine]:
    """影響時間、發生可能性、衝擊強度（SPEC 17.13 第 2 點）：等級 → 原文用語（原文）→ 「—」。"""
    if _has(level):
        return [CellLine(str(level))]
    if _has(raw):
        return [CellLine(clean_raw(raw), TAG_RAW)]
    return [CellLine(NULL_TEXT)]


def scope_cell(item: FinalItem) -> list[CellLine]:
    """影響範圍分兩行：原文（P1 影響範圍，否則影響範圍原文用語＋（原文））；P2「影響公司哪一塊」（不加標示）。"""
    lines: list[CellLine] = []
    if _has(item.scope):
        lines.append(CellLine(str(item.scope)))
    elif _has(item.scope_raw):
        lines.append(CellLine(clean_raw(item.scope_raw), TAG_RAW))
    if _has(item.where) and item.where.strip() != WHERE_NOT_MENTIONED:
        lines.append(CellLine(item.where.strip()))
    return lines or [CellLine(NULL_TEXT)]


def financial_impact_text(item: FinalItem) -> str:
    """公司揭露的財務影響＝P1「顯示財務Impact」；null 顯示「—」。"""
    return item.financial_impact if _has(item.financial_impact) else NULL_TEXT


def accounts_lines(item: FinalItem) -> list[str]:
    """「潛在受影響會計科目」儲存格的各行（SPEC 12.2）。"""
    if item.reasoning_status in (REASONING_FAILED, REASONING_CHECK_FAILED):
        return [item.reasoning_status]
    if item.top_accounts == IMPACT_NO or item.top_accounts is None:
        return [IMPACT_NO]
    lines: list[str] = []
    for title, rows in ((BALANCE_SHEET, item.top_accounts.balance_sheet), (INCOME_STATEMENT, item.top_accounts.income_statement)):
        if rows:
            lines.append(title)
            lines.extend(f"  {i}. {r.account}（{r.direction}）" for i, r in enumerate(rows, 1))
    return lines


@dataclass(frozen=True)
class AccountDetail:
    heading: str  # 科目（方向）
    grade: str  # 證據等級 B：短說明
    explanation: str  # 傳導說明
    premise: str | None  # 推論前提（null 不顯示）


@dataclass(frozen=True)
class Reasoning:
    rationale: str | None
    statements: list[tuple[str, list[AccountDetail]]]  # [(表名, 科目)]，只含網頁顯示的科目

    @property
    def empty(self) -> bool:
        return not self.rationale and not self.statements


def reasoning_detail(item: FinalItem) -> Reasoning:
    """推理說明展開區（SPEC 17.13 第 3 點）。科目只在推理狀態成功且有影響時列出；不含原文引用與頁碼。"""
    rationale = item.rationale.strip() if _has(item.rationale) else None
    statements: list[tuple[str, list[AccountDetail]]] = []
    if item.reasoning_status == REASONING_OK and item.top_accounts not in (None, IMPACT_NO):
        for title, rows in ((BALANCE_SHEET, item.top_accounts.balance_sheet), (INCOME_STATEMENT, item.top_accounts.income_statement)):
            if rows:
                statements.append((title, [
                    AccountDetail(
                        heading=f"{r.account}（{r.direction}）",
                        grade=f"證據等級 {r.evidence_grade}：{EVIDENCE_GRADE_SHORT[r.evidence_grade]}",
                        explanation=r.explanation,
                        premise=r.inference_premise if _has(r.inference_premise) else None,
                    )
                    for r in rows
                ]))
    return Reasoning(rationale=rationale, statements=statements)


def build_site(paths: ProjectPaths, settings: Settings) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    env.globals.update(accounts_lines=accounts_lines, show=show, level_cell=level_cell, scope_cell=scope_cell,
                       financial_impact_text=financial_impact_text, reasoning_detail=reasoning_detail)
    html = env.get_template("index.html.j2").render(
        views=collect_latest(paths), ok=REPORT_STATUS_OK, legend=DIRECTION_LEGEND,
        grade_legend=EVIDENCE_GRADE_LEGEND,
    )
    out = paths.site_dir / "index.html"
    atomic_write_text(out, html)
    return out
