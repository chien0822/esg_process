"""S2 定位階段（SPEC 6、17.10）：章節地圖與策略表頁 → 02_manifest.json。

LLM 只用在目錄解析、角色分類、策略表判斷與書末索引；其餘為程式。
另寫 02_locate_debug.json（不是階段輸出，不做 schema 驗證），保存中間結果供人工核對。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from esgfin.constants import (
    INDEX_PAGE_KEYWORDS,
    STRATEGY_TABLE_KEYWORDS,
)
from esgfin.llm.base import DocumentPart, LLMRequest, TextPart
from esgfin.llm.service import llm_schema
from esgfin.pipeline.context import ReportContext
from esgfin.pipeline.prompts import load_prompt
from esgfin.schemas.base import read_jsonl, write_json, write_model
from esgfin.schemas.locate import IndexResult, RoleResult, StrategyResult, TocParse
from esgfin.schemas.manifest import IndexCrossCheck, Manifest, PageOffset, Section, StrategyTable
from esgfin.schemas.pages import PageRecord
from esgfin.textnorm import normalize

from . import toc as T
from .layout import Layout, analyze, page_offset, strip_nav

ROLE_ENV = "環境"
ROLE_INDEX = "索引附錄"
NAV_MIN_COVERAGE = 0.3  # 以顏色標示目前章節的頁數比例低於此值時，不使用導覽列範圍


def _ask(ctx: ReportContext, prompt: str, parts: list, model, step: str):
    info = ctx.prompts[prompt]
    req = LLMRequest(prompt, info.version, load_prompt(ctx.paths.root, info), tuple(parts), llm_schema(model))
    res = ctx.llm.structured(req, model, tag=ctx.llm_tag(step=step))
    if res.value is None:
        raise RuntimeError(f"S2 {step}：LLM 未能產生有效輸出（{res.error}）")
    return res.value


def _pdf_bytes(pdf: Path, pages: list[int]) -> bytes:
    import pymupdf

    with pymupdf.open(pdf) as src, pymupdf.open() as out:
        for n in pages:
            out.insert_pdf(src, from_page=n - 1, to_page=n - 1)
        return out.tobytes()


def _page_block(n: int, text: str, limit: int) -> str:
    body = text if len(text) <= limit else text[:limit] + "\n…（以下截斷）"
    return f"===== PDF 第 {n} 頁 =====\n{body}"


# ---------------------------------------------------------------- 第 1、2 步


def _toc_entries(ctx: ReportContext, layout: Layout, debug: dict) -> tuple[list[T.TocEntry], str | None]:
    import pymupdf

    s = ctx.settings.locate
    with pymupdf.open(ctx.pdf_path) as doc:
        bookmarks = doc.get_toc()
    usable, note = T.bookmarks_usable(bookmarks, s.bookmark_min_entries)
    debug["bookmarks"] = {"count": len(bookmarks), "usable": usable, "note": note}
    if usable:
        entries = [T.TocEntry(title=str(t), level=lvl, evidence=str(t), pdf_page=p) for lvl, t, p, *_ in bookmarks]
        return entries, "bookmarks"

    toc_pages = T.find_toc_pages(layout, s.toc_scan_pages, s.toc_min_numbers)
    debug["toc_pages"] = toc_pages
    if not toc_pages:
        return [], None
    text = "\n\n".join(f"===== PDF 第 {n} 頁（目錄頁）=====\n{layout.pages[n].text}" for n in toc_pages)
    parts = [
        TextPart(text),
        DocumentPart(_pdf_bytes(ctx.pdf_path, toc_pages), label=f"{ctx.report_id} toc p{toc_pages}"),
    ]
    parsed = _ask(ctx, "s2_toc", parts, TocParse, "toc")
    entries = [
        T.TocEntry(title=e.title, level=e.level, evidence=e.evidence, printed_page=e.printed_page, uncertain=list(e.uncertain))
        for e in parsed.entries
    ]
    return entries, "toc_page"


# ---------------------------------------------------------------- 第 5 步


def _classify_roles(ctx: ReportContext, sections: list[Section]) -> None:
    if not sections:
        return
    parent: dict[str, str] = {}
    stack: list[Section] = []
    for sec in sections:
        while stack and stack[-1].level >= sec.level:
            stack.pop()
        if stack:
            parent[sec.section_id] = stack[-1].title
        stack.append(sec)
    lines = [f"{s.section_id}｜層級 {s.level}｜上層：{parent.get(s.section_id, '—')}｜{s.title}" for s in sections]
    result = _ask(ctx, "s2_roles", [TextPart("\n".join(lines))], RoleResult, "roles")
    roles = {r.section_id: r.role for r in result.roles}
    for sec in sections:
        sec.role = roles.get(sec.section_id)


# ---------------------------------------------------------------- 第 6 步


def _body_text(pages: dict[int, PageRecord], layout: Layout) -> dict[int, str]:
    return {n: strip_nav(p.text_md, layout.nav_tokens)[0] for n, p in pages.items()}


def _keyword_hits(text: str) -> list[str]:
    return [k for k in STRATEGY_TABLE_KEYWORDS if k in text]


def _judge_strategy(ctx: ReportContext, candidates: list[int], body: dict[int, str]) -> dict[int, tuple[bool, str | None]]:
    if not candidates:
        return {}
    limit = ctx.settings.locate.max_chars_per_candidate_page
    text = "\n\n".join(_page_block(n, body[n], limit) for n in candidates)
    result = _ask(ctx, "s2_strategy", [TextPart(text)], StrategyResult, "strategy")
    return {j.pdf_page: (j.is_strategy_table, j.evidence) for j in result.pages if j.pdf_page in candidates}


def _group_tables(judged: dict[int, tuple[bool, str | None]], found_by: str, sections: list[Section]) -> list[StrategyTable]:
    pages = sorted(n for n, (ok, _) in judged.items() if ok)
    groups: list[list[int]] = []
    for n in pages:
        if groups and n == groups[-1][-1] + 1:
            groups[-1].append(n)
        else:
            groups.append([n])
    tables = []
    for g in groups:
        evidence = " ／ ".join(f"p.{n}：{judged[n][1]}" for n in g if judged[n][1])
        tables.append(StrategyTable(pdf_pages=g, section_id=_section_of(g[0], sections), evidence=evidence, found_by=found_by))
    return tables


def _section_of(page: int, sections: list[Section]) -> str | None:
    """包含該頁的最深層章節（優先環境章節）。"""
    hits = [s for s in sections if s.pdf_page_range and s.pdf_page_range[0] <= page <= s.pdf_page_range[1]]
    if not hits:
        return None
    hits.sort(key=lambda s: (s.role != ROLE_ENV, -s.level))
    return hits[0].section_id


# ---------------------------------------------------------------- 第 7 步


def _index_pages(sections: list[Section], layout: Layout, max_pages: int) -> list[int]:
    pool: set[int] = set()
    for s in sections:
        if s.role == ROLE_INDEX and s.pdf_page_range_buffered:
            pool.update(range(s.pdf_page_range_buffered[0], s.pdf_page_range_buffered[1] + 1))
    if not pool:  # 沒有索引附錄章節：看最後 15% 的頁
        pool = set(range(max(1, int(layout.page_count * 0.85)), layout.page_count + 1))
    scored = []
    for n in sorted(pool):
        text = layout.pages[n].text
        if not any(k in text for k in INDEX_PAGE_KEYWORDS):
            continue
        score = sum(text.count(k) for k in ("201-2", "氣候", "TCFD", "IFRS S2", "風險與機會"))
        scored.append((score, n))
    top = sorted(scored, key=lambda x: (-x[0], x[1]))[:max_pages]
    return sorted(n for score, n in top if score > 0)


def _cross_check(ctx, index_pages, body, offset, sections, located: set[int]) -> tuple[list[IndexCrossCheck], list[int]]:
    if not index_pages:
        return [], []
    limit = ctx.settings.locate.max_chars_per_candidate_page
    text = "\n\n".join(_page_block(n, body[n], limit) for n in index_pages)
    result = _ask(ctx, "s2_index", [TextPart(text)], IndexResult, "index")
    checks, pointed = [], []
    for item in result.items:
        pdf_pages = [p + offset for p in item.printed_pages] if offset is not None else []
        pdf_pages = [p for p in pdf_pages if 1 <= p <= max(body)]
        pointed.extend(pdf_pages)
        if pdf_pages:
            inside = all(p in located for p in pdf_pages)
        else:
            target = normalize(item.points_to)
            secs = [s for s in sections if normalize(s.title) and normalize(s.title) in target]
            inside = None if not secs else any(s.role == ROLE_ENV for s in secs)
        checks.append(IndexCrossCheck(index_item=item.index_item, points_to=item.points_to, inside_located_range=inside))
    return checks, sorted(set(pointed))


# ---------------------------------------------------------------- 主流程


def run_locate(ctx: ReportContext) -> None:
    s = ctx.settings.locate
    layout = analyze(ctx.pdf_path, s.margin_ratio, s.nav_min_pages)
    pages = {p.pdf_page: p for p in read_jsonl(ctx.report_dir / "01_pages.jsonl", PageRecord)}
    debug: dict = {}
    warnings: list[str] = []

    # 第 1–3 步
    entries, toc_source = _toc_entries(ctx, layout, debug)
    if not entries:
        warnings.append("找不到可用的書籤或目錄頁，無法建立章節地圖")
    offset, offset_note = page_offset(layout, s.offset_min_agreement)
    if offset is None:
        warnings.append(f"無法判定印刷頁碼位移：{offset_note}")
    page_attr = "pdf_page" if toc_source == "bookmarks" else "printed_page"
    T.check_rules(entries, page_attr, layout.page_count)

    # 第 4 步
    T.to_pdf_pages(entries, offset, layout.page_count)
    nav = T.nav_ranges(layout, NAV_MIN_COVERAGE)
    T.apply_nav(entries, nav, s.section_page_buffer)
    T.compute_ends(entries, layout.page_count)
    T.check_nav_ranges(entries, nav, s.section_page_buffer)
    # 第 8 步（章節標題）
    T.check_titles(entries, layout, s.section_page_buffer)

    sections = []
    for i, e in enumerate(entries, start=1):
        pdf_range = (e.pdf_page, e.pdf_end) if e.pdf_page is not None and e.pdf_end is not None else None
        printed = (pdf_range[0] - offset, pdf_range[1] - offset) if pdf_range and offset is not None else None
        sections.append(
            Section(
                section_id=f"S{i:02d}",
                title=e.title,
                level=e.level,
                printed_page_range=printed,
                pdf_page_range=pdf_range,
                pdf_page_range_buffered=T.buffered(*pdf_range, s.section_page_buffer, layout.page_count) if pdf_range else None,
                role=None,
                evidence=e.evidence,
                flags=list(dict.fromkeys(e.flags)),
            )
        )
    for sec in sections:
        if sec.flags:
            warnings.append(f"{sec.section_id}「{sec.title}」：{'、'.join(sec.flags)}")

    # 第 5 步
    _classify_roles(ctx, sections)
    env_pages: set[int] = set()
    for sec in sections:
        if sec.role == ROLE_ENV and sec.pdf_page_range_buffered:
            env_pages.update(range(sec.pdf_page_range_buffered[0], sec.pdf_page_range_buffered[1] + 1))
    if not env_pages:
        warnings.append("沒有任何章節被分類為環境，無法在環境章節內找策略表")

    # 第 6 步
    body = _body_text(pages, layout)
    scores = {n: _keyword_hits(body[n]) for n in sorted(env_pages) if n in body}
    ranked = sorted((n for n, h in scores.items() if len(h) >= s.min_keyword_hits), key=lambda n: (-len(scores[n]), n))
    candidates = sorted(ranked[: s.max_strategy_candidates])
    if len(ranked) > len(candidates):
        warnings.append(f"策略表候選頁 {len(ranked)} 頁，超過上限 {s.max_strategy_candidates}，只送關鍵字最多的頁")
    judged = _judge_strategy(ctx, candidates, body)
    tables = _group_tables(judged, "keyword+llm", sections)

    # 第 7 步
    located = env_pages | {n for t in tables for n in t.pdf_pages}
    index_pages = _index_pages(sections, layout, s.max_index_pages)
    checks, pointed = _cross_check(ctx, index_pages, body, offset, sections, located)
    for c in checks:
        if c.inside_located_range is False:
            warnings.append(f"索引項目「{c.index_item}」指向 {c.points_to}，不在定位結果（環境章節與策略表頁）內")
    fallback: list[int] = []
    if not tables and pointed:
        fallback = sorted({p + d for p in pointed for d in range(-s.section_page_buffer, s.section_page_buffer + 1) if 1 <= p + d <= layout.page_count})
        fallback = fallback[: s.max_strategy_candidates]
        judged_fb = _judge_strategy(ctx, fallback, body)
        tables = _group_tables(judged_fb, "index_fallback", sections)
        judged.update(judged_fb)

    # 第 8 步（策略表頁）與引文核對
    for t in tables:
        for n in t.pdf_pages:
            if not _keyword_hits(body.get(n, "")):
                warnings.append(f"策略表 p.{n} 不含任何關鍵字")
            ev = judged.get(n, (True, None))[1]
            if ev and normalize(ev) not in normalize(pages[n].text_md):
                warnings.append(f"策略表 p.{n} 的原文摘錄未逐字出現在該頁：{ev[:40]}")

    manifest = Manifest(
        report_id=ctx.report_id,
        toc_source=toc_source,
        page_offset=PageOffset(value=offset, method="header_footer_mode", confidence_note=offset_note),
        sections=sections,
        strategy_tables=tables,
        index_cross_check=checks,
        warnings=warnings,
    )
    write_model(ctx.report_dir / "02_manifest.json", manifest)

    debug.update(_layout_debug(layout, nav, pages))
    debug["strategy_candidates"] = {
        "environment_pages": sorted(env_pages),
        "keyword_hits": {n: h for n, h in scores.items() if h},
        "sent_to_llm": candidates,
        "index_fallback_pages": fallback,
        "llm_judgement": {n: {"is_strategy_table": ok, "evidence": ev} for n, (ok, ev) in sorted(judged.items())},
    }
    debug["index_pages"] = index_pages
    write_json(ctx.report_dir / "02_locate_debug.json", debug)


def _layout_debug(layout: Layout, nav: dict, pages: dict[int, PageRecord]) -> dict:
    removed_by_page = {n: strip_nav(p.text_md, layout.nav_tokens)[1] for n, p in pages.items()}
    line_counts = Counter(line for lines in removed_by_page.values() for line in lines)
    return {
        "printed_page_numbers": {n: v for n, v in layout.printed_numbers.items()},
        "nav_tokens": sorted(layout.nav_tokens),
        "current_nav_by_color": {"pages": len(layout.current_nav), "used": bool(nav), "ranges": nav},
        "docling_nav_lines": {
            "pages_with_nav_lines": sorted(n for n, lines in removed_by_page.items() if lines),
            "most_common_lines": line_counts.most_common(15),
        },
    }
