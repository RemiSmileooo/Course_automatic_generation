"""LLM 模块：把原始文案拆解为结构化课程，并把讲解稿改写成自然口播稿。

两个阶段（对应参考方案）：
  1) structure_course : 文案 -> 课程大纲 / 每页标题 / 要点 / 每个要点的讲解稿
  2) polish_narration : 把讲解稿改写成更适合 AI 朗读的自然中文口播稿

无 OPENAI_API_KEY 时自动退化为纯规则实现，保证离线可演示。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from .config import settings
from .models import Course, Slide, Segment

# 记录最近一次结构化是否真正用上了 LLM（供上层提示降级）
LAST_STATUS = {"llm": False, "note": ""}


# --------------------------------------------------------------------------- #
# OpenAI 客户端（惰性创建）
# --------------------------------------------------------------------------- #
def _client():
    import httpx
    from openai import OpenAI

    # 某些运行环境会注入不可用的 HTTP(S)_PROXY，导致本可直连的 API 请求失败。
    return OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        http_client=httpx.Client(trust_env=False),
    )


def _chat_json(system: str, user: str) -> dict:
    """调用 LLM 并强制返回 JSON 对象。"""
    client = _client()
    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


# --------------------------------------------------------------------------- #
# 阶段 1：结构化拆解
# --------------------------------------------------------------------------- #
_STRUCTURE_SYSTEM = """你是一名资深课程设计师和信息可视化设计师，擅长把长篇上课文案设计成内容完整、版式多样的课件。
请严格输出 JSON，不要包含任何多余文字。"""

_STRUCTURE_USER_TMPL = """请把下面这篇上课文案拆解成一套课程 PPT 结构。

要求：
1. 提炼课程标题(title)与副标题(subtitle)。
2. 根据原文长度生成 8~20 页；长文（约 6000 字以上或包含 8 个以上一级章节）应生成 14~20 页。原文每个一级编号章节都必须至少有一页，重要章节可拆成两页。第一页 kind 为 "cover"，最后一页 kind 为 "summary"。
3. 不得把多个无关章节压缩到同一页。必须覆盖原文中的主要公司、部门类型、排序、案例和结论；宁可增加页数，不要为了页数限制丢内容。
4. 页面支持两种 layout：
   - "bullets"：3~6 条信息完整的要点，每条通常 18~45 字，不要固定成三条，也不要只写几个词。
   - "table"：原文包含公司对比、分类矩阵、排名或表格时使用。给出 table_headers、table_rows，并完整保留原表的重要行列；表格页 bullets 为空。
5. bullets 页面给出 bullet_scripts，与 bullets 一一对应；table 页面给出 table_row_scripts，与 table_rows 一一对应，逐行解释表格内容。
6. title 不超过 22 字；intro_script 是进入该页的一句自然过渡。
7. 封面页 bullets、table_headers、table_rows 均为空。
8. 讲解稿要口语化、有讲课感，同时保留原文的事实、公司名称、岗位名称和关键数字，不要泛化成空洞总结。

只输出如下 JSON 结构：
{
  "title": "...",
  "subtitle": "...",
  "slides": [
    {"kind":"cover","layout":"bullets","title":"...","bullets":[],"table_headers":[],"table_rows":[],"intro_script":"...","bullet_scripts":[],"table_row_scripts":[]},
    {"kind":"content","layout":"bullets","title":"...","bullets":["...","..."],"table_headers":[],"table_rows":[],"intro_script":"...","bullet_scripts":["...","..."],"table_row_scripts":[]},
    {"kind":"content","layout":"table","title":"...","bullets":[],"table_headers":["公司类型","最核心部门"],"table_rows":[["Google / Meta / TikTok","Ads、Ranking、Feed/Search、Growth、AI Infra"]],"intro_script":"...","bullet_scripts":[],"table_row_scripts":["逐行讲解..."]}
  ]
}

文案如下：
\"\"\"
{script}
\"\"\""""


def structure_course(text: str) -> Course:
    if settings.llm_available():
        try:
            data = _chat_json(_STRUCTURE_SYSTEM, _STRUCTURE_USER_TMPL.replace("{script}", text.strip()))
            course = _course_from_struct(data)
            if not course.slides:
                raise ValueError("LLM 返回空结构（可能模型只输出了思考内容）")
            LAST_STATUS.update(llm=True, note="")
            return course
        except Exception as e:  # 出错回退规则法，保证流程不中断
            print(f"[llm] 结构化调用失败，回退规则法: {e}")
            LAST_STATUS.update(llm=False, note=f"LLM 拆解失败，已用规则兜底：{e}")
            return _rule_based_structure(text)
    LAST_STATUS.update(llm=False, note="未配置 OPENAI_API_KEY，已用规则兜底拆解")
    return _rule_based_structure(text)


def _course_from_struct(data: dict) -> Course:
    course = Course(title=data.get("title", "课程讲解"), subtitle=data.get("subtitle", ""))
    for i, s in enumerate(data.get("slides", [])):
        bullets = list(s.get("bullets", []) or [])
        table_headers = [str(x).strip() for x in (s.get("table_headers", []) or [])]
        table_rows = [
            [str(cell).strip() for cell in row]
            for row in (s.get("table_rows", []) or [])
            if isinstance(row, list)
        ]
        layout = s.get("layout", "table" if table_rows else "bullets")
        bullet_scripts = list(s.get("bullet_scripts", []) or [])
        table_row_scripts = list(s.get("table_row_scripts", []) or [])
        # 对齐长度，避免越界
        while len(bullet_scripts) < len(bullets):
            bullet_scripts.append(bullets[len(bullet_scripts)])
        segments = []
        intro = (s.get("intro_script") or "").strip()
        if intro:
            segments.append(Segment(kind="intro", script=intro))
        for bi, bs in enumerate(bullet_scripts[: len(bullets)]):
            segments.append(Segment(kind="bullet", script=bs.strip(), bullet_index=bi))
        while len(table_row_scripts) < len(table_rows):
            table_row_scripts.append("；".join(table_rows[len(table_row_scripts)]))
        for ri, script in enumerate(table_row_scripts[: len(table_rows)]):
            segments.append(Segment(kind="table", script=str(script).strip(), bullet_index=ri))
        course.slides.append(
            Slide(
                title=s.get("title", course.title if i == 0 else f"第 {i} 节"),
                bullets=bullets,
                layout=layout,
                table_headers=table_headers,
                table_rows=table_rows,
                segments=segments,
                index=i,
                kind=s.get("kind", "cover" if i == 0 else "content"),
            )
        )
    return course


# --------------------------------------------------------------------------- #
# 阶段 2：口播稿改写
# --------------------------------------------------------------------------- #
_POLISH_SYSTEM = """你是一名专业的课程配音导演，负责把书面讲解稿改写成适合 AI 语音朗读的中文口播稿。
请严格输出 JSON。"""

_POLISH_USER_TMPL = """把下面这组讲解稿改写成自然、流畅、有讲课感的中文口播稿。

改写要求：
- 口语化，像老师在课堂上娓娓道来；
- 合理断句，必要处用逗号制造停顿，让语音更自然；
- 适当加入"那么/接下来/我们可以看到/其实"等口语衔接词；
- 含义不变，不要加入要点之外的新信息；
- 每条长度与原文相近，不要明显变长。

输入是一个字符串数组，请按相同顺序输出改写后的数组：
{"scripts": ["...","..."]}

原始讲解稿数组：
{items}"""


def polish_narration(course: Course) -> Course:
    """把整门课所有 segment 的口播稿统一改写（一次调用，按页处理以控长度）。"""
    if not settings.llm_available():
        return course  # 规则法产出的稿子本身取自原文，已足够自然

    for slide in course.slides:
        scripts = [seg.script for seg in slide.segments]
        if not scripts:
            continue
        try:
            data = _chat_json(
                _POLISH_SYSTEM,
                _POLISH_USER_TMPL.replace("{items}", json.dumps(scripts, ensure_ascii=False)),
            )
            polished = data.get("scripts", scripts)
            if isinstance(polished, list) and len(polished) == len(scripts):
                for seg, new in zip(slide.segments, polished):
                    if isinstance(new, str) and new.strip():
                        seg.script = new.strip()
        except Exception as e:
            print(f"[llm] 口播改写失败(第{slide.index}页)，保留原稿: {e}")
    return course


# --------------------------------------------------------------------------- #
# 规则兜底：无 LLM 时把文案切成结构
# --------------------------------------------------------------------------- #
def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _rule_based_structure(text: str) -> Course:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    if not blocks:
        blocks = [text.strip()]

    # 第一行作为标题
    first_line = blocks[0].splitlines()[0].strip()
    title = first_line if len(first_line) <= 30 else "课程讲解"
    course = Course(title=title, subtitle="AI 自动生成课程")

    # 封面
    cover_intro = "同学们好，欢迎来到本节课程，下面我们正式开始。"
    if len(blocks) > 1:
        cover_intro = _split_sentences(blocks[1])[0] if _split_sentences(blocks[1]) else cover_intro
    course.slides.append(
        Slide(title=title, bullets=[], segments=[Segment(kind="intro", script=cover_intro)], index=0, kind="cover")
    )

    # 内容页：每个段落一页
    content_blocks = blocks[1:] if len(blocks) > 1 else blocks
    idx = 1
    for block in content_blocks:
        sentences = _split_sentences(block)
        if not sentences:
            continue
        # 标题取段落首句的核心，要点取后续句子（最多4条）
        page_title = _short_title(sentences[0])
        body = sentences[1:] if len(sentences) > 1 else sentences
        bullets_src = body[:4] if len(body) >= 2 else sentences[:4]
        bullets = [_to_bullet(s) for s in bullets_src]
        segments = [Segment(kind="intro", script=sentences[0])]
        for bi, s in enumerate(bullets_src):
            segments.append(Segment(kind="bullet", script=s, bullet_index=bi))
        course.slides.append(
            Slide(title=page_title, bullets=bullets, segments=segments, index=idx, kind="content")
        )
        idx += 1

    # 标记最后一页为总结
    if len(course.slides) > 1:
        course.slides[-1].kind = "summary"
    return course


def _short_title(sentence: str) -> str:
    s = re.sub(r"[，,。.！!？?；;：:]", "", sentence)
    return s[:16] if len(s) > 16 else s


def _to_bullet(sentence: str) -> str:
    s = sentence.strip().rstrip("。.！!？?；;")
    return s[:22] + ("…" if len(s) > 22 else "")
