"""所有 pydantic 模型的共用基底與 JSON 讀寫工具。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

M = TypeVar("M", bound=BaseModel)


class StrictModel(BaseModel):
    """英文 key 的模型（設定檔、manifest、run.json 等）。"""

    model_config = ConfigDict(extra="forbid")


class ZhModel(BaseModel):
    """英文屬性、中文 alias 的模型。讀寫 JSON 一律用中文 key。"""

    model_config = ConfigDict(
        extra="forbid",
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
    )


def zh(alias: str, default: Any = ..., **kwargs: Any) -> Any:
    """以中文 alias 宣告欄位。"""
    return Field(default, alias=alias, **kwargs)


def atomic_write_text(path: Path, text: str) -> None:
    """先寫暫存檔再改名，避免中斷時留下半個檔案。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_model(path: Path, model: BaseModel, *, exclude_none: bool = False) -> None:
    atomic_write_text(path, model.model_dump_json(indent=2, by_alias=True, exclude_none=exclude_none) + "\n")


def read_model(path: Path, cls: type[M]) -> M:
    return cls.model_validate_json(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, models: list[BaseModel]) -> None:
    lines = [m.model_dump_json(by_alias=True) for m in models]
    atomic_write_text(path, "".join(line + "\n" for line in lines))


def read_jsonl(path: Path, cls: type[M]) -> list[M]:
    out: list[M] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(cls.model_validate_json(line))
    return out


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
