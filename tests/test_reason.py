"""S5 P2 推理（M4）：schema、提示組裝、reason_item、--items 試跑。全部用 FakeLLM，不呼叫真實 API。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

import esgfin.constants as C
from esgfin.llm.base import LLMParams, LLMRequest, TextPart
from esgfin.llm.fake import FakeLLM
from esgfin.llm.service import llm_schema
from esgfin.pipeline.errors import RunSetupError
from esgfin.pipeline.runner import RunOptions
from esgfin.reason.stage import CONTEXT_CARD_NONE, accounts_text, reason_item
from esgfin.schemas.p2 import P2Draft, P2Result

from fakes import EXTRACTION, fake_registry, p2_dict

ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "prompts" / "p2_reasoning_v1.md"
REALTEK = "realtek_2024_ESG"
QUANTA = "Quanta_2024_ESG"


def item_json(request: LLMRequest) -> dict:
    text = next(p.text for p in request.parts if isinstance(p, TextPart) and p.text.startswith("【條目全文】"))
    return json.loads(text.split("\n", 1)[1])


def draft_responder(request: LLMRequest) -> dict:
    d = p2_dict(item_json(request)["代碼"])
    d.pop("代碼")
    return d


class RecordingLLM(FakeLLM):
    """另記每次呼叫的模型參數（檢查 reason.thinking_level）。"""

    def __init__(self, scripts):
        super().__init__(scripts)
        self.params: list[LLMParams] = []

    def generate(self, request, params):
        self.params.append(params)
        return super().generate(request, params)


@pytest.fixture
def reason_project(project: Path) -> Path:
    shutil.copy(PROMPT_FILE, project / "prompts" / "p2_reasoning_v1.md")
    path = project / "config" / "settings.yaml"
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    s["reason"] = {"thinking_level": "high"}
    path.write_text(yaml.safe_dump(s, allow_unicode=True), encoding="utf-8")
    return project


def real_registry():
    return fake_registry(reason_item=reason_item)


# ---------------------------------------------------------------- schema


def test_draft_schema_has_no_code_and_premise_between_explanation_and_quote():
    draft = llm_schema(P2Draft)
    assert "代碼" not in draft["properties"]
    assert list(llm_schema(P2Result)["properties"])[0] == "代碼"
    for name in ("BalanceSheetImpact", "IncomeStatementImpact"):
        props = list(draft["$defs"][name]["properties"])
        assert props[props.index("傳導說明") + 1] == "推論前提"
        assert props[props.index("推論前提") + 1] == "原文依據"
    assert draft["$defs"]["BalanceSheetImpact"]["properties"]["科目"]["enum"] == list(C.BALANCE_SHEET_ACCOUNT_NAMES)
    assert draft["$defs"]["IncomeStatementImpact"]["properties"]["科目"]["enum"] == list(C.INCOME_STATEMENT_ACCOUNT_NAMES)
    assert C.DIRECTION_NONE not in str(draft["$defs"]["BalanceSheetImpact"]["properties"]["影響方向"])


def test_premise_and_quote_both_null_passes_pydantic():
    """跨欄位規則（原文依據為空且缺推論前提）由 S6 標記，pydantic 不擋、不觸發重試。"""
    d = p2_dict("x")
    acc = d["潛在受影響會計科目"]["綜合損益表"][0]
    acc.update({"證據等級": "C", "原文依據": None, "推論前提": None})
    P2Result.model_validate(d)


def test_premise_rule_is_minor():
    assert C.P2_CHECK_RULES[C.RULE_PREMISE_MISSING] == C.SEVERITY_MINOR


# ---------------------------------------------------------------- 提示


def test_accounts_text_comes_from_constants():
    text = accounts_text()
    for accounts in (C.BALANCE_SHEET_ACCOUNTS, C.INCOME_STATEMENT_ACCOUNTS):
        for name, desc in accounts.items():
            assert name in text
            if desc:
                assert f"{name}（{desc}）" in text
    for ex in C.EXCLUDED_ITEMS:
        assert ex in text


def test_prompt_contains_required_rules():
    text = PROMPT_FILE.read_text(encoding="utf-8")
    # 方向定義（17.12 A）與兩個對照例子
    assert "這個事件不發生（其他條件不變）" in text
    assert "只有一方變動時填該方的方向" in text
    assert "電費上漲 → 營業成本：上升" in text
    assert "提列設備減損 → 資產減損及處分損益：下降" in text and "不動產、廠房及設備：下降" in text
    # 推論前提（17.12 B）
    assert "推論前提" in text and "假設電費主要為生產用電" in text
    # 使用者 11/12 要求的兩段：以原文內容為準；等級 null 時可讀原文用語
    assert "判斷以條目的「原文內容」為準" in text and "不得只依它推論" in text
    assert "等級為 null 只表示原表的尺度無法換成統一等級，不代表原文沒有資訊" in text
    assert "公司直接營運" in text


# ---------------------------------------------------------------- reason_item（經由 runner）


def test_reason_item_request_and_output(reason_project, make_runner):
    llm = RecordingLLM({"p1_extract": EXTRACTION, "p2_reasoning": draft_responder})
    rec = make_runner(reason_project, registry=real_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    rdir = reason_project / "runs" / rec.run_id / REALTEK
    calls = [c for c in llm.calls if c.prompt_name == "p2_reasoning"]
    assert len(calls) == 2
    req = calls[0]
    texts = [p.text for p in req.parts if isinstance(p, TextPart)]
    assert texts[0] == accounts_text()
    assert "瑞昱半導體股份有限公司" in texts[1] and "2024" in texts[1]
    assert texts[2] == f"【公司脈絡卡】{CONTEXT_CARD_NONE}"
    item = item_json(req)
    assert item["代碼"] == "2379-2024-M1-001" and "原文內容" in item and item["頁碼"] == [2]
    assert "代碼" not in req.response_schema["properties"]
    assert req.system_instruction == PROMPT_FILE.read_text(encoding="utf-8")
    # thinking level 用 reason.thinking_level（設定為 high），不是 llm.thinking_level（medium）
    p2_params = [p for c, p in zip(llm.calls, llm.params) if c.prompt_name == "p2_reasoning"]
    assert {p.thinking_level for p in p2_params} == {"high"}
    assert {p.thinking_level for c, p in zip(llm.calls, llm.params) if c.prompt_name != "p2_reasoning"} == {"medium"}
    out = json.loads((rdir / "05_p2" / "2379-2024-M1-001.json").read_text(encoding="utf-8"))
    assert list(out)[0] == "代碼" and out["代碼"] == "2379-2024-M1-001"
    acc = out["潛在受影響會計科目"]["綜合損益表"][0]
    assert list(acc)[list(acc).index("傳導說明") + 1] == "推論前提"


def test_reason_item_retries_with_errors_then_fails_and_continues(reason_project, make_runner):
    bad = {"理解紀錄": {}}
    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": [bad, draft_responder, bad, bad, bad]})
    rec = make_runner(reason_project, registry=real_registry(), llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    rdir = reason_project / "runs" / rec.run_id / REALTEK
    # 001：第一次無效、附錯誤重問後成功；002：三次都無效 → 推理失敗，流程繼續到 S6、S7
    assert (rdir / "05_p2" / "2379-2024-M1-001.json").exists()
    assert (rdir / "05_p2" / "2379-2024-M1-002.failed.json").exists()
    calls = [c for c in llm.calls if c.prompt_name == "p2_reasoning"]
    assert len(calls) == 5
    assert "沒有通過 JSON schema 驗證" in "".join(p.text for p in calls[1].parts if isinstance(p, TextPart))
    st = rec.reports[REALTEK].stages
    assert st["reason"].items_succeeded == 1 and st["reason"].items_failed == 1 and st["reason"].validation_retries == 3
    assert st["aggregate"].status == "completed"


# ---------------------------------------------------------------- --items 試跑


def test_items_runs_only_selected_and_stops_after_s5(reason_project, make_runner):
    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": draft_responder})
    rec = make_runner(reason_project, registry=real_registry(), llm=llm).run(RunOptions(items=["2379-2024-M1-002"])).record
    calls = [c for c in llm.calls if c.prompt_name == "p2_reasoning"]
    assert [item_json(c)["代碼"] for c in calls] == ["2379-2024-M1-002"]
    data = json.loads((reason_project / "runs" / rec.run_id / "run.json").read_text(encoding="utf-8"))
    assert data["selected_items"] == ["2379-2024-M1-002"]
    for rid, n in ((REALTEK, 1), (QUANTA, 0)):
        st = rec.reports[rid].stages
        assert st["reason"].status == "partial" and st["reason"].items_total == n
        assert "check" not in st  # S6 之後不執行
        assert "試跑（--items）" in rec.reports[rid].error
    rdir = reason_project / "runs" / rec.run_id / REALTEK
    assert (rdir / "05_p2" / "2379-2024-M1-002.json").exists()
    assert not (rdir / "05_p2" / "2379-2024-M1-001.json").exists()


def test_items_then_full_run_hits_cache(reason_project, make_runner):
    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": draft_responder})
    trial = make_runner(reason_project, registry=real_registry(), llm=llm).run(
        RunOptions(reports=["realtek_2024_ESG.pdf"], items=["2379-2024-M1-001"])).record
    llm2 = FakeLLM({"p2_reasoning": draft_responder})
    full = make_runner(reason_project, registry=real_registry(), llm=llm2).run(
        RunOptions(from_stage="reason", source=trial.run_id)).record
    assert [item_json(c)["代碼"] for c in llm2.calls] == ["2379-2024-M1-002"]  # 001 命中快取
    st = full.reports[REALTEK].stages["reason"]
    assert st.status == "completed" and st.items_succeeded == 2 and st.cache_hits == 1


def test_items_code_validation(reason_project, make_runner):
    runner = make_runner(reason_project, registry=real_registry(), llm=FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": draft_responder}))
    with pytest.raises(RunSetupError, match="不屬於本次處理的報告書"):
        runner.run(RunOptions(reports=["realtek_2024_ESG.pdf"], items=["2382-2024-M1-001"]))
    with pytest.raises(RunSetupError, match="不在 P1 結果中"):
        runner.run(RunOptions(reports=["realtek_2024_ESG.pdf"], items=["2379-2024-M1-099"]))
    with pytest.raises(RunSetupError, match="--items 不能與 --resume"):
        runner.run(RunOptions(resume="latest", items=["2379-2024-M1-001"]))


def test_run_py_items_argument():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_cli", ROOT / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)

    args = run.parse_args(["--from-stage", "reason", "--items", "2379-2024-M1-002, 2382-2024-M1-005"])
    assert args.items == ["2379-2024-M1-002", "2382-2024-M1-005"]
    with pytest.raises(SystemExit):
        run.parse_args(["--resume", "--items", "2379-2024-M1-002"])
