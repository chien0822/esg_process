"""測試用的假階段實作與假 LLM 回應（不打真 API）。"""

from __future__ import annotations

from datetime import datetime

from esgfin.llm.base import LLMRequest, TextPart
from esgfin.llm.service import llm_schema
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.prompts import load_prompt
from esgfin.pipeline.runner import StageRegistry
from esgfin.pipeline.discover import run_explore
from esgfin.schemas.base import read_model, write_jsonl, write_model
from esgfin.schemas.check import CheckReport, P1Check
from esgfin.schemas.final import FinalItem, FinalReport, Metrics, TopAccounts
from esgfin.schemas.manifest import Manifest, StrategyTable
from esgfin.schemas.meta import ReportMeta
from esgfin.schemas.p1 import P1Extraction, P1Item, P1Items, make_item_code
from esgfin.schemas.p2 import P2Failure, P2Result
from esgfin.schemas.pages import PageRecord
from esgfin.site.build import build_site

EVENT_1 = "電價上漲，每年約增加電費 1,770 萬元"
EVENT_2 = "極端降雨造成廠區淹水"


def p1_item_dict(event: str, page: int, item_type: str = "轉型風險", sub: str = "市場") -> dict:
    return {
        "類型": item_type,
        "子類別": sub,
        "子類別來源": "AI歸類",
        "事件描述": event,
        "影響時間": "中",
        "影響時間原文用語": "中期",
        "發生可能性": None,
        "發生可能性原文用語": None,
        "衝擊強度": None,
        "衝擊強度原文用語": None,
        "影響範圍": "全公司",
        "顯示財務Impact": "營運成本增加",
        "因應措施備註": None,
        "原文內容": {"事件描述": event, "顯示財務Impact": "營運成本增加", "因應措施備註": None},
        "頁碼": [page],
    }


EXTRACTION = {
    "column_mapping": [{"原表欄位": "氣候影響事件", "對應欄位": "事件描述"}],
    "items": [p1_item_dict(EVENT_1, 2), p1_item_dict(EVENT_2, 3, "實體風險", "急性")],
}


def p2_dict(code: str, impact: bool = True) -> dict:
    field = {"內容": "x", "原文引用": None, "來源": "AI推論"}
    d = {
        "代碼": code,
        "理解紀錄": {
            "事件是什麼": field,
            "影響公司哪一塊": field,
            "已發生或預期": {"內容": "規劃中或預期", "原文引用": None, "來源": "AI推論"},
            "量化線索": field,
            "公司因應": field,
            "缺口": [{"類型": "成本歸屬或用途", "說明": "未說明電費歸屬"}],
        },
        "傳導管道": [{"管道": "營運成本", "傳導說明": "電價上漲→電費增加→營業成本"}] if impact else [],
        "整體判斷": "有影響" if impact else "無影響",
        "無影響理由類型": None if impact else "描述不足以建立傳導路徑",
        "判斷理由": "理由",
        "潛在受影響會計科目": {
            "資產負債表": [],
            "綜合損益表": [
                {"排序": 1, "科目": "營業成本", "影響方向": "上升", "直接性層級": "一", "證據等級": "B",
                 "發生階段": "規劃中或預期", "傳導說明": "…", "原文依據": "電費", "替代科目": ["推銷及管理費用"]},
            ] if impact else [],
        },
    }
    return d


def reason_responder(request: LLMRequest) -> dict:
    """依請求內文找出代碼，回傳對應的 P2 結果。"""
    text = "".join(p.text for p in request.parts if isinstance(p, TextPart))
    code = text.split("代碼=")[1].split("\n")[0]
    return p2_dict(code)


# ---------------------------------------------------------------- 假階段


def fake_parse(ctx: ReportContext) -> None:
    pages = [
        PageRecord(pdf_page=i, text_md=t, parser="docling", parser_version="test")
        for i, t in enumerate(["封面", EVENT_1, EVENT_2], 1)
    ]
    write_jsonl(ctx.report_dir / "01_pages.jsonl", pages)


def fake_locate(ctx: ReportContext) -> None:
    m = Manifest(
        report_id=ctx.report_id, toc_source="bookmarks", page_offset=None,
        strategy_tables=[StrategyTable(pdf_pages=[2, 3], section_id=None, evidence="氣候", found_by="keyword+llm")],
    )
    write_model(ctx.report_dir / "02_manifest.json", m)


def fake_locate_no_table(ctx: ReportContext) -> None:
    write_model(ctx.report_dir / "02_manifest.json", Manifest(report_id=ctx.report_id, toc_source=None, page_offset=None))


def fake_extract(ctx: ReportContext) -> None:
    info = ctx.prompts["p1_extract"]
    req = LLMRequest("p1_extract", info.version, load_prompt(ctx.paths.root, info), (TextPart("頁面"),), llm_schema(P1Extraction))
    res = ctx.llm.structured(req, P1Extraction, tag=ctx.llm_tag())
    assert res.value is not None, res.error
    meta = read_model(ctx.report_dir / "00_meta.json", ReportMeta)
    items = [
        P1Item(code=make_item_code(meta.stock_code, meta.report_year, i), method="方法一", **d.model_dump())
        for i, d in enumerate(res.value.items, 1)
    ]
    write_model(ctx.report_dir / "03_p1_items.json", P1Items(report_id=ctx.report_id, column_mapping=res.value.column_mapping, items=items))


def fake_p1check(ctx: ReportContext) -> None:
    write_model(ctx.report_dir / "04_p1_check.json", P1Check(report_id=ctx.report_id, items=[], citations=[]))


def fake_reason_item(ctx: ReportContext, item: P1Item) -> P2Result | P2Failure:
    info = ctx.prompts["p2_reasoning"]
    req = LLMRequest("p2_reasoning", info.version, load_prompt(ctx.paths.root, info), (TextPart(f"代碼={item.code}\n"),), llm_schema(P2Result))
    res = ctx.llm.structured(req, P2Result, tag=ctx.llm_tag(item=item.code))
    if res.value is not None:
        return res.value
    return P2Failure(code=item.code, error=res.error or "", attempts=res.attempts)


def fake_check(ctx: ReportContext) -> None:
    write_model(ctx.report_dir / "06_check_report.json", CheckReport(report_id=ctx.report_id, issues=[], citations=[]))


def fake_aggregate(ctx: ReportContext) -> None:
    meta = read_model(ctx.report_dir / "00_meta.json", ReportMeta)
    items = read_model(ctx.report_dir / "03_p1_items.json", P1Items).items
    out = []
    for it in items:
        p = ctx.report_dir / "05_p2" / f"{it.code}.json"
        base = it.model_dump()
        if p.exists():
            r = read_model(p, P2Result)
            out.append(FinalItem(**base, reasoning_status="成功", overall=r.overall,
                                 top_accounts=TopAccounts(income_statement=r.accounts.income_statement[:3] or None,
                                                          balance_sheet=r.accounts.balance_sheet[:3] or None)))
        else:
            out.append(FinalItem(**base, reasoning_status="推理失敗"))
    write_model(ctx.report_dir / "07_final.json",
                FinalReport(report_id=ctx.report_id, run_id=ctx.run_id, generated_at=datetime.now().isoformat(), meta=meta, items=out),
                exclude_none=False)
    write_model(ctx.report_dir / "07_metrics.json", Metrics(scope=ctx.report_id, item_count=len(out)))


def fake_registry(**overrides) -> StageRegistry:
    fns = {
        "explore": run_explore,
        "parse": fake_parse,
        "locate": fake_locate,
        "extract": fake_extract,
        "p1check": fake_p1check,
        "check": fake_check,
        "aggregate": fake_aggregate,
    }
    reason = overrides.pop("reason_item", fake_reason_item)
    fns.update(overrides)
    return StageRegistry(fns=fns, reason_item=reason, build_site=build_site)
