from __future__ import annotations

import pytest
from pydantic import ValidationError

from esgfin import constants as C
from esgfin.llm.service import llm_schema
from esgfin.schemas.final import FinalItem, Ratio
from esgfin.schemas.meta import Industry
from esgfin.schemas.p1 import P1Item, P1Items, make_item_code
from esgfin.schemas.p2 import P2Result
from esgfin.schemas.settings import Settings

from fakes import EXTRACTION, p2_dict


def test_account_lists():
    assert len(C.BALANCE_SHEET_ACCOUNT_NAMES) == 11
    assert len(C.INCOME_STATEMENT_ACCOUNT_NAMES) == 8
    assert not set(C.BALANCE_SHEET_ACCOUNT_NAMES) & set(C.INCOME_STATEMENT_ACCOUNT_NAMES)
    assert not set(C.ALL_ACCOUNT_NAMES) & set(C.EXCLUDED_ITEMS)
    assert C.DIRECTION_NONE not in C.DIRECTIONS_LLM


def test_item_code_format():
    assert make_item_code("2379", 2024, 1) == "2379-2024-M1-001"


def test_p1_item_roundtrip_uses_chinese_keys_and_code_first():
    d = {"代碼": "2379-2024-M1-001", "方法": "方法一", **EXTRACTION["items"][0]}
    item = P1Item.model_validate(d)
    dumped = item.model_dump(by_alias=True)
    assert list(dumped)[:3] == ["代碼", "方法", "類型"]
    assert dumped == d
    P1Items.model_validate_json(P1Items(report_id="r", column_mapping=[], items=[item]).model_dump_json())


def test_p1_rejects_values_outside_closed_lists():
    bad = {**EXTRACTION["items"][0], "影響範圍": "全世界"}
    with pytest.raises(ValidationError):
        P1Item.model_validate({"代碼": "x", "方法": "方法一", **bad})


def test_p2_schema_order_reasoning_before_conclusion():
    s = llm_schema(P2Result)
    assert list(s["properties"]) == ["代碼", "理解紀錄", "傳導管道", "整體判斷", "無影響理由類型", "判斷理由", "潛在受影響會計科目"]
    bs = s["$defs"]["BalanceSheetImpact"]
    assert list(bs["properties"])[:3] == ["排序", "科目", "影響方向"]
    assert bs["properties"]["科目"]["enum"] == list(C.BALANCE_SHEET_ACCOUNT_NAMES)
    assert s["$defs"]["IncomeStatementImpact"]["properties"]["科目"]["enum"] == list(C.INCOME_STATEMENT_ACCOUNT_NAMES)


def test_p2_account_must_match_statement_and_direction():
    d = p2_dict("x")
    P2Result.model_validate(d)
    wrong_table = p2_dict("x")
    wrong_table["潛在受影響會計科目"]["資產負債表"] = wrong_table["潛在受影響會計科目"]["綜合損益表"]
    with pytest.raises(ValidationError):
        P2Result.model_validate(wrong_table)
    no_effect = p2_dict("x")
    no_effect["潛在受影響會計科目"]["綜合損益表"][0]["影響方向"] = "不影響"
    with pytest.raises(ValidationError):
        P2Result.model_validate(no_effect)


def test_final_item_no_impact_string():
    d = {"代碼": "c", "方法": "方法一", **EXTRACTION["items"][0], "推理狀態": "成功", "整體判斷": "無影響",
         "潛在受影響會計科目": "無影響", "影響方向": "不影響", "無影響理由類型": "與財務無關"}
    item = FinalItem.model_validate(d)
    assert item.top_accounts == "無影響"


def test_industry_name_must_match_code():
    Industry(section_code="C", section_name="製造業", version="v")
    with pytest.raises(ValidationError):
        Industry(section_code="C", section_name="金融及保險業", version="v")


def test_pooled_ratio_differs_from_mean_of_ratios():
    a = Ratio(numerator=1, denominator=2)  # 0.5
    b = Ratio(numerator=9, denominator=10)  # 0.9
    pooled = Ratio.pooled([a, b])
    assert pooled.value == pytest.approx(10 / 12)
    assert pooled.value != pytest.approx((a.value + b.value) / 2)
    assert Ratio(numerator=0, denominator=0).value is None


def test_settings_reject_minimal_thinking_and_context_card():
    base = {"llm": {"model": "m"}}
    Settings.model_validate(base)
    with pytest.raises(ValidationError):
        Settings.model_validate({"llm": {"model": "m", "thinking_level": "minimal"}})
    with pytest.raises(ValidationError):
        Settings.model_validate({**base, "context_card": {"enabled": True}})
