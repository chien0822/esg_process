"""S0 探索：比對 report/ 與 config/reports.yaml，產生 00_meta.json。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import yaml

from esgfin.constants import REJECTED_INDUSTRY_SECTIONS
from esgfin.schemas.base import write_model
from esgfin.schemas.meta import ReportConfig, ReportMeta, ReportsConfig

from .context import ReportContext
from .errors import RunSetupError
from .paths import ProjectPaths


@dataclass
class Discovery:
    runnable: list[ReportConfig] = field(default_factory=list)
    rejected: list[tuple[ReportConfig, str]] = field(default_factory=list)
    missing_config: list[str] = field(default_factory=list)  # 有 PDF、沒設定
    missing_pdf: list[ReportConfig] = field(default_factory=list)  # 有設定、沒 PDF


def rejection_reason(cfg: ReportConfig) -> str | None:
    code = cfg.industry.section_code
    if code in REJECTED_INDUSTRY_SECTIONS:
        return f"行業大類 {code}（{cfg.industry.section_name}）不處理：{REJECTED_INDUSTRY_SECTIONS[code]}"
    return None


def discover(paths: ProjectPaths, config: ReportsConfig, selected: list[str] | None = None) -> Discovery:
    pdfs = sorted(p.name for p in paths.report_dir.glob("*.pdf")) if paths.report_dir.exists() else []
    pdfs += sorted(p.name for p in paths.report_dir.glob("*.PDF")) if paths.report_dir.exists() else []
    by_file = {r.file: r for r in config.reports}

    if selected:
        unknown = [f for f in selected if f not in pdfs]
        if unknown:
            raise RunSetupError(f"report/ 內找不到：{', '.join(unknown)}")
        no_cfg = [f for f in selected if f not in by_file]
        if no_cfg:
            raise RunSetupError(
                "以下報告書在 config/reports.yaml 沒有設定，請先補上：\n" + yaml_template(no_cfg)
            )
        pdfs = [f for f in pdfs if f in selected]

    d = Discovery()
    for f in pdfs:
        cfg = by_file.get(f)
        if cfg is None:
            d.missing_config.append(f)
            continue
        reason = rejection_reason(cfg)
        if reason:
            d.rejected.append((cfg, reason))
        else:
            d.runnable.append(cfg)
    if not selected:
        d.missing_pdf = [r for r in config.reports if r.file not in pdfs]
    return d


def yaml_template(files: list[str]) -> str:
    """可直接貼進 reports.yaml 的範本。"""
    entries = [
        {
            "file": f,
            "company_full_name": "（請填公司全名）",
            "stock_code": "（請填股票代號）",
            "report_year": 0,
            "industry": {"section_code": "（A–S）", "section_name": "（行業大類名稱）", "version": "待確認", "detail": None},
        }
        for f in files
    ]
    return yaml.safe_dump(entries, allow_unicode=True, sort_keys=False)


def run_explore(ctx: ReportContext) -> None:
    pdf = ctx.pdf_path
    data = pdf.read_bytes()
    meta = ReportMeta(
        report_id=ctx.report_id,
        file=ctx.report.file,
        company_full_name=ctx.report.company_full_name,
        stock_code=ctx.report.stock_code,
        report_year=ctx.report.report_year,
        industry=ctx.report.industry,
        pdf_sha256=hashlib.sha256(data).hexdigest(),
        pdf_size_bytes=len(data),
    )
    write_model(ctx.report_dir / "00_meta.json", meta)
