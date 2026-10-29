"""讀取 config/ 下的設定檔與 .env。"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from esgfin.schemas.meta import ReportsConfig
from esgfin.schemas.settings import Settings


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_reports(path: Path) -> ReportsConfig:
    if not path.exists():
        return ReportsConfig(reports=[])
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ReportsConfig.model_validate({"reports": data.get("reports") or []})


def get_api_key(root: Path, name: str = "GEMINI_API_KEY") -> str:
    load_dotenv(root / ".env")
    key = os.environ.get(name, "").strip()
    if not key:
        raise RuntimeError(f"找不到 {name}：請在 .env 設定 {name}=<你的金鑰>")
    return key
