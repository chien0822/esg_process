from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from esgfin.llm.base import PermanentLLMError
from esgfin.llm.fake import FakeLLM
from esgfin.pipeline.errors import RunSetupError
from esgfin.pipeline.runner import RunOptions

from fakes import EXTRACTION, fake_locate_no_table, fake_registry, p2_dict, reason_responder

REALTEK = "realtek_2024_ESG"
QUANTA = "Quanta_2024_ESG"


def run_json(root: Path, run_id: str) -> dict:
    return json.loads((root / "runs" / run_id / "run.json").read_text(encoding="utf-8"))


def test_new_run_end_to_end(project, make_runner, fake_llm):
    rec = make_runner(project).run(RunOptions()).record
    assert {r.status for r in rec.reports.values()} == {"成功"}
    rdir = project / "runs" / rec.run_id / REALTEK
    for name in ["00_meta.json", "01_pages.jsonl", "02_manifest.json", "03_p1_items.json", "04_p1_check.json",
                 "06_check_report.json", "07_final.json", "07_metrics.json", "llm_calls.jsonl"]:
        assert (rdir / name).exists(), name
    assert (rdir / "05_p2" / "2379-2024-M1-001.json").exists()
    assert (project / "site" / "index.html").exists()
    # run.json 記錄模型、提示版本與雜湊、token
    data = run_json(project, rec.run_id)
    assert data["model"] == "gemini-3.8-flash"
    assert data["prompts"]["p2_reasoning"]["version"] == 1 and len(data["prompts"]["p2_reasoning"]["sha256"]) == 64
    reason = data["reports"][REALTEK]["stages"]["reason"]
    assert reason["items_total"] == 2 and reason["items_succeeded"] == 2 and reason["input_tokens"] > 0
    # 兩份報告書的假 extract 請求相同 → 第二份命中快取
    hits = [data["reports"][r]["stages"]["extract"]["cache_hits"] for r in (QUANTA, REALTEK)]
    assert sorted(hits) == [0, 1]
    html = (project / "site" / "index.html").read_text(encoding="utf-8")
    assert "瑞昱半導體股份有限公司" in html and rec.run_id in html and "營業成本（上升）" in html


def test_single_report_option(project, make_runner):
    rec = make_runner(project).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    assert list(rec.reports) == [REALTEK]


def test_resume_skips_completed_stages(project, make_runner, fake_llm):
    rec = make_runner(project).run(RunOptions()).record
    n_calls = len(fake_llm.calls)
    rec2 = make_runner(project).run(RunOptions(resume=rec.run_id)).record
    assert rec2.run_id == rec.run_id
    assert len(fake_llm.calls) == n_calls
    stages = rec2.reports[REALTEK].stages
    assert all(s.status == "skipped_existing" for k, s in stages.items()), stages
    assert len(rec2.resumes) == 1


def test_resume_refuses_when_settings_change(project, make_runner):
    rec = make_runner(project).run(RunOptions()).record
    path = project / "config" / "settings.yaml"
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    s["llm"]["thinking_level"] = "high"
    path.write_text(yaml.safe_dump(s, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RunSetupError, match="模型參數"):
        make_runner(project).run(RunOptions(resume=rec.run_id))


def test_resume_refuses_when_prompt_content_changes_without_version_bump(project, make_runner):
    make_runner(project).run(RunOptions()).record
    (project / "prompts" / "p2_reasoning_v1.md").write_text("偷偷改了", encoding="utf-8")
    with pytest.raises(RunSetupError, match="提示版本或內容"):
        make_runner(project).run(RunOptions(resume="latest"))


def test_resume_refuses_when_metadata_changes(project, make_runner):
    make_runner(project).run(RunOptions())
    path = project / "config" / "reports.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["reports"][0]["report_year"] = 2023
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RunSetupError, match="reports.yaml"):
        make_runner(project).run(RunOptions(resume="latest"))


def test_failed_items_are_retried_on_resume_and_downstream_rerun(project, make_runner):
    # 第一次：001 成功、002 三次都回無效 JSON → 推理失敗
    def flaky(request):
        text = request.parts[0].text
        if "M1-002" in text:
            return "not json"
        return reason_responder(request)

    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": flaky})
    rec = make_runner(project, llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    rdir = project / "runs" / rec.run_id / REALTEK
    assert (rdir / "05_p2" / "2379-2024-M1-002.failed.json").exists()
    assert not (rdir / "05_p2" / "2379-2024-M1-002.json").exists()
    assert rec.reports[REALTEK].stages["reason"].items_failed == 1
    final = json.loads((rdir / "07_final.json").read_text(encoding="utf-8"))
    assert [i["推理狀態"] for i in final["items"]] == ["成功", "推理失敗"]
    # 失敗的回應沒有進快取
    cached = [json.loads(p.read_text(encoding="utf-8"))["text"] for p in (project / "cache" / "llm").rglob("*.json")]
    assert "not json" not in cached

    # 接續：只重試 002，然後 S6、S7 重跑
    llm2 = FakeLLM({"p2_reasoning": reason_responder})
    rec2 = make_runner(project, llm=llm2).run(RunOptions(resume=rec.run_id)).record
    assert len(llm2.calls) == 1 and "M1-002" in llm2.calls[0].parts[0].text
    assert (rdir / "05_p2" / "2379-2024-M1-002.json").exists()
    assert not (rdir / "05_p2" / "2379-2024-M1-002.failed.json").exists()
    st = rec2.reports[REALTEK].stages
    assert st["reason"].items_skipped == 1 and st["reason"].items_succeeded == 1
    assert st["check"].status == "completed" and st["aggregate"].status == "completed"
    final = json.loads((rdir / "07_final.json").read_text(encoding="utf-8"))
    assert [i["推理狀態"] for i in final["items"]] == ["成功", "成功"]


def test_from_stage_creates_new_run_and_keeps_source(project, make_runner, fake_llm):
    src = make_runner(project).run(RunOptions()).record
    src_files = {p: p.read_bytes() for p in (project / "runs" / src.run_id).rglob("*") if p.is_file()}

    # 升級 P2 提示版本
    (project / "prompts" / "p2_reasoning_v2.md").write_text("推理提示 v2", encoding="utf-8")
    path = project / "config" / "settings.yaml"
    s = yaml.safe_load(path.read_text(encoding="utf-8"))
    s["prompts"]["p2_reasoning"] = 2
    path.write_text(yaml.safe_dump(s, allow_unicode=True), encoding="utf-8")

    n_extract = sum(1 for c in fake_llm.calls if c.prompt_name == "p1_extract")
    new = make_runner(project).run(RunOptions(from_stage="reason")).record
    assert new.run_id != src.run_id
    assert new.mode == "from_stage" and new.source_run_id == src.run_id and new.from_stage == "reason"
    assert new.prompts["p2_reasoning"].version == 2
    st = new.reports[REALTEK].stages
    assert [st[k].status for k in ["explore", "parse", "locate", "extract", "p1check"]] == ["copied"] * 5
    assert st["reason"].status == "completed" and st["reason"].llm_calls == 2
    assert sum(1 for c in fake_llm.calls if c.prompt_name == "p1_extract") == n_extract  # 沒重新擷取
    # 來源 run 完全沒被動到
    assert {p: p.read_bytes() for p in (project / "runs" / src.run_id).rglob("*") if p.is_file()} == src_files


def test_from_stage_with_explicit_source(project, make_runner):
    first = make_runner(project).run(RunOptions()).record
    make_runner(project).run(RunOptions())  # 較新的 run
    new = make_runner(project).run(RunOptions(from_stage="check", source=first.run_id)).record
    assert new.source_run_id == first.run_id


def test_stage_override_recorded(project, make_runner):
    odir = project / "overrides" / REALTEK
    odir.mkdir(parents=True)
    manifest = {"report_id": REALTEK, "toc_source": None, "page_offset": None,
                "strategy_tables": [{"pdf_pages": [2], "section_id": None, "evidence": "人工指定", "found_by": "override"}]}
    (odir / "02_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    rec = make_runner(project).run(RunOptions()).record
    r = rec.reports[REALTEK]
    assert r.stages["locate"].status == "override"
    assert [o.stage for o in r.overrides] == ["locate"] and r.overrides[0].file == f"overrides/{REALTEK}/02_manifest.json"
    assert rec.reports[QUANTA].overrides == []


def test_invalid_override_is_rejected(project, make_runner):
    odir = project / "overrides" / REALTEK
    odir.mkdir(parents=True)
    (odir / "02_manifest.json").write_text('{"report_id": "x"}', encoding="utf-8")
    with pytest.raises(RunSetupError, match="未通過 schema 驗證"):
        make_runner(project).run(RunOptions())


def test_p2_item_override_recorded_and_inherited(project, make_runner, fake_llm):
    odir = project / "overrides" / REALTEK / "05_p2"
    odir.mkdir(parents=True)
    manual = p2_dict("2379-2024-M1-002", impact=False)
    (odir / "2379-2024-M1-002.json").write_text(json.dumps(manual, ensure_ascii=False), encoding="utf-8")
    rec = make_runner(project).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    ov = rec.reports[REALTEK].overrides
    assert len(ov) == 1 and ov[0].stage == "reason" and ov[0].items == ["2379-2024-M1-002"]
    assert not any("M1-002" in c.parts[0].text for c in fake_llm.calls if c.prompt_name == "p2_reasoning")

    # --from-stage check 會沿用 S5，並繼承人工取代紀錄
    new = make_runner(project).run(RunOptions(from_stage="check")).record
    inherited = new.reports[REALTEK].overrides
    assert inherited[0].items == ["2379-2024-M1-002"] and inherited[0].inherited_from_run == rec.run_id


def test_missing_config_prints_template_and_rejects_finance(project, make_runner):
    (project / "report" / "new_2024_ESG.pdf").write_bytes(b"%PDF")
    path = project / "config" / "reports.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["reports"].append({"file": "bank_2024_ESG.pdf", "company_full_name": "某銀行", "stock_code": "9999",
                            "report_year": 2024, "industry": {"section_code": "K", "section_name": "金融及保險業", "version": "v"}})
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    (project / "report" / "bank_2024_ESG.pdf").write_bytes(b"%PDF")
    runner = make_runner(project)
    rec = runner.run(RunOptions()).record
    out = runner.console.file.getvalue()
    assert "new_2024_ESG.pdf" in out and "company_full_name" in out
    assert "new_2024_ESG" not in rec.reports
    assert rec.reports["bank_2024_ESG"].status == "拒絕處理"
    assert not (project / "runs" / rec.run_id / "bank_2024_ESG").exists()


def test_no_strategy_table_stops_report(project, make_runner):
    rec = make_runner(project, registry=fake_registry(locate=fake_locate_no_table)).run(RunOptions()).record
    r = rec.reports[REALTEK]
    assert r.status == "未找到氣候策略表" and "overrides" in r.error
    assert "extract" not in r.stages


def test_one_report_failure_does_not_stop_others(project, make_runner):
    def boom(ctx):
        if ctx.report_id == QUANTA:
            raise ValueError("解析爆炸")
        from fakes import fake_parse
        fake_parse(ctx)

    rec = make_runner(project, registry=fake_registry(parse=boom)).run(RunOptions()).record
    assert rec.reports[QUANTA].status == "失敗" and "解析爆炸" in rec.reports[QUANTA].error
    assert rec.reports[REALTEK].status == "成功"
    assert (project / "runs" / rec.run_id / QUANTA / "error.log").exists()


def test_api_error_marks_item_failed_and_continues(project, make_runner):
    llm = FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": PermanentLLMError("400 bad", 400)})
    rec = make_runner(project, llm=llm).run(RunOptions(reports=["realtek_2024_ESG.pdf"])).record
    assert rec.reports[REALTEK].status == "成功"
    assert rec.reports[REALTEK].stages["reason"].items_failed == 2


def test_production_registry_stops_at_parse(project):
    import io

    from rich.console import Console

    from esgfin.pipeline.runner import Runner

    rec = Runner(project, console=Console(file=io.StringIO())).run(RunOptions()).record
    r = rec.reports[REALTEK]
    assert r.status == "失敗" and "M1" in r.error
    assert r.stages["explore"].status == "completed" and r.stages["parse"].status == "not_implemented"


def test_site_shows_latest_success_and_newer_failure_note(project, make_runner):
    ok = make_runner(project).run(RunOptions()).record

    def boom(ctx):
        raise ValueError("壞掉")

    bad = make_runner(project, registry=fake_registry(parse=boom)).run(RunOptions()).record
    html = (project / "site" / "index.html").read_text(encoding="utf-8")
    assert ok.run_id in html and bad.run_id in html and "未成功" in html
    assert "營業成本（上升）" in html
