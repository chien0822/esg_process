from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from esgfin.llm.fake import FakeLLM
from esgfin.pipeline.runner import Runner

from fakes import EXTRACTION, fake_registry, reason_responder

SETTINGS = {
    "llm": {"provider": "gemini", "model": "gemini-3.8-flash", "thinking_level": "medium", "validation_retries": 2},
    "rate_limit": {"requests_per_minute": None},
    "retry": {"max_attempts": 3, "initial_backoff_s": 0.01, "jitter": False},
    "prompts": {"p1_extract": 1, "p2_reasoning": 1},
}

REPORTS = [
    {"file": "realtek_2024_ESG.pdf", "company_full_name": "瑞昱半導體股份有限公司", "stock_code": "2379",
     "report_year": 2024, "industry": {"section_code": "C", "section_name": "製造業", "version": "待確認"}},
    {"file": "Quanta_2024_ESG.pdf", "company_full_name": "廣達電腦股份有限公司", "stock_code": "2382",
     "report_year": 2024, "industry": {"section_code": "C", "section_name": "製造業", "version": "待確認"}},
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text(yaml.safe_dump(SETTINGS, allow_unicode=True), encoding="utf-8")
    (tmp_path / "config" / "reports.yaml").write_text(yaml.safe_dump({"reports": REPORTS}, allow_unicode=True), encoding="utf-8")
    (tmp_path / "report").mkdir()
    for r in REPORTS:
        (tmp_path / "report" / r["file"]).write_bytes(b"%PDF-1.4 fake " + r["file"].encode())
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "p1_extract_v1.md").write_text("擷取提示 v1", encoding="utf-8")
    (tmp_path / "prompts" / "p2_reasoning_v1.md").write_text("推理提示 v1", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM({"p1_extract": EXTRACTION, "p2_reasoning": reason_responder})


@pytest.fixture
def make_runner(fake_llm: FakeLLM):
    def _make(root: Path, registry=None, llm: FakeLLM | None = None) -> Runner:
        provider = llm or fake_llm
        return Runner(
            root,
            registry=registry or fake_registry(),
            provider_factory=lambda settings: provider,
            console=Console(file=io.StringIO(), width=200),
            sleep=lambda s: None,
        )

    return _make
