"""S8：由各報告書最新一次成功的 07_final.json 產生 site/index.html（SPEC 12）。

M0 只完成「選哪個 run 的結果」與基本版面；投資人版的完整格式在 M5 完成。
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
    env.globals["accounts_lines"] = accounts_lines
    html = env.get_template("index.html.j2").render(views=collect_latest(paths), ok=REPORT_STATUS_OK)
    out = paths.site_dir / "index.html"
    atomic_write_text(out, html)
    return out
