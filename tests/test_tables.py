"""M3：S3a 表格重建的程式檢查、S3 擷取、S4 新規則（SPEC 7.6、7.2–7.4、8）。不打真 API。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from esgfin.check.citation import coverage_ratio, match_quote
from esgfin.check.p1check import run_p1check
from esgfin.extract.stage import run_extract
from esgfin.llm.fake import FakeLLM
from esgfin.pipeline.runner import RunOptions
from esgfin.schemas.base import read_model, write_jsonl, write_model
from esgfin.schemas.check import P1Check
from esgfin.schemas.manifest import Manifest, StrategyTable
from esgfin.schemas.p1 import P1Items, constrained_extraction
from esgfin.schemas.pages import PageRecord, PageTable, TableCell
from esgfin.schemas.tables import ReconstructedTable, TableCheckReport, TableRow
from esgfin.tables.checks import check_table, header_rows
from esgfin.tables.stage import run_tables

from fakes import EXTRACTION, fake_registry, p1_item_dict, reason_responder

REALTEK = "realtek_2024_ESG"
HEADER = ["類型", "子類別", "氣候影響事件"]
RATIO = 0.9


def cells(rows: list[list[str]], spans: dict[tuple[int, int], int] | None = None) -> PageTable:
    """rows[r][c]；空字串＝沒有格子。spans[(r, c)] = 跨幾列。column_header 一律 False（同廣達 p.80）。"""
    spans = spans or {}
    out = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                out.append(TableCell(start_row=r, end_row=r + spans.get((r, c), 1), start_col=c, end_col=c + 1, text=text))
    return PageTable(num_rows=len(rows), num_cols=len(rows[0]), cells=out)


# 第 2 頁：類型「轉型風險」只在第 1 列（實際涵蓋第 1–2 列）；子類別「極端性氣候」被拆成「極端性」「氣候」
P2_GRID = [
    ["類型", "子類別", "氣候影響事件"],
    ["轉型風險", "政策和法規", "碳費上路"],
    ["", "", "電價上漲"],
    ["實體風險", "極端性", "颱風淹水"],
    ["", "氣候", "乾旱缺水"],
]
# 第 3 頁：重印表頭；類型欄沒有標籤（延續前一頁的「實體風險」）
P3_GRID = [["類型", "子類別", "氣候影響事件"], ["", "長期性", "平均氣溫上升"]]
TRUTH = {
    2: [["轉型風險", "政策和法規", "碳費上路"], ["轉型風險", "政策和法規", "電價上漲"],
        ["實體風險", "極端性氣候", "颱風淹水"], ["實體風險", "極端性氣候", "乾旱缺水"]],
    3: [["實體風險", "長期性", "平均氣溫上升"]],
}


def page(n: int, grid: list[list[str]]) -> PageRecord:
    md = "\n".join("| " + " | ".join(r) + " |" for r in grid)
    return PageRecord(pdf_page=n, text_md=md, tables_md=[md], table_cells=[cells(grid)], parser="docling", parser_version="test")


PAGES = {2: page(2, P2_GRID), 3: page(3, P3_GRID)}


def table(rows_by_page: dict[int, list[list[str | None]]], header=HEADER) -> ReconstructedTable:
    rows = [TableRow(pdf_page=p, row_on_page=k, cells=c) for p, rs in rows_by_page.items() for k, c in enumerate(rs, 1)]
    return ReconstructedTable(pages=sorted(rows_by_page), header=header, rows=rows)


def table_json(rows_by_page) -> dict:
    t = table(rows_by_page)
    return {"merge_notes": "第 1 欄：第 1–2 列為轉型風險", "header": t.header, "rows": [r.model_dump() for r in t.rows]}


# ---------------------------------------------------------------- 程式檢查


def test_header_row_found_by_text_not_flag():
    assert header_rows(cells(P2_GRID), HEADER, RATIO) == [0]  # column_header 為 False 仍判為表頭


def test_correct_reconstruction_passes_and_reports_boundary_rows():
    out = check_table(table(TRUTH), PAGES, {}, RATIO)
    assert out.passed, out.problems
    # 第 2 頁第 2 列：類型、子類別都夾在兩個不同的 Docling 值之間 → 邊界待確認
    assert {(b.pdf_page, b.row_on_page, b.column) for b in out.boundary} == {(2, 2, 1), (2, 2, 2)}
    assert out.page_checks[0].docling_header_rows == [0] and out.page_checks[0].docling_data_rows == [1, 2, 3, 4]


def test_split_cell_join_accepted():
    """「極端性」＋「氣候」依序相接＝「極端性氣候」：文字存在與逐欄對照都通過。"""
    out = check_table(table(TRUTH), PAGES, {}, RATIO)
    assert not [p for p in out.problems if p.column == 2]


def test_row_count_mismatch_fails():
    bad = {2: TRUTH[2][:3], 3: TRUTH[3]}
    out = check_table(table(bad), PAGES, {}, RATIO)
    assert any(p.check == "欄數與列數" and p.pdf_page == 2 for p in out.problems)


def test_value_change_inside_docling_value_segment_fails():
    """Docling 在第 3 列放「實體風險」，重建表卻把第 3 列標成轉型風險：該段沒有對應的 Docling 值。"""
    bad = copy.deepcopy(TRUTH)
    bad[2][2][0] = "轉型風險"
    out = check_table(table(bad), PAGES, {}, RATIO)
    assert any(p.check == "逐欄對照" and p.column == 1 for p in out.problems)


def test_null_where_docling_has_value_fails():
    bad = copy.deepcopy(TRUTH)
    bad[2][0][1] = None
    out = check_table(table(bad), PAGES, {}, RATIO)
    assert any(p.check == "逐欄對照" and "空白" in p.message for p in out.problems)


def test_text_not_on_page_fails():
    bad = copy.deepcopy(TRUTH)
    bad[2][1][2] = "電價大幅上漲且停電"
    out = check_table(table(bad), PAGES, {}, RATIO)
    assert any(p.check == "文字存在" for p in out.problems)


def test_continuation_from_previous_page_allowed_only_for_first_segment():
    out = check_table(table(TRUTH), PAGES, {}, RATIO)
    assert out.passed
    bad = copy.deepcopy(TRUTH)
    bad[3][0][0] = "轉型風險"  # 不是前一頁最後一列的值，Docling 也沒有放
    assert not check_table(table(bad), PAGES, {}, RATIO).passed


def test_header_as_data_row_fails():
    bad = copy.deepcopy(TRUTH)
    bad[3] = [HEADER[:], *bad[3]]
    out = check_table(table(bad), PAGES, {}, RATIO)
    assert any(p.check == "表頭不當資料列" for p in out.problems)


def test_wrong_column_count_fails():
    t = table({2: [r + ["x"] for r in TRUTH[2]], 3: [r + ["x"] for r in TRUTH[3]]}, header=HEADER + ["備註"])
    out = check_table(t, PAGES, {}, RATIO)
    assert any(p.check == "欄數與列數" and "4 欄" in p.message for p in out.problems)


def test_coverage_ratio_ignores_item_order():
    quote = "一、導入ISO 50001能源管理系統。二、設置太陽能板。"
    text = "二、設置太陽能板。一、導入ISO 50001能源管理系統。"
    assert match_quote(quote, text, 0.9).level == "模糊通過"
    assert coverage_ratio("完全不同的文字內容", "毫無關聯") == 0.0


# ---------------------------------------------------------------- S3 schema


def test_constrained_extraction_checks_item_count_and_columns():
    data = copy.deepcopy(EXTRACTION)
    checked = constrained_extraction(n_rows=2, n_cols=1)
    checked.model_validate(data)
    with pytest.raises(ValidationError, match="恰好 3 筆"):
        constrained_extraction(n_rows=3, n_cols=1).model_validate(data)
    with pytest.raises(ValidationError, match="欄序"):
        constrained_extraction(n_rows=2, n_cols=2).model_validate(data)


def test_subcategory_and_source_null_together():
    from esgfin.schemas.p1 import P1ItemDraft

    d = p1_item_dict("x")
    P1ItemDraft.model_validate({**d, "子類別": None, "子類別來源": None})
    with pytest.raises(ValidationError, match="同時"):
        P1ItemDraft.model_validate({**d, "子類別": None})


# ---------------------------------------------------------------- 階段（S3a → S3 → S4）


def _item(event, row_event, sub, source, raw, item_type="轉型風險"):
    d = p1_item_dict(event, item_type, sub)
    d.update({"子類別來源": source, "子類別原文用語": raw})
    d["原文內容"] = {"事件描述": row_event, "顯示財務Impact": None, "因應措施備註": None}
    return d


EXTRACT_RESPONSE = {
    "column_mapping": [
        {"欄序": 1, "原表欄位": "類型", "對應欄位": "類型"},
        {"欄序": 2, "原表欄位": "子類別", "對應欄位": "子類別"},
        {"欄序": 3, "原表欄位": "氣候影響事件", "對應欄位": "事件描述"},
    ],
    "items": [
        _item("碳費", "碳費上路", "政策與法規", "原文", "政策和法規"),
        _item("電價", "碳費上路", "市場", "原文", "政策和法規"),  # 錯位引文；同義詞表矛盾
        _item("颱風", "颱風淹水", "急性", "AI歸類", "極端性氣候", "實體風險"),
        _item("乾旱", "乾旱缺水", "急性", "AI歸類", "氣候", "實體風險"),  # 原文用語與對應列不符
        _item("氣溫", "平均氣溫上升", None, None, "長期性", "實體風險"),  # 子類別 null：跳過子類別與類型不符
    ],
}


@pytest.fixture
def m3_project(project: Path) -> Path:
    import pymupdf

    settings_file = project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    settings["prompts"] = {**settings["prompts"], "s3a_table": 1, "s3_extract": 1, "ref_tcfd_examples": 1}
    settings["extract"] = {"input_mode": "text"}
    settings_file.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    for name in ("s3a_table", "s3_extract", "ref_tcfd_examples"):
        (project / "prompts" / f"{name}_v1.md").write_text(f"{name} 測試", encoding="utf-8")
    for f in ("realtek_2024_ESG.pdf", "Quanta_2024_ESG.pdf"):
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page().insert_text((72, 72), "page")
        doc.save(project / "report" / f)
    return project


def fake_parse_cells(ctx) -> None:
    pages = [PageRecord(pdf_page=1, text_md="封面", parser="docling", parser_version="test"), PAGES[2], PAGES[3]]
    write_jsonl(ctx.report_dir / "01_pages.jsonl", pages)


def fake_locate_23(ctx) -> None:
    m = Manifest(report_id=ctx.report_id, toc_source="bookmarks", page_offset=None,
                 strategy_tables=[StrategyTable(pdf_pages=[2, 3], section_id=None, evidence="氣候", found_by="keyword+llm")])
    write_model(ctx.report_dir / "02_manifest.json", m)


def m3_registry():
    return fake_registry(parse=fake_parse_cells, locate=fake_locate_23, tables=run_tables, extract=run_extract, p1check=run_p1check)


def m3_llm(*table_responses) -> FakeLLM:
    return FakeLLM({"s3a_table": list(table_responses), "s3_extract": EXTRACT_RESPONSE, "p2_reasoning": reason_responder})


def test_s3a_retries_on_check_failure_then_s3_s4(m3_project, make_runner):
    bad = table_json({2: TRUTH[2][:3], 3: TRUTH[3]})  # 第 2 頁少一列
    llm = m3_llm(bad, table_json(TRUTH))
    rec = make_runner(m3_project, registry=m3_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    rdir = m3_project / "runs" / rec.run_id / REALTEK
    stages = rec.reports[REALTEK].stages
    assert stages["tables"].status == "completed" and stages["tables"].check_retries == 1
    assert stages["extract"].status == "completed" and stages["p1check"].status == "completed"

    check = read_model(rdir / "03a_table_check.json", TableCheckReport)
    assert check.passed and [a.passed for a in check.tables[0].attempts] == [False, True]
    # 第二次請求附上了檢查錯誤
    retry_req = [c for c in llm.calls if c.prompt_name == "s3a_table"][1]
    assert "沒有通過程式檢查" in retry_req.parts[-1].text and "欄數與列數" in retry_req.parts[-1].text
    assert not any(type(p).__name__ == "DocumentPart" for p in retry_req.parts)  # text 模式不給 PDF

    items = read_model(rdir / "03_p1_items.json", P1Items).items
    assert [(i.source_row.pdf_page, i.source_row.row_on_page, i.pages) for i in items] == [
        (2, 1, [2]), (2, 2, [2]), (2, 3, [2]), (2, 4, [2]), (3, 1, [3])]

    issues = {(i.code[-3:], i.rule) for i in read_model(rdir / "04_p1_check.json", P1Check).issues}
    assert ("002", "引文不在對應列") in issues
    assert ("002", "子類別來源與同義詞表矛盾") in issues
    assert ("004", "子類別原文用語與對應列不符") in issues
    assert not any(code == "005" and rule == "子類別與類型不符" for code, rule in issues)
    assert ("001", "子類別來源與同義詞表矛盾") not in issues


def test_s3a_stops_report_when_checks_keep_failing(m3_project, make_runner):
    bad = table_json({2: TRUTH[2][:3], 3: TRUTH[3]})
    rec = make_runner(m3_project, registry=m3_registry(), llm=m3_llm(bad, bad, bad)).run(
        RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    r = rec.reports[REALTEK]
    rdir = m3_project / "runs" / rec.run_id / REALTEK
    assert r.status == "失敗" and "S3a" in r.error and f"overrides/{REALTEK}/03a_tables.json" in r.error
    assert not (rdir / "03a_tables.json").exists() and (rdir / "03a_tables.failed.json").exists()
    assert "extract" not in r.stages
    check = json.loads((rdir / "03a_table_check.json").read_text(encoding="utf-8"))
    assert check["通過"] is False and len(check["表格"][0]["各次檢查"]) == 3


def test_s3a_override_is_still_checked(m3_project, make_runner):
    odir = m3_project / "overrides" / REALTEK
    odir.mkdir(parents=True)
    t = table(TRUTH)
    (odir / "03a_tables.json").write_text(
        json.dumps({"report_id": REALTEK, "input_mode": "override", "tables": [t.model_dump()]}, ensure_ascii=False), encoding="utf-8")
    llm = m3_llm()
    rec = make_runner(m3_project, registry=m3_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    r = rec.reports[REALTEK]
    assert r.stages["tables"].status == "override" and r.stages["extract"].status == "completed"
    assert [o.stage for o in r.overrides] == ["tables"]
    assert not [c for c in llm.calls if c.prompt_name == "s3a_table"]
    check = read_model(m3_project / "runs" / rec.run_id / REALTEK / "03a_table_check.json", TableCheckReport)
    assert check.input_mode == "override" and check.passed

    # 人工表格未通過檢查 → 停在 S3a
    bad = table({2: TRUTH[2][:3], 3: TRUTH[3]})
    (odir / "03a_tables.json").write_text(
        json.dumps({"report_id": REALTEK, "input_mode": "override", "tables": [bad.model_dump()]}, ensure_ascii=False), encoding="utf-8")
    rec = make_runner(m3_project, registry=m3_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    assert rec.reports[REALTEK].status == "失敗" and "S3a" in rec.reports[REALTEK].error


def test_input_mode_option_overrides_settings(m3_project, make_runner):
    llm = m3_llm(table_json(TRUTH))
    rec = make_runner(m3_project, registry=m3_registry(), llm=llm).run(
        RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False, input_mode="text+pdf")).record
    assert rec.settings["extract"]["input_mode"] == "text+pdf"
    req = [c for c in llm.calls if c.prompt_name == "s3a_table"][0]
    assert any(type(p).__name__ == "DocumentPart" for p in req.parts)
