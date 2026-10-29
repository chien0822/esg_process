"""唯一入口（SPEC 3.1）。

  uv run python run.py                                    處理 report/ 內所有已設定 metadata 的 PDF，跑完開網頁
  uv run python run.py --report realtek_2024_ESG.pdf      只跑一份（可重複指定）
  uv run python run.py --resume [run_id]                  中斷後接續（預設最新的 run）
  uv run python run.py --from-stage reason [--source ID]  開新 run，X 之前的階段從來源 run 複製（預設最新的 run）
  uv run python run.py --no-serve                         只產生網頁檔，不開伺服器
  uv run python run.py --serve-only                       不跑流程，只用最新結果開網頁
  uv run python run.py --strict ...                       正式實驗：有未提交修改或 commit 改變就拒絕執行
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
    args = p.parse_args(argv)

    if args.serve_only and (args.report or args.resume or args.from_stage or args.source or args.no_serve or args.strict):
        p.error("--serve-only 不能與其他選項同時使用")
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
