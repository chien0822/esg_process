"""S5 的逐條目框架：跳過已完成、套用逐條人工取代、記錄失敗。

M4 只需要提供 reason_item（一個條目 → P2Result 或 P2Failure）。
- 成功：寫 05_p2/<代碼>.json，並刪除舊的 .failed.json
- 失敗：寫 05_p2/<代碼>.failed.json（不進成功快取；--resume 時會重新嘗試）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from pydantic import ValidationError

from esgfin.schemas.base import read_model, write_model
from esgfin.schemas.p1 import P1Item, P1Items
from esgfin.schemas.p2 import P2Failure, P2Result

from .context import ReportContext
from .errors import RunSetupError
from .stages import P2_DIR, p2_failure_path, p2_item_path, p2_item_valid

ReasonItemFn = Callable[[ReportContext, P1Item], "P2Result | P2Failure"]


@dataclass
class PerItemOutcome:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    overridden: list[str] = field(default_factory=list)
    override_hashes: dict[str, str] = field(default_factory=dict)


def run_per_item(
    ctx: ReportContext,
    reason_item: ReasonItemFn,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> PerItemOutcome:
    items = read_model(ctx.report_dir / "03_p1_items.json", P1Items).items
    out = PerItemOutcome(total=len(items))
    override_dir = ctx.paths.override_dir(ctx.report_id) / P2_DIR
    for item in items:
        target = p2_item_path(ctx.report_dir, item.code)
        failed_path = p2_failure_path(ctx.report_dir, item.code)
        override = override_dir / f"{item.code}.json"

        if override.exists():
            try:
                result = read_model(override, P2Result)
            except ValidationError as e:
                raise RunSetupError(f"人工檔 {override} 未通過 schema 驗證：{e}") from e
            write_model(target, result)
            failed_path.unlink(missing_ok=True)
            out.overridden.append(item.code)
            out.override_hashes[item.code] = hashlib.sha256(override.read_bytes()).hexdigest()
            out.succeeded += 1
        elif p2_item_valid(target):
            out.skipped += 1
        else:
            result = reason_item(ctx, item)
            if isinstance(result, P2Result):
                write_model(target, result)
                failed_path.unlink(missing_ok=True)
                out.succeeded += 1
            else:
                target.unlink(missing_ok=True)
                write_model(failed_path, result)
                out.failed += 1
        if on_progress:
            on_progress(item.code)
    return out
