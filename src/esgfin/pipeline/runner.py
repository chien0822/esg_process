"""階段框架（SPEC 3）。

三種模式：
- 新 run（預設）：開新資料夾，全部重跑（LLM 有快取）
- --resume [run_id]：中斷後接續，在同一個資料夾跑；設定、提示、metadata 有任何不同就拒絕
- --from-stage X [--source run_id]：開新 run，把 X 之前的階段輸出從來源 run 複製過來，舊 run 不動

每個階段的處理順序：人工檔（overrides/）→ 既有有效輸出（只在 --resume）→ 執行。
某階段實際執行或輸出被人工檔改變後，其後所有階段強制重跑。
"""

from __future__ import annotations

import hashlib
import shutil
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from pydantic import ValidationError
from rich.console import Console

from esgfin.config import get_api_key, load_reports, load_settings
from esgfin.constants import (
    REPORT_STATUS_FAILED,
    REPORT_STATUS_NO_TABLE,
    REPORT_STATUS_OK,
    REPORT_STATUS_REJECTED,
    REPORT_STATUS_RUNNING,
)
from esgfin.llm.base import LLMParams, LLMProvider
from esgfin.llm.cache import LLMCache
from esgfin.llm.ratelimit import RateLimiter
from esgfin.llm.service import LLMCallRecord, LLMService
from esgfin.schemas.base import atomic_write_text, read_jsonl, read_model, write_jsonl, write_model
from esgfin.schemas.manifest import Manifest
from esgfin.schemas.meta import ReportConfig
from esgfin.schemas.p1 import P1Items
from esgfin.schemas.settings import Settings

from .context import ReportContext
from .discover import discover, run_explore, yaml_template
from .errors import ReportStop, RunSetupError, StageNotImplemented
from .paths import ProjectPaths
from .per_item import ReasonItemFn, run_per_item
from .prompts import resolve_prompts
from .record import (
    GitInfo,
    OverrideRecord,
    ReportRunRecord,
    ResumeEvent,
    RunRecord,
    RunRecorder,
    StageRecord,
    components_from_record,
    describe_differences,
    fingerprint_components,
    fingerprint_of,
    git_info,
    now_iso,
)
from .stages import (
    JSONL_MODELS,
    OUTPUT_MODELS,
    P2_DIR,
    REPORT_STAGES,
    STAGE_BY_KEY,
    STAGE_KEYS,
    StageSpec,
    p2_failure_path,
    p2_item_path,
    p2_item_valid,
    stage_complete,
    stage_index,
    validate_output_file,
)

StageFn = Callable[[ReportContext], None]


def _not_implemented(code: str, label: str, milestone: str) -> StageFn:
    def fn(ctx: ReportContext) -> None:
        raise StageNotImplemented(f"{code} {label}尚未實作（{milestone}）")

    return fn


@dataclass
class StageRegistry:
    """各階段的實作。尚未完成的里程碑以 _not_implemented 佔位。"""

    fns: dict[str, StageFn]
    reason_item: ReasonItemFn | None
    build_site: Callable[[ProjectPaths, Settings], Path] | None

    @classmethod
    def production(cls) -> "StageRegistry":
        from esgfin.parse.stage import run_parse
        from esgfin.site.build import build_site

        return cls(
            fns={
                "explore": run_explore,
                "parse": run_parse,
                "locate": _not_implemented("S2", "定位", "M2"),
                "extract": _not_implemented("S3", "方法一擷取", "M3"),
                "p1check": _not_implemented("S4", "完整度檢查", "M5"),
                "check": _not_implemented("S6", "檢查模組", "M5"),
                "aggregate": _not_implemented("S7", "彙整", "M5"),
            },
            reason_item=None,  # M4
            build_site=build_site,
        )


@dataclass
class RunOptions:
    reports: list[str] | None = None  # PDF 檔名
    resume: str | None = None  # run_id 或 "latest"
    from_stage: str | None = None
    source: str | None = None  # 來源 run_id；None = 最新
    build_site: bool = True
    strict: bool = False  # 正式實驗：有未提交修改或 commit 與 run 開始時不同就拒絕執行


def list_runs(paths: ProjectPaths) -> list[str]:
    if not paths.runs_dir.exists():
        return []
    return sorted(p.name for p in paths.runs_dir.iterdir() if (p / "run.json").exists())


def latest_run(paths: ProjectPaths) -> str:
    runs = list_runs(paths)
    if not runs:
        raise RunSetupError("runs/ 內還沒有任何 run")
    return runs[-1]


def new_run_id(paths: ProjectPaths) -> str:
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, n = base, 2
    while paths.run_dir(run_id).exists():
        run_id, n = f"{base}-{n}", n + 1
    return run_id


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Runner:
    def __init__(
        self,
        root: Path,
        *,
        registry: StageRegistry | None = None,
        provider_factory: Callable[[Settings], LLMProvider] | None = None,
        console: Console | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.paths = ProjectPaths(root)
        self.registry = registry or StageRegistry.production()
        self.provider_factory = provider_factory or self._gemini_factory
        self.console = console or Console()
        self._sleep = sleep
        self._provider: LLMProvider | None = None
        self._strict = False
        self._warned_commits: set[str | None] = set()

    # ------------------------------------------------------------ git 檢查

    def _git_guard(self, recorder: RunRecorder) -> GitInfo:
        """每個階段開始前檢查程式版本。--strict 時不符就中止整個執行；否則只警告。"""
        g = git_info(self.paths.root)
        start = recorder.record.git
        if self._strict:
            if g.commit is None:
                raise RunSetupError("--strict 需要程式已在 git commit 中（目前沒有任何 commit 或不是 git repo）")
            if g.dirty:
                raise RunSetupError("--strict：有未提交的修改，請先 commit 再執行正式實驗")
            if g.commit != start.commit:
                raise RunSetupError(
                    f"--strict：目前 commit {g.commit[:10]} 與 run 開始時 {str(start.commit)[:10]} 不同；"
                    "請切回原 commit，或開新 run"
                )
        elif g.commit != start.commit and g.commit not in self._warned_commits:
            self._warned_commits.add(g.commit)
            self.console.print(
                f"[yellow]警告：目前 git commit（{str(g.commit)[:10]}）與 run 開始時（{str(start.commit)[:10]}）不同；"
                "各階段使用的 commit 會記錄在 run.json[/yellow]"
            )
        return g

    # ------------------------------------------------------------------ LLM

    def _gemini_factory(self, settings: Settings) -> LLMProvider:
        from esgfin.llm.gemini import GeminiProvider

        return GeminiProvider(get_api_key(self.paths.root), timeout_s=settings.llm.timeout_s)

    def _make_llm_factory(self, settings: Settings, recorder: RunRecorder, report_id: str, cache: LLMCache, limiter: RateLimiter) -> Callable[[], LLMService]:
        def sink(rec: LLMCallRecord) -> None:
            path = self.paths.run_report_dir(recorder.record.run_id, report_id) / "llm_calls.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(rec.model_dump_json() + "\n")
            stage = recorder.report(report_id).stages.get(rec.tag.get("stage", ""))
            if stage is not None:
                stage.add_call(rec)

        def factory() -> LLMService:
            if self._provider is None:
                self._provider = self.provider_factory(settings)
            return LLMService(
                self._provider,
                LLMParams(settings.llm.model, settings.llm.thinking_level, settings.llm.max_output_tokens),
                cache,
                limiter,
                settings.retry,
                validation_retries=settings.llm.validation_retries,
                sink=sink,
                sleep=self._sleep,
            )

        return factory

    # ------------------------------------------------------------- 主流程

    def run(self, opts: RunOptions) -> RunRecorder:
        self._strict = opts.strict
        settings = load_settings(self.paths.settings_file)
        reports_cfg = load_reports(self.paths.reports_file)
        prompts = resolve_prompts(self.paths.prompts_dir, settings.prompts, self.paths.root)

        if opts.resume:
            recorder, configs, source = self._prepare_resume(opts, settings, prompts)
        else:
            recorder, configs, source = self._prepare_new(opts, settings, reports_cfg, prompts)

        cache = LLMCache(self.paths.llm_cache_dir)
        limiter = RateLimiter(settings.rate_limit.requests_per_minute, settings.rate_limit.tokens_per_minute, sleep=self._sleep)

        self.console.print(f"[bold]run_id：{recorder.record.run_id}[/bold]（{self._mode_label(recorder.record, bool(opts.resume))}）")
        self._git_guard(recorder)
        for cfg in configs:
            ctx = ReportContext(
                paths=self.paths,
                settings=settings,
                run_id=recorder.record.run_id,
                report=cfg,
                prompts=recorder.record.prompts,
                console=self.console,
                _llm_factory=self._make_llm_factory(settings, recorder, cfg.report_id, cache, limiter),
            )
            self._process_report(ctx, recorder, source)

        if opts.build_site and self.registry.build_site is not None:
            self._build_site(recorder, settings)

        recorder.record.ended_at = now_iso()
        recorder.estimate_cost(settings)
        recorder.save()
        self._print_summary(recorder)
        return recorder

    @staticmethod
    def _mode_label(rec: RunRecord, resumed: bool) -> str:
        if resumed:
            return "接續中斷的 run"
        if rec.mode == "from_stage":
            return f"從 {rec.from_stage} 開始，來源 run {rec.source_run_id}"
        return "新 run，全部執行"

    def _prepare_new(self, opts, settings, reports_cfg, prompts):
        source: RunRecorder | None = None
        selected = opts.reports
        if opts.from_stage:
            if opts.from_stage not in STAGE_KEYS:
                raise RunSetupError(f"--from-stage 只能是：{', '.join(STAGE_KEYS)}")
            source_id = opts.source or latest_run(self.paths)
            source_path = self.paths.run_dir(source_id) / "run.json"
            if not source_path.exists():
                raise RunSetupError(f"找不到來源 run：{source_id}")
            source = RunRecorder.load(source_path)
            source_files = [r.file for r in source.record.reports.values() if r.status != REPORT_STATUS_REJECTED]
            if selected:
                missing = [f for f in selected if f not in source_files]
                if missing:
                    raise RunSetupError(f"來源 run {source_id} 沒有處理過：{', '.join(missing)}")
            selected = selected or source_files
            if not selected:
                raise RunSetupError(f"來源 run {source_id} 沒有可用的報告書")

        d = discover(self.paths, reports_cfg, selected)
        if d.missing_config:
            self.console.print(
                "[yellow]以下 PDF 在 config/reports.yaml 沒有設定，已跳過。可貼上以下範本補設定：[/yellow]\n"
                + yaml_template(d.missing_config)
            )
        for cfg in d.missing_pdf:
            self.console.print(f"[yellow]reports.yaml 設定了 {cfg.file}，但 report/ 內沒有這個檔案，已跳過[/yellow]")
        if not d.runnable and not d.rejected:
            raise RunSetupError("沒有可處理的報告書：請把 PDF 放進 report/，並在 config/reports.yaml 填寫 metadata")

        all_cfgs = d.runnable + [c for c, _ in d.rejected]
        reports_config = {c.report_id: c.model_dump(mode="json") for c in all_cfgs}
        components = fingerprint_components(settings.llm.provider, settings, prompts, reports_config)
        run_id = new_run_id(self.paths)
        record = RunRecord(
            run_id=run_id,
            mode="from_stage" if opts.from_stage else "new",
            started_at=now_iso(),
            source_run_id=source.record.run_id if source else None,
            from_stage=opts.from_stage,
            git=git_info(self.paths.root),
            strict=opts.strict,
            provider=settings.llm.provider,
            model=settings.llm.model,
            prompts=prompts,
            settings=settings.model_dump(mode="json"),
            reports_config=reports_config,
            fingerprint=fingerprint_of(components),
            selected_reports=[c.report_id for c in all_cfgs],
        )
        for cfg in d.runnable:
            record.reports[cfg.report_id] = ReportRunRecord(report_id=cfg.report_id, file=cfg.file)
        for cfg, reason in d.rejected:
            record.reports[cfg.report_id] = ReportRunRecord(
                report_id=cfg.report_id, file=cfg.file, status=REPORT_STATUS_REJECTED, error=reason
            )
            self.console.print(f"[red]{cfg.file}：拒絕處理。{reason}[/red]")
        recorder = RunRecorder(record, self.paths.run_dir(run_id) / "run.json")
        recorder.save()
        return recorder, d.runnable, source

    def _prepare_resume(self, opts, settings, prompts):
        run_id = latest_run(self.paths) if opts.resume == "latest" else opts.resume
        path = self.paths.run_dir(run_id) / "run.json"
        if not path.exists():
            raise RunSetupError(f"找不到要接續的 run：{run_id}")
        recorder = RunRecorder.load(path)
        rec = recorder.record

        # 比對：目前的設定、提示、metadata 必須與原 run 完全相同
        current_cfg = load_reports(self.paths.reports_file)
        by_id = {c.report_id: c for c in current_cfg.reports}
        current_reports_config = {
            rid: by_id[rid].model_dump(mode="json") if rid in by_id else None for rid in rec.reports_config
        }
        old = components_from_record(rec)
        new = fingerprint_components(settings.llm.provider, settings, prompts, current_reports_config)
        diffs = describe_differences(old, new)
        if diffs:
            raise RunSetupError(
                f"無法接續 run {run_id}：以下設定與原 run 不同。\n- " + "\n- ".join(diffs)
                + "\n若要用新設定重跑，請開新 run，或用 --from-stage 從某階段開始。"
            )
        rec.resumes.append(ResumeEvent(at=now_iso(), git=git_info(self.paths.root), strict=opts.strict))
        rec.ended_at = None
        configs = [
            ReportConfig.model_validate(rec.reports_config[rid])
            for rid in rec.selected_reports
            if rec.reports[rid].status != REPORT_STATUS_REJECTED
        ]
        source = RunRecorder.load(self.paths.run_dir(rec.source_run_id) / "run.json") if rec.source_run_id else None
        recorder.save()
        return recorder, configs, source

    # --------------------------------------------------------- 單份報告書

    def _process_report(self, ctx: ReportContext, recorder: RunRecorder, source: RunRecorder | None) -> None:
        rec = recorder.report(ctx.report_id)
        rec.status = REPORT_STATUS_RUNNING
        rec.error = None
        recorder.save()
        self.console.print(f"\n[bold]{ctx.report.file}[/bold]（{ctx.report.company_full_name} {ctx.report.report_year}）")

        run = recorder.record
        copy_until = stage_index(run.from_stage) if run.mode == "from_stage" and run.from_stage else 0
        force = False
        try:
            for i, spec in enumerate(REPORT_STAGES):
                ctx.stage = spec.key
                if i < copy_until:
                    if spec.key not in rec.copied_stages:
                        assert source is not None
                        self._copy_stage(ctx, spec, source, rec)
                        rec.copied_stages.append(spec.key)
                        rec.stages[spec.key] = StageRecord(status="copied")
                        recorder.save()
                    self.console.print(f"  {spec.code} {spec.label}：沿用來源 run {source.record.run_id if source else ''}")
                else:
                    force = self._run_stage(ctx, spec, recorder, force)
                if spec.key == "locate":
                    self._require_strategy_table(ctx)
            rec.status = REPORT_STATUS_OK
        except StageNotImplemented as e:
            rec.status = REPORT_STATUS_FAILED
            rec.error = str(e)
            self.console.print(f"  [yellow]{e}；此報告書停在這裡[/yellow]")
        except ReportStop as e:
            rec.status = e.status
            rec.error = e.reason
            self.console.print(f"  [yellow]{e.reason}[/yellow]")
        except RunSetupError:
            raise
        except Exception as e:  # 單份失敗不影響其他報告書
            rec.status = REPORT_STATUS_FAILED
            rec.error = f"{type(e).__name__}: {e}"
            atomic_write_text(ctx.report_dir / "error.log", traceback.format_exc())
            self.console.print(f"  [red]失敗：{rec.error}（詳見 {ctx.report_dir / 'error.log'}）[/red]")
        finally:
            recorder.save()

    def _run_stage(self, ctx: ReportContext, spec: StageSpec, recorder: RunRecorder, force: bool) -> bool:
        """執行一個階段，回傳之後的階段是否必須強制重跑。"""
        git = self._git_guard(recorder)
        rec = recorder.report(ctx.report_id)
        old = rec.stages.get(spec.key)
        srec = (
            old.model_copy(update={"error": None})
            if old is not None and old.status not in ("copied", "skipped_existing")
            else StageRecord(status="completed")
        )
        srec.started_at = now_iso()
        rec.stages[spec.key] = srec
        t0 = time.monotonic()
        try:
            if spec.per_item:
                return self._run_reason(ctx, spec, recorder, srec, force)

            changed = self._apply_override(ctx, spec, rec)
            if changed is not None:
                srec.status = "override"
                self.console.print(f"  {spec.code} {spec.label}：使用人工檔 overrides/{ctx.report_id}/")
                return force or changed
            if not force and stage_complete(ctx.report_dir, spec):
                srec.status = "skipped_existing"
                self.console.print(f"  {spec.code} {spec.label}：已有有效輸出，跳過")
                return False

            self.console.print(f"  {spec.code} {spec.label}：執行中…")
            ctx.report_dir.mkdir(parents=True, exist_ok=True)
            srec.status = "completed"
            ctx.stage_record = srec
            self.registry.fns[spec.key](ctx)
            if not stage_complete(ctx.report_dir, spec):
                raise RuntimeError(f"{spec.code} 執行完畢但輸出檔不完整或未通過 schema 驗證")
            return True
        except StageNotImplemented as e:
            srec.status = "not_implemented"
            srec.error = str(e)
            raise
        except ReportStop:
            raise
        except Exception as e:
            srec.status = "failed"
            srec.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            srec.ended_at = now_iso()
            srec.duration_s = round(srec.duration_s + time.monotonic() - t0, 3)
            srec.git_commit, srec.git_dirty = git.commit, git.dirty
            recorder.save()

    def _run_reason(self, ctx, spec, recorder, srec, force) -> bool:
        rec = recorder.report(ctx.report_id)
        if not force and stage_complete(ctx.report_dir, spec) and not self._has_p2_overrides(ctx):
            srec.status = "skipped_existing"
            self.console.print(f"  {spec.code} {spec.label}：所有條目都已完成，跳過")
            return False
        if self.registry.reason_item is None:
            raise StageNotImplemented(f"{spec.code} {spec.label}尚未實作（M4）")
        if force:
            # 上游有變：舊的條目結果不可沿用
            shutil.rmtree(ctx.report_dir / P2_DIR, ignore_errors=True)
        srec.status = "completed"
        items_total = len(read_model(ctx.report_dir / "03_p1_items.json", P1Items).items)
        self.console.print(f"  {spec.code} {spec.label}：{items_total} 個條目")

        done = 0

        def progress(code: str) -> None:
            nonlocal done
            done += 1
            self.console.print(f"    [{done}/{items_total}] {code}")
            recorder.save()

        outcome = run_per_item(ctx, self.registry.reason_item, on_progress=progress)
        srec.items_total = outcome.total
        srec.items_succeeded = outcome.succeeded
        srec.items_failed = outcome.failed
        srec.items_skipped = outcome.skipped
        rec.overrides = [o for o in rec.overrides if o.stage != spec.key or o.inherited_from_run]
        if outcome.overridden:
            rec.overrides.append(
                OverrideRecord(
                    stage=spec.key,
                    file=str((self.paths.override_dir(ctx.report_id) / P2_DIR).relative_to(self.paths.root)),
                    sha256=hashlib.sha256("".join(outcome.override_hashes[c] for c in outcome.overridden).encode()).hexdigest(),
                    items=outcome.overridden,
                )
            )
        if outcome.failed:
            self.console.print(f"    [yellow]推理失敗 {outcome.failed} 條（--resume 會重新嘗試）[/yellow]")
        # 有任何條目實際執行或被取代，下游就要重跑
        return outcome.skipped < outcome.total or force

    def _has_p2_overrides(self, ctx: ReportContext) -> bool:
        d = self.paths.override_dir(ctx.report_id) / P2_DIR
        return d.exists() and any(d.glob("*.json"))

    def _apply_override(self, ctx: ReportContext, spec: StageSpec, rec: ReportRunRecord) -> bool | None:
        """有人工檔就驗證後寫入輸出位置。回傳 None = 沒有人工檔；否則回傳輸出是否改變。"""
        odir = self.paths.override_dir(ctx.report_id)
        present = [n for n in spec.outputs if (odir / n).exists()]
        if not present:
            return None
        if len(present) != len(spec.outputs):
            missing = sorted(set(spec.outputs) - set(present))
            raise RunSetupError(f"overrides/{ctx.report_id}/ 只有部分 {spec.code} 輸出檔，缺：{', '.join(missing)}")
        changed = False
        rec.overrides = [o for o in rec.overrides if o.stage != spec.key]
        for name in spec.outputs:
            src = odir / name
            dst = ctx.report_dir / name
            try:
                if name in OUTPUT_MODELS:
                    model = read_model(src, OUTPUT_MODELS[name])
                    before = dst.read_text(encoding="utf-8") if dst.exists() else None
                    write_model(dst, model)
                else:
                    rows = read_jsonl(src, JSONL_MODELS[name])
                    before = dst.read_text(encoding="utf-8") if dst.exists() else None
                    write_jsonl(dst, rows)
            except (ValidationError, ValueError) as e:
                raise RunSetupError(f"人工檔 {src.relative_to(self.paths.root)} 未通過 schema 驗證：{e}") from e
            changed = changed or before != dst.read_text(encoding="utf-8")
            rec.overrides.append(
                OverrideRecord(stage=spec.key, file=str(src.relative_to(self.paths.root)), sha256=_sha256(src))
            )
        return changed

    def _copy_stage(self, ctx: ReportContext, spec: StageSpec, source: RunRecorder, rec: ReportRunRecord) -> None:
        src_dir = self.paths.run_report_dir(source.record.run_id, ctx.report_id)
        sid = source.record.run_id
        if spec.per_item:
            items = read_model(src_dir / "03_p1_items.json", P1Items).items
            missing = [
                it.code for it in items
                if not (p2_item_valid(p2_item_path(src_dir, it.code)) or p2_failure_path(src_dir, it.code).exists())
            ]
            if missing:
                raise ReportStop(REPORT_STATUS_FAILED, self._copy_failure_message(ctx, spec, sid, f"缺少條目 {', '.join(missing)} 的結果"))
            shutil.copytree(src_dir / P2_DIR, ctx.report_dir / P2_DIR, dirs_exist_ok=True)
        else:
            problems = [
                f"{name}（{why}）"
                for name in spec.outputs
                if (why := validate_output_file(src_dir / name, name)) is not None
            ]
            if problems:
                raise ReportStop(REPORT_STATUS_FAILED, self._copy_failure_message(ctx, spec, sid, "；".join(problems)))
            ctx.report_dir.mkdir(parents=True, exist_ok=True)
            for name in spec.outputs:
                shutil.copy2(src_dir / name, ctx.report_dir / name)
        src_rec = source.record.reports.get(ctx.report_id)
        if src_rec:
            for o in src_rec.overrides:
                if o.stage == spec.key:
                    rec.overrides.append(o.model_copy(update={"inherited_from_run": o.inherited_from_run or sid}))

    def _copy_failure_message(self, ctx: ReportContext, spec: StageSpec, source_id: str, detail: str) -> str:
        run = self.paths.run_dir(ctx.run_id)
        src_rec = RunRecorder.load(self.paths.run_dir(source_id) / "run.json").record.reports.get(ctx.report_id)
        src_status = f"（來源 run 中該報告書狀態：{src_rec.status}{'，' + src_rec.error if src_rec and src_rec.error else ''}）" if src_rec else ""
        from_stage = STAGE_BY_KEY[self._from_stage_key(run)]
        return (
            f"無法從 {from_stage.code} {from_stage.label} 開始：來源 run {source_id} 的 "
            f"{spec.code} {spec.label}（--from-stage {spec.key}）沒有有效輸出：{detail}{src_status}。"
            f"補救方式：(1) 改用 --from-stage {spec.key} --source {source_id}，從缺少的階段開始重跑；"
            f"(2) 用 --source 指定另一個有完整輸出的 run；"
            f"(3) 把人工整理的檔案放進 overrides/{ctx.report_id}/ 後，用 --from-stage {spec.key} 重跑"
        )

    @staticmethod
    def _from_stage_key(run_dir: Path) -> str:
        return RunRecorder.load(run_dir / "run.json").record.from_stage or "explore"

    def _require_strategy_table(self, ctx: ReportContext) -> None:
        manifest = read_model(ctx.report_dir / "02_manifest.json", Manifest)
        if not manifest.strategy_tables:
            raise ReportStop(
                REPORT_STATUS_NO_TABLE,
                f"未找到氣候策略表。可在 overrides/{ctx.report_id}/02_manifest.json 手動指定策略表頁碼",
            )

    # ---------------------------------------------------------------- S8

    def _build_site(self, recorder: RunRecorder, settings: Settings) -> None:
        srec = StageRecord(status="completed", started_at=now_iso())
        recorder.record.site_stage = srec
        t0 = time.monotonic()
        try:
            assert self.registry.build_site is not None
            out = self.registry.build_site(self.paths, settings)
            self.console.print(f"\nS8 網頁：已產生 {out.relative_to(self.paths.root)}")
        except Exception as e:
            srec.status = "failed"
            srec.error = f"{type(e).__name__}: {e}"
            self.console.print(f"\n[red]S8 網頁產生失敗：{srec.error}[/red]")
        finally:
            srec.ended_at = now_iso()
            srec.duration_s = round(time.monotonic() - t0, 3)

    def _print_summary(self, recorder: RunRecorder) -> None:
        self.console.print("\n[bold]結果摘要[/bold]")
        for r in recorder.record.reports.values():
            extra = f"：{r.error}" if r.error else ""
            self.console.print(f"  {r.file}：{r.status}{extra}")
        self.console.print(f"  紀錄：{recorder.path.relative_to(self.paths.root)}")
