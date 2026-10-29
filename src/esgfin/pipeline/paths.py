"""專案目錄配置（SPEC 2）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def report_dir(self) -> Path:
        return self.root / "report"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.yaml"

    @property
    def reports_file(self) -> Path:
        return self.config_dir / "reports.yaml"

    @property
    def prompts_dir(self) -> Path:
        return self.root / "prompts"

    @property
    def overrides_dir(self) -> Path:
        return self.root / "overrides"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def llm_cache_dir(self) -> Path:
        return self.root / "cache" / "llm"

    @property
    def parse_cache_dir(self) -> Path:
        return self.root / "cache" / "parse"

    @property
    def site_dir(self) -> Path:
        return self.root / "site"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def run_report_dir(self, run_id: str, report_id: str) -> Path:
        return self.run_dir(run_id) / report_id

    def override_dir(self, report_id: str) -> Path:
        return self.overrides_dir / report_id
