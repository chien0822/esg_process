"""階段函式拿到的執行環境。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from rich.console import Console

from esgfin.llm.service import LLMService
from esgfin.schemas.meta import ReportConfig
from esgfin.schemas.settings import Settings

from .paths import ProjectPaths
from .record import PromptInfo


@dataclass
class ReportContext:
    paths: ProjectPaths
    settings: Settings
    run_id: str
    report: ReportConfig
    prompts: dict[str, PromptInfo]
    console: Console
    _llm_factory: Callable[[], LLMService]
    stage: str = ""
    _llm: LLMService | None = field(default=None, repr=False)

    @property
    def report_id(self) -> str:
        return self.report.report_id

    @property
    def report_dir(self) -> Path:
        return self.paths.run_report_dir(self.run_id, self.report_id)

    @property
    def pdf_path(self) -> Path:
        return self.paths.report_dir / self.report.file

    @property
    def llm(self) -> LLMService:
        """第一次用到才建立，因此不用 LLM 的階段不需要 API key。"""
        if self._llm is None:
            self._llm = self._llm_factory()
        return self._llm

    def llm_tag(self, **extra: str) -> dict[str, str]:
        return {"report_id": self.report_id, "stage": self.stage, **extra}
