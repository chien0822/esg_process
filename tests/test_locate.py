"""M2：S2 定位（合成 PDF＋假 LLM）。不呼叫真實 API、不載入 Docling。"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pymupdf
import pytest
import yaml

from esgfin.constants import FLAG_CORRECTED_BY_CHILD, FLAG_PARENT_AFTER_CHILD, REPORT_STATUS_NO_TABLE
from esgfin.llm.base import DocumentPart
from esgfin.llm.fake import FakeLLM
from esgfin.locate.layout import analyze, is_nav_line, page_offset, strip_nav
from esgfin.locate.toc import TocEntry, bookmarks_usable, check_rules, find_toc_pages
from esgfin.parse.stage import run_parse
from esgfin.pipeline.runner import RunOptions
from esgfin.schemas.base import read_model
from esgfin.schemas.manifest import Manifest
from esgfin.textnorm import normalize

from fakes import EXTRACTION, fake_registry, reason_responder
from test_parse import CountingParser

REALTEK = "realtek_2024_ESG"
PAGES = 12
OFFSET = 1  # PDF 第 n 頁印頁碼 n-1
NAV = "前言  CH1 永續治理  CH2 綠色營運  附錄"
CONTENT = {
    2: ["目錄 CONTENTS", "前言 01", "CH1 永續治理 03", "治理架構 03", "風險管理 04",
        "CH2 綠色營運 11", "氣候變遷管理 06", "能源管理 08", "附錄 10", "GRI 準則索引 10", "1 2 3 4 5 6 7 8 9"],
    4: ["治理架構", "董事會"],
    5: ["風險管理", "企業風險管理流程"],
    7: ["氣候變遷管理", "氣候風險與機會", "轉型風險 政策與法規 碳費 營運成本增加", "實體風險 颱風 營運中斷"],
    8: ["氣候風險與機會（續）", "機會 再生能源 降低成本 財務影響"],
    9: ["能源管理", "用電量 12,000 MWh"],
    11: ["GRI 準則索引", "GRI 201-2 氣候變遷所產生的財務影響及其他風險與機會 06-07"],
}


def write_report_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for n in range(1, PAGES + 1):
        page = doc.new_page(width=842, height=595)
        page.insert_text((120, 20), NAV, fontname="china-t", fontsize=9)  # 頁首導覽列
        if n > 1:
            page.insert_text((790, 570), str(n - OFFSET), fontsize=9)  # 頁尾頁碼
        for i, line in enumerate(CONTENT.get(n, ["內文"])):
            page.insert_text((100, 120 + 24 * i), line, fontname="china-t", fontsize=11)
    doc.save(path)


TOC_RESPONSE = {
    "目錄項": [
        {"目錄原文": "前言 01", "標題": "前言", "層級": 1, "印刷頁碼": 1, "不確定欄位": []},
        {"目錄原文": "CH1 永續治理 03", "標題": "CH1 永續治理", "層級": 1, "印刷頁碼": 3, "不確定欄位": []},
        {"目錄原文": "治理架構 03", "標題": "治理架構", "層級": 2, "印刷頁碼": 3, "不確定欄位": []},
        {"目錄原文": "風險管理 04", "標題": "風險管理", "層級": 2, "印刷頁碼": 4, "不確定欄位": []},
        # 已知案例（瑞昱 CH3 與 107）：章的頁碼比子項目大，應以子項目校正
        {"目錄原文": "CH2 綠色營運 11", "標題": "CH2 綠色營運", "層級": 1, "印刷頁碼": 11, "不確定欄位": []},
        {"目錄原文": "氣候變遷管理 06", "標題": "氣候變遷管理", "層級": 2, "印刷頁碼": 6, "不確定欄位": []},
        {"目錄原文": "能源管理 08", "標題": "能源管理", "層級": 2, "印刷頁碼": 8, "不確定欄位": []},
        {"目錄原文": "附錄 10", "標題": "附錄", "層級": 1, "印刷頁碼": 10, "不確定欄位": []},
        {"目錄原文": "GRI 準則索引 10", "標題": "GRI 準則索引", "層級": 2, "印刷頁碼": 10, "不確定欄位": []},
    ]
}
ROLES = {"前言": "其他", "CH1 永續治理": "治理", "治理架構": "治理", "風險管理": "治理",
         "CH2 綠色營運": "環境", "氣候變遷管理": "環境", "能源管理": "環境", "附錄": "索引附錄", "GRI 準則索引": "索引附錄"}


def roles_responder(request):
    lines = request.parts[0].text.splitlines()
    out = []
    for line in lines:
        sid, *_, title = line.split("｜")
        out.append({"章節代碼": sid, "理由": "依標題", "角色": ROLES[title]})
    return {"分類": out}


def strategy_responder(request):
    text = request.parts[0].text
    pages = [int(chunk.split(" 頁")[0]) for chunk in text.split("===== PDF 第 ")[1:]]
    return {"判斷": [
        {"PDF頁序": n, "原文摘錄": "氣候風險與機會" if n in (7, 8) else None, "理由": "測試", "是否為策略表": n in (7, 8)}
        for n in pages
    ]}


INDEX_RESPONSE = {"氣候相關揭露": [
    {"索引原文": "GRI 201-2 氣候變遷所產生的財務影響及其他風險與機會 06-07", "索引項目": "GRI 201-2", "指向": "06-07", "印刷頁碼": [6, 7]},
]}


@pytest.fixture
def locate_project(project: Path) -> Path:
    settings_file = project / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    s2 = {"s2_toc": 1, "s2_roles": 1, "s2_strategy": 1, "s2_index": 1}
    settings["prompts"] = {**settings["prompts"], **s2}
    settings["locate"] = {"nav_min_pages": 3}
    settings_file.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    for name in s2:
        (project / "prompts" / f"{name}_v1.md").write_text(f"{name} 測試提示", encoding="utf-8")
    for f in ("realtek_2024_ESG.pdf", "Quanta_2024_ESG.pdf"):
        write_report_pdf(project / "report" / f)
    return project


def locate_llm(strategy=strategy_responder) -> FakeLLM:
    return FakeLLM({
        "s2_toc": TOC_RESPONSE, "s2_roles": roles_responder, "s2_strategy": strategy, "s2_index": INDEX_RESPONSE,
        "p1_extract": EXTRACTION, "p2_reasoning": reason_responder,  # 下游假階段用
    })


def run_one(project, make_runner, llm):
    parser = CountingParser()
    registry = fake_registry(parse=partial(run_parse, parser_factory=lambda name: parser))
    from esgfin.locate.stage import run_locate

    registry.fns["locate"] = run_locate
    rec = make_runner(project, registry=registry, llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"], build_site=False)).record
    return rec, project / "runs" / rec.run_id / REALTEK


def test_manifest_end_to_end(locate_project, make_runner):
    llm = locate_llm()
    rec, rdir = run_one(locate_project, make_runner, llm)
    assert rec.reports[REALTEK].status == "成功", rec.reports[REALTEK].error
    m = read_model(rdir / "02_manifest.json", Manifest)

    assert m.toc_source == "toc_page"
    assert m.page_offset.value == OFFSET
    by_title = {s.title: s for s in m.sections}
    ch2 = by_title["CH2 綠色營運"]
    assert FLAG_PARENT_AFTER_CHILD in ch2.flags and FLAG_CORRECTED_BY_CHILD in ch2.flags
    assert ch2.pdf_page_range == (7, 10) and ch2.printed_page_range == (6, 9)
    assert ch2.pdf_page_range_buffered == (6, 11)
    assert by_title["氣候變遷管理"].pdf_page_range == (7, 8)
    assert ch2.role == "環境" and by_title["GRI 準則索引"].role == "索引附錄"

    assert [t.pdf_pages for t in m.strategy_tables] == [[7, 8]]
    assert m.strategy_tables[0].found_by == "keyword+llm"
    assert m.strategy_tables[0].section_id == by_title["氣候變遷管理"].section_id
    assert m.index_cross_check[0].inside_located_range is True

    # 目錄頁同時給文字與 PDF；導覽列不得讓整個環境章節都成為候選頁
    toc_call = next(c for c in llm.calls if c.prompt_name == "s2_toc")
    assert any(isinstance(p, DocumentPart) for p in toc_call.parts)
    strategy_call = next(c for c in llm.calls if c.prompt_name == "s2_strategy")
    assert "PDF 第 9 頁" not in strategy_call.parts[0].text  # p.9 只有導覽列與「能源管理」
    assert rec.reports[REALTEK].stages["locate"].llm_calls == 4


def test_no_strategy_table_stops_report(locate_project, make_runner):
    def nothing(request):
        res = strategy_responder(request)
        for j in res["判斷"]:
            j["是否為策略表"] = False
        return res

    rec, rdir = run_one(locate_project, make_runner, locate_llm(strategy=nothing))
    r = rec.reports[REALTEK]
    assert r.status == REPORT_STATUS_NO_TABLE and "overrides" in r.error
    m = read_model(rdir / "02_manifest.json", Manifest)
    assert m.strategy_tables == []  # 索引退路也判為否


def test_layout_offset_and_nav(tmp_path):
    pdf = tmp_path / "r.pdf"
    write_report_pdf(pdf)
    layout = analyze(pdf, 0.12, 3)
    assert page_offset(layout, 0.3)[0] == OFFSET
    assert {"CH1", "永續治理", "附錄"} <= layout.nav_tokens
    assert is_nav_line("## CH2 綠色營運 附錄", layout.nav_tokens)
    text, removed = strip_nav("## 前言 CH1 永續治理\n氣候風險與機會\n| 附錄 |", layout.nav_tokens)
    assert removed == ["## 前言 CH1 永續治理"] and "| 附錄 |" in text


def test_bookmarks_usability():
    assert not bookmarks_usable([[1, "封面", 1], [1, "內文", 2], [1, "封底", 9]], 5)[0]
    codes = [[1, "ch1", 3], [2, "1.1", 4], [2, "1.2", 5], [1, "ch2", 6], [2, "2.1", 7]]
    assert not bookmarks_usable(codes, 5)[0]
    titled = [[1, f"第{i}章 標題", i] for i in range(1, 7)]
    assert bookmarks_usable(titled, 5)[0]


def test_rule_checks_flag_without_silent_skip():
    entries = [
        TocEntry("A", 1, "A 3", printed_page=3),
        TocEntry("B", 1, "B 2", printed_page=2),  # 同層未遞增，無子項目可校正
        TocEntry("C", 1, "C 999", printed_page=999),  # 超出總頁數
    ]
    check_rules(entries, "printed_page", 50)
    assert "同層頁碼未遞增" in entries[1].flags
    assert "頁碼超出總頁數" in entries[2].flags and entries[2].printed_page is None


def test_normalize_keeps_hyphenated_codes():
    assert normalize("| GRI 201-2 |\n|---|\n**１，７７０** 萬<br>") == "GRI201-21,770萬"


def test_toc_continuation_page_without_keyword(tmp_path):
    """跨頁目錄：續頁沒有「目錄」字樣，但頁碼行夠多（瑞昱 p.3–4）。"""
    doc = pymupdf.open()
    pages = [
        ["目錄"] + [f"第{i}節 {i * 3}" for i in range(1, 6)] + [str(i) for i in range(10, 16)],
        ["CH3 綠色營運"] + [str(i) for i in range(70, 82)],  # 續頁：12 行只有頁碼
        ["關於本報告書", "本報告書涵蓋 2024 年 1 月 1 日至 12 月 31 日", "5"],
    ]
    for lines in pages:
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((72, 60 + 18 * i), line, fontname="china-t", fontsize=10)
    pdf = tmp_path / "toc.pdf"
    doc.save(pdf)
    assert find_toc_pages(analyze(pdf, 0.12, 5), scan_pages=20, min_numbers=10) == [1, 2]
