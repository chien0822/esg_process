"""唯一入口（SPEC 3.1）。

  uv run python run.py                                    處理 report/ 內所有已設定 metadata 的 PDF，跑完開網頁
  uv run python run.py --report realtek_2024_ESG.pdf      只跑一份（可重複指定）
  uv run python run.py --resume [run_id]                  中斷後接續（預設最新的 run）
  uv run python run.py --from-stage reason [--source ID]  開新 run，X 之前的階段從來源 run 複製（預設最新的 run）
  uv run python run.py --no-serve                         只產生網頁檔，不開伺服器
  uv run python run.py --serve-only                       不跑流程，只用最新結果開網頁
  uv run python run.py --strict ...                       正式實驗：有未提交修改或 commit 改變就拒絕執行
  uv run python run.py --input-mode text ...              覆寫 extract.input_mode（S3a 的 text / text+pdf 比較）
  uv run python run.py --from-stage reason --items 代碼,代碼  試跑：S5 只跑指定條目，S6 之後不執行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from esgfin.config import load_settings  # noqa: E402
from esgfin.pipeline.errors import RunSetupError  # noqa: E402
from esgfin.pipeline.paths import ProjectPaths  # noqa: E402
from esgfin.pipeline.runner import Runner, RunOptions  # noqa: E402
from esgfin.pipeline.stages import STAGE_KEYS  # noqa: E402
from esgfin.site.serve import serve  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ESG 氣候風險→會計科目分析系統")
    p.add_argument("--report", action="append", metavar="PDF檔名", help="只處理指定的報告書，可重複指定")
    p.add_argument("--resume", nargs="?", const="latest", metavar="RUN_ID", help="接續中斷的 run（不填 = 最新的 run）")
    p.add_argument("--from-stage", choices=STAGE_KEYS, help="開新 run，從此階段開始；之前的階段從來源 run 複製")
    p.add_argument("--source", metavar="RUN_ID", help="--from-stage 的來源 run（預設最新的 run）")
    p.add_argument("--no-serve", action="store_true", help="只產生網頁檔，不開伺服器")
    p.add_argument("--serve-only", action="store_true", help="不跑流程，只用最新結果開網頁")
    p.add_argument("--strict", action="store_true", help="正式實驗：有未提交修改，或 commit 與 run 開始時不同，就拒絕執行")
    p.add_argument("--input-mode", choices=["text", "text+pdf"], help="覆寫 settings.yaml 的 extract.input_mode（S3a 表格重建）")
    p.add_argument("--items", metavar="代碼,代碼", help="試跑：S5 只跑指定的條目代碼（逗號分隔），S6 之後不執行")
    args = p.parse_args(argv)
    args.items = [c.strip() for c in args.items.split(",") if c.strip()] if args.items else None

    if args.serve_only and (args.report or args.resume or args.from_stage or args.source or args.no_serve or args.strict):
        p.error("--serve-only 不能與其他選項同時使用")
    if args.items is not None and not args.items:
        p.error("--items 至少要有一個條目代碼")
    if args.items and (args.resume or args.serve_only):
        p.error("--items 不能與 --resume／--serve-only 同時使用")
    if args.input_mode and (args.resume or args.serve_only):
        p.error("--input-mode 不能與 --resume／--serve-only 同時使用")
    if args.resume and (args.from_stage or args.source):
        p.error("--resume 不能與 --from-stage／--source 同時使用（接續會沿用原 run 的設定）")
    if args.resume and args.report:
        p.error("--resume 會沿用原 run 的報告書清單，不能再指定 --report")
    if args.source and not args.from_stage:
        p.error("--source 必須搭配 --from-stage")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = ProjectPaths(ROOT)
    settings = load_settings(paths.settings_file)

    if not args.serve_only:
        runner = Runner(ROOT)
        try:
            runner.run(
                RunOptions(
                    reports=args.report,
                    resume=args.resume,
                    from_stage=args.from_stage,
                    source=args.source,
                    strict=args.strict,
                    input_mode=args.input_mode,
                    items=args.items,
                )
            )
        except RunSetupError as e:
            print(f"錯誤：{e}", file=sys.stderr)
            return 2

    if args.no_serve:
        print(f"網頁檔：{paths.site_dir / 'index.html'}")
        return 0
    try:
        serve(paths.site_dir, settings.site.port)
    except (OSError, FileNotFoundError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
