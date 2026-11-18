"""M5：S6 檢查規則、S7 彙整與指標、S8 網頁。全部用假資料，不呼叫真實 API。"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from fakes import fake_registry, full_item, p2_dict

import esgfin.constants as C
from esgfin.aggregate.stage import (
    account_metrics,
    final_item,
    pooled_metrics,
    run_aggregate,
    top_accounts,
)
from esgfin.check.p2check import check_citations, check_rules, run_p2check
from esgfin.pipeline.runner import RunOptions
from esgfin.schemas.final import FinalItem, Metrics, Ratio
from esgfin.schemas.p1 import P1Item
from esgfin.schemas.p2 import P2Result
from esgfin.site.build import DIRECTION_LEGEND, accounts_lines

ROOT = Path(__file__).resolve().parents[1]
REALTEK = "realtek_2024_ESG"


def acc(account: str, rank: int, direction: str = "上升", grade: str = "B", quote: str | None = "電費", premise: str | None = None, alts=()):
    return {"排序": rank, "科目": account, "影響方向": direction, "直接性層級": "一", "證據等級": grade, "發生階段": "未說明",
            "傳導說明": "說明", "推論前提": premise, "原文依據": quote, "替代科目": list(alts)}


def make_p2(code="X-001", bs=(), inc=(), impact=True, **kw) -> P2Result:
    d = p2_dict(code, impact=impact)
    d["潛在受影響會計科目"] = {"資產負債表": list(bs), "綜合損益表": list(inc)}
    d.update(kw)
    return P2Result.model_validate(d)


def rules(issues) -> list[str]:
    return [i.rule for i in issues]


# ---------------------------------------------------------------- S6 規則


def test_clean_p2_has_no_issues():
    assert check_rules(make_p2(inc=[acc("營業成本", 1)])) == []


def test_judgement_table_and_reason_mismatch():
    p = make_p2(inc=[])  # 有影響但兩表皆空
    assert rules(check_rules(p)) == [C.RULE_JUDGEMENT_TABLE_MISMATCH]
    p = make_p2(impact=False, inc=[acc("營業成本", 1)])  # 無影響但列出科目
    assert C.RULE_JUDGEMENT_TABLE_MISMATCH in rules(check_rules(p))
    p = make_p2(inc=[acc("營業成本", 1)], 無影響理由類型="與財務無關")
    assert rules(check_rules(p)) == [C.RULE_JUDGEMENT_REASON_MISMATCH]
    p = make_p2(impact=False, 無影響理由類型=None)
    assert rules(check_rules(p)) == [C.RULE_JUDGEMENT_REASON_MISMATCH]


def test_insufficient_without_gap_is_minor():
    d = p2_dict("X", impact=False)
    d["理解紀錄"]["缺口"] = []
    issues = check_rules(P2Result.model_validate(d))
    assert rules(issues) == [C.RULE_INSUFFICIENT_WITHOUT_GAP] and issues[0].severity == C.SEVERITY_MINOR


def test_account_rules_via_mutation():
    """pydantic enum 已擋，S6 仍檢查（schema 放寬時的防線）：直接改屬性模擬。"""
    p = make_p2(bs=[acc("存貨", 1), acc("負債準備", 2)], inc=[acc("營業成本", 1), acc("研發費用", 2)])
    p.accounts.balance_sheet[0].account = "營業成本"  # 放錯表
    p.accounts.balance_sheet[1].account = "現金及約當現金"  # 排除項目（字串與排除清單相同）
    p.accounts.income_statement[1].account = "營業成本"  # 同表重複
    got = rules(check_rules(p))
    assert C.RULE_ACCOUNT_WRONG_STATEMENT in got and C.RULE_ACCOUNT_DUPLICATE in got
    p.accounts.balance_sheet[1].account = "其他科目"
    assert C.RULE_ACCOUNT_NOT_IN_LIST in rules(check_rules(p))
    p.accounts.balance_sheet[1].account = C.EXCLUDED_ITEMS[2]
    assert C.RULE_ACCOUNT_EXCLUDED in rules(check_rules(p))


def test_rank_direction_alternative_and_blank_explanation():
    p = make_p2(inc=[acc("營業成本", 1), acc("研發費用", 3, alts=["研發費用"])])
    p.accounts.income_statement[0].direction = C.DIRECTION_NONE
    p.accounts.income_statement[0].explanation = "  "
    got = rules(check_rules(p))
    assert set(got) == {C.RULE_RANK_NOT_CONTINUOUS, C.RULE_DIRECTION_NONE, C.RULE_EXPLANATION_EMPTY, C.RULE_ALTERNATIVE_EQUALS_MAIN}
    severe = {i.rule for i in check_rules(p) if i.severity == C.SEVERITY_SEVERE}
    assert severe == {C.RULE_RANK_NOT_CONTINUOUS, C.RULE_DIRECTION_NONE}


def test_rank_order_must_match_list_order():
    p = make_p2(inc=[acc("營業成本", 2), acc("研發費用", 1)])
    assert rules(check_rules(p)) == [C.RULE_RANK_NOT_CONTINUOUS]


def test_quote_and_premise_rules():
    p = make_p2(inc=[
        acc("營業成本", 1, grade="A", quote=None, premise="假設"),  # A 缺原文依據
        acc("研發費用", 2, grade="C", quote=None, premise=None),  # 原文依據空且缺推論前提
        acc("推銷及管理費用", 3, grade="C", quote=None, premise="假設電費主要為生產用電"),  # 合規
    ])
    issues = check_rules(p)
    assert rules(issues) == [C.RULE_QUOTE_MISSING_FOR_AB, C.RULE_PREMISE_MISSING]
    assert {i.severity for i in issues} == {C.SEVERITY_MINOR}
    assert issues[0].field == "綜合損益表[1].原文依據" and issues[1].field == "綜合損益表[2].推論前提"


def test_citations_skip_null_and_grade_levels():
    item = P1Item.model_validate(full_item("X-001", page=2))
    d = p2_dict("X-001")
    d["理解紀錄"]["事件是什麼"] = {"內容": "x", "原文引用": "電價上漲", "來源": "原文明示"}
    d["理解紀錄"]["量化線索"] = {"內容": "x", "原文引用": "完全不存在的句子內容", "來源": "原文明示"}
    d["潛在受影響會計科目"]["綜合損益表"] = [acc("營業成本", 1, quote="每年約增加電費"), acc("研發費用", 2, grade="C", quote=None, premise="p")]
    pages = {1: "封面", 2: "電價上漲，每年約增加電費 1,770 萬元", 3: "其他"}
    cits, issues = check_citations(P2Result.model_validate(d), item, pages, buffer=1, ratio=0.9)
    assert [(c.field, c.level) for c in cits] == [
        ("理解紀錄.事件是什麼.原文引用", C.CITATION_EXACT),
        ("理解紀錄.量化線索.原文引用", C.CITATION_FAIL),
        ("綜合損益表[1]（營業成本）.原文依據", C.CITATION_EXACT),
    ]  # null 的原文引用、原文依據不驗證
    assert cits[0].pages_searched == [1, 2, 3]
    assert rules(issues) == [C.RULE_CITATION_FAIL]


# ---------------------------------------------------------------- S7


def test_top_three_per_statement_and_empty_table_omitted():
    inc = [acc(a, i) for i, a in enumerate(C.INCOME_STATEMENT_ACCOUNT_NAMES[:5], 1)]
    p = make_p2(inc=inc)
    top = top_accounts(p)
    assert [r.account for r in top.income_statement] == list(C.INCOME_STATEMENT_ACCOUNT_NAMES[:3])
    assert top.balance_sheet is None
    assert list(json.loads(top.model_dump_json())) == ["綜合損益表"]  # 空的表不輸出


def test_final_item_status_precedence():
    item = P1Item.model_validate(full_item("X-001"))
    assert final_item(item, None, {"X-001"}).reasoning_status == C.REASONING_FAILED  # 推理失敗優先
    p = make_p2("X-001", inc=[acc("營業成本", 1)])
    f = final_item(item, p, {"X-001"})
    assert f.reasoning_status == C.REASONING_CHECK_FAILED and f.top_accounts is None
    assert accounts_lines(f) == [C.REASONING_CHECK_FAILED]
    f = final_item(item, make_p2("X-001", impact=False), set())
    assert f.top_accounts == C.IMPACT_NO and f.direction == C.DIRECTION_NONE and f.no_impact_reason is not None
    assert accounts_lines(f) == [C.IMPACT_NO]


def test_certainty_ratio_all_and_top_and_c_null_ratio():
    p1 = make_p2(inc=[acc("營業成本", 1), acc("研發費用", 2, "不確定"), acc("推銷及管理費用", 3), acc("利息費用", 4, "不確定")],
                 bs=[acc("存貨", 1, "下降", grade="C", quote=None, premise="p")])
    p2 = make_p2(impact=False)  # 無影響不計入分子分母
    m = account_metrics([p1, p2])
    assert (m["all"].numerator, m["all"].denominator) == (3, 5)
    assert (m["top"].numerator, m["top"].denominator) == (3, 4)  # 第 4 名不在前三
    assert (m["c_null"].numerator, m["c_null"].denominator) == (1, 5)


def test_pooled_ratios_use_summed_counts():
    a = Metrics(scope="a", certainty_all=Ratio(numerator=1, denominator=1), inconsistent=Ratio(numerator=0, denominator=1))
    b = Metrics(scope="b", certainty_all=Ratio(numerator=1, denominator=3), inconsistent=Ratio(numerator=2, denominator=9))
    t = pooled_metrics([a, b])
    assert t.certainty_all.value == pytest.approx(2 / 4)  # 不是 (1 + 1/3) / 2
    assert t.inconsistent.value == pytest.approx(2 / 10)
    assert t.scope == "全部"


# ---------------------------------------------------------------- 經由 runner（S4 假、S6/S7 真）


def m5_registry():
    return fake_registry(check=run_p2check, aggregate=run_aggregate)


def test_end_to_end_inconsistency_and_metrics(project, make_runner):
    from fakes import EXTRACTION, reason_responder

    from esgfin.llm.fake import FakeLLM

    def responder(request):
        d = reason_responder(request)
        if d["代碼"].endswith("002"):  # 002：排序不連續 → 嚴重 → 檢查未通過
            d = copy.deepcopy(d)
            d["潛在受影響會計科目"]["綜合損益表"][0]["排序"] = 2
        return d

    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": responder})
    rec = make_runner(project, registry=m5_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    assert rec.reports[REALTEK].status == C.REPORT_STATUS_OK
    run_dir = project / "runs" / rec.run_id
    rdir = run_dir / REALTEK
    check = json.loads((rdir / "06_check_report.json").read_text(encoding="utf-8"))
    assert [(i["代碼"], i["規則"]) for i in check["問題"]] == [("2379-2024-M1-002", C.RULE_RANK_NOT_CONTINUOUS)]
    final = json.loads((rdir / "07_final.json").read_text(encoding="utf-8"))
    assert [it["推理狀態"] for it in final["items"]] == [C.REASONING_OK, C.REASONING_CHECK_FAILED]
    assert "資產負債表" not in final["items"][0]["潛在受影響會計科目"]
    m = json.loads((rdir / "07_metrics.json").read_text(encoding="utf-8"))
    assert m["不一致比例"] == {"分子": 1, "分母": 2, "比例": 0.5}
    assert m["問題比例_S6"][C.RULE_RANK_NOT_CONTINUOUS]["分子"] == 1
    assert m["問題比例_S6"][C.RULE_PREMISE_MISSING]["分子"] == 0  # 分級表全部規則都列出
    assert m["證據等級C且原文依據null比例"] == {"分子": 0, "分母": 2, "比例": 0.0}
    assert m["JSON首次通過率"]["分母"] == 3  # S3 一次 + S5 兩次
    assert m["引文驗證_P2"]["完全通過"] == 2 and m["引文驗證_P2"]["通過率"]["比例"] == 1.0
    assert "S5 P2 推理" in m["階段耗時與token"]
    total = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    assert total["範圍"] == "全部" and total["不一致比例"]["分子"] == 1

    html = (project / "site" / "index.html").read_text(encoding="utf-8")
    assert C.REASONING_CHECK_FAILED in html and "本頁為研究系統輸出，不構成投資建議" in html


def test_from_stage_check_reuses_stage_usage_without_llm(project, make_runner):
    from fakes import EXTRACTION, reason_responder

    from esgfin.llm.fake import FakeLLM

    first = make_runner(project, registry=m5_registry(), llm=FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": reason_responder})).run(
        RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    llm2 = FakeLLM({})
    rec = make_runner(project, registry=m5_registry(), llm=llm2).run(RunOptions(from_stage="check", source=first.run_id)).record
    assert llm2.calls == []
    m = json.loads((project / "runs" / rec.run_id / REALTEK / "07_metrics.json").read_text(encoding="utf-8"))
    assert m["JSON首次通過率"]["分母"] == 3  # 從來源 run 的 llm_calls.jsonl 取得


# ---------------------------------------------------------------- S8


def _spec_legend() -> list[str]:
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    block = spec.split("### 12.4", 1)[1].split("---", 1)[0]
    return [line[2:].strip() for line in block.splitlines() if line.startswith("- ")]


def test_legend_is_verbatim_from_spec():
    assert list(DIRECTION_LEGEND) == _spec_legend()


def test_site_null_dash_and_cell_format(project, make_runner):
    from fakes import EXTRACTION, reason_responder

    from esgfin.llm.fake import FakeLLM

    make_runner(project, registry=m5_registry(), llm=FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": reason_responder})).run(
        RunOptions(reports=["realtek_2024_ESG.pdf"]))
    html = (project / "site" / "index.html").read_text(encoding="utf-8")
    for line in DIRECTION_LEGEND:
        assert line in html
    assert html.index("方向圖例") < html.index("<table")
    assert "<td>—</td>" in html  # 發生可能性 null
    assert "None" not in re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    assert '<div class="stmt">綜合損益表</div><div class="acc">1. 營業成本（上升）</div>' in html
    assert "轉型風險／市場" in html


def test_accounts_lines_order_balance_sheet_first():
    item = P1Item.model_validate(full_item("X-001"))
    f = final_item(item, make_p2("X-001", bs=[acc("存貨", 1, "下降")], inc=[acc("營業成本", 1)]), set())
    assert accounts_lines(f) == ["資產負債表", "  1. 存貨（下降）", "綜合損益表", "  1. 營業成本（上升）"]
    assert isinstance(f, FinalItem)
