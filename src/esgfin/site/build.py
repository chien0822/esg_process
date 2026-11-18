"""S8：由各報告書最新一次成功的 07_final.json 產生 site/index.html（SPEC 12）。

版面依 SPEC 12：儲存格格式 12.2、視覺 12.3、方向圖例 12.4（文字照 SPEC 原文）、null 顯示「—」、頁尾免責聲明。
"""

from __future__ import annotations

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

# SPEC 12.4 方向圖例【已決定 2026-11-12】，原文照用，不得改寫
DIRECTION_LEGEND: tuple[str, ...] = (
    "所有方向都是與這個事件沒發生相比的推估，不是與去年相比，也不代表金額大小。科目排序依影響的直接程度與證據強度排列，同樣不代表金額大小。",
    "資產負債表：上升＝該科目在報導日的帳面金額較高；下降＝較低。資產上升不一定是好事（例如為因應風險增加的設備投資），負債上升表示公司未來要付出的義務增加。",
    "綜合損益表：營業收入上升＝收入增加；費用科目（營業成本、研發費用、推銷及管理費用、利息費用）上升＝費用增加，會使獲利減少；損益科目（資產減損及處分損益、投資類損益、停業單位損益）上升＝利益增加或損失減少，下降＝損失增加或利益減少。",
    "不確定＝找得到影響路徑，但依報告書內容無法判斷金額變大或變小。",
)


def show(v) -> str:
    """null 或空字串顯示為「—」（SPEC 12.1）。"""
    return NULL_TEXT if v is None or (isinstance(v, str) and not v.strip()) else str(v)


def type_text(item: FinalItem) -> str:
    """類型與子類別，例如「轉型風險／政策與法規」；子類別 null 時顯示「轉型風險／—」。"""
    return f"{item.item_type}／{show(item.subcategory)}"


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


def build_site(paths: ProjectPaths, settings: Settings) -> Path:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    env.globals.update(accounts_lines=accounts_lines, show=show, type_text=type_text)
    html = env.get_template("index.html.j2").render(views=collect_latest(paths), ok=REPORT_STATUS_OK, legend=DIRECTION_LEGEND)
    out = paths.site_dir / "index.html"
    atomic_write_text(out, html)
    return out
