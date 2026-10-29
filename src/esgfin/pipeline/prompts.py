"""提示檔管理：prompts/<名稱>_v<版本>.md（CLAUDE.md 必守規則 5）。

改內容就升版本號、不覆寫舊檔。run.json 同時記錄版本號與檔案雜湊，
所以即使有人忘了升版，--resume 也能偵測到內容變了。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import RunSetupError
from .record import PromptInfo


def prompt_path(prompts_dir: Path, name: str, version: int) -> Path:
    return prompts_dir / f"{name}_v{version}.md"


def resolve_prompts(prompts_dir: Path, versions: dict[str, int], root: Path) -> dict[str, PromptInfo]:
    out: dict[str, PromptInfo] = {}
    for name, version in sorted(versions.items()):
        path = prompt_path(prompts_dir, name, version)
        if not path.exists():
            raise RunSetupError(f"settings.yaml 指定提示 {name} 第 {version} 版，但找不到 {path.relative_to(root)}")
        out[name] = PromptInfo(
            version=version,
            file=str(path.relative_to(root)),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return out


def load_prompt(root: Path, info: PromptInfo) -> str:
    path = root / info.file
    text = path.read_text(encoding="utf-8")
    if hashlib.sha256(path.read_bytes()).hexdigest() != info.sha256:
        raise RunSetupError(f"提示檔 {info.file} 在執行期間被修改；請升版本號後重新執行")
    return text
