"""SPEC 第 17 節（2026-10-29 決議）的測試。"""

from __future__ import annotations

import subprocess

import pytest

from esgfin.parse.cache import ParseCache
from esgfin.pipeline.errors import RunSetupError
from esgfin.pipeline.runner import RunOptions
from esgfin.schemas.final import FinalItem
from esgfin.schemas.pages import PageRecord
from esgfin.site.build import accounts_lines

from fakes import EXTRACTION, fake_registry

REALTEK = "realtek_2024_ESG"


def git(root, *args):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(project):
    (project / ".gitignore").write_text("runs/\ncache/\nsite/\n", encoding="utf-8")
    git(project, "init", "-q")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "init")
    return project


def test_strict_requires_commit(project, make_runner):
    with pytest.raises(RunSetupError, match="--strict 需要"):
        make_runner(project).run(RunOptions(strict=True))


def test_strict_refuses_dirty_tree(repo, make_runner):
    (repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(RunSetupError, match="未提交"):
        make_runner(repo).run(RunOptions(strict=True))


def test_strict_run_records_commit_per_stage(repo, make_runner):
    rec = make_runner(repo).run(RunOptions(strict=True)).record
    assert rec.strict and rec.git.commit and rec.git.dirty is False
    stages = rec.reports[REALTEK].stages
    assert all(s.git_commit == rec.git.commit and s.git_dirty is False for s in stages.values())


def test_resume_after_commit_change_warns_or_refuses_with_strict(repo, make_runner):
    rec = make_runner(repo).run(RunOptions(strict=True)).record
    (repo / "note.txt").write_text("改程式", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "change")

    with pytest.raises(RunSetupError, match="與 run 開始時"):
        make_runner(repo).run(RunOptions(resume=rec.run_id, strict=True))

    runner = make_runner(repo)
    rec2 = runner.run(RunOptions(resume=rec.run_id)).record
    assert "警告：目前 git commit" in runner.console.file.getvalue()
    assert rec2.resumes[-1].git.commit != rec.git.commit


def test_from_stage_missing_source_output_message(project, make_runner):
    def boom(ctx):
        raise ValueError("解析失敗")

    src = make_runner(project, registry=fake_registry(parse=boom)).run(RunOptions()).record
    new = make_runner(project).run(RunOptions(from_stage="reason", source=src.run_id)).record
    err = new.reports[REALTEK].error
    assert new.reports[REALTEK].status == "失敗"
    assert "S1 解析" in err and "--from-stage parse" in err and f"--source {src.run_id}" in err
    assert "overrides/realtek_2024_ESG/" in err and "解析失敗" in err


def test_parse_cache_roundtrip_and_version_key(tmp_path):
    cache = ParseCache(tmp_path)
    pages = [PageRecord(pdf_page=1, text_md="a", parser="docling", parser_version="2.0")]
    assert cache.get("ab" * 32, "docling", "2.0") is None
    cache.put("ab" * 32, "docling", "2.0", pages)
    assert cache.get("ab" * 32, "docling", "2.0") == pages
    assert cache.get("ab" * 32, "docling", "2.1") is None
    assert cache.get("ab" * 32, "pymupdf4llm", "2.0") is None


def test_check_failed_item_shown_as_such():
    base = {"代碼": "c", "方法": "方法一", **EXTRACTION["items"][0]}
    item = FinalItem.model_validate({**base, "推理狀態": "檢查未通過", "整體判斷": "無影響"})
    assert accounts_lines(item) == ["檢查未通過"]
