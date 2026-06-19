"""架构 Y：LLM 端到端生成课程方案（每页 内容层HTML + 口播稿）。

单次 LLM 调用：原始文案 + 设计系统规范 → JSON：
  { "title": "...", "slides": [ {kind, narration, html}, ... ] }

每页的 html 是"内容层"纯 HTML（根元素 <section class="slide">），写入
slide.content_html；narration 写入该页 intro segment 的 script（供 TTS/字幕）。

无 Key / 调用失败 / 解析失败 → 返回 None，由上层走规则兜底。
启用缓存：key = hash(text + 设计系统版本 + model)。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from .config import settings
from .models import Course, Slide, Segment
from . import slide_design

LAST_STATUS = {"llm": False, "note": "", "cached": False}

CACHE_DIR = Path("runs") / ".cache"

_SYSTEM = (
    "你是一名资深课程设计师、信息可视化设计师和前端工程师。"
    "你要把一段上课文案设计成一套高质量的课程 PPT：自主决定分页、每页版式、每页讲解口播稿，"
    "并为每页直接产出 HTML。严格输出 JSON，不要输出任何多余文字。"
)


def _user_prompt(text: str) -> str:
    return f"""请把下面这篇上课文案，设计成一套课程 PPT 视频的逐页方案（带逐要点高亮）。

{slide_design.design_spec_for_prompt()}

【任务要求】
- 自主决定分页数量，通常 6~12 页（随文案长度）；第一页 kind="cover"，最后一页 kind="summary"，中间 kind="content"。
- 每页给出：
  - kind：cover / content / summary
  - html：这一页的 base 内容层 HTML，根元素必须是 <section class="slide">…</section>。
    把本页可被"逐个讲到"的要点/卡片/区块，分别加上标记属性 data-hl="1"、data-hl="2" …（从 1 开始按讲解顺序编号）。
    并在本页 HTML 内自带一个 <style>，自行设计两种高亮态样式：
      .hl-on  —— 被讲到时该元素的强调效果（如描边/提亮/上浮/变色，你自由设计，要明显好看）
      .hl-dim —— 其它未讲到元素的弱化效果（如降低透明度/去饱和，可选）
    注意：base 状态下不要预先加 .hl-on/.hl-dim（系统会在讲到时自动给对应 data-hl 元素加这些 class）。
  - steps：讲解分段数组，按顺序播放。每个元素 {{"focus": 数字, "narration": "这一段口播稿"}}：
      focus=0 表示开场/过渡（不高亮任何元素）；focus=N 表示讲到并高亮 data-hl="N" 的元素。
      narration 自然口语、有讲课感，讲的就是当前高亮的内容；封面页通常只有一个 focus=0 的开场。
- 口播分段化：开场(可选 focus=0) + 每个要点各一段(focus=1,2,3…)，像老师逐点讲。
- 内容必须忠实于给定文案，不要编造文案里没有的事实。

【只输出如下 JSON】
{{"title":"课程标题","slides":[
  {{"kind":"cover","html":"<section class=\\"slide\\">…</section>","steps":[{{"focus":0,"narration":"开场白…"}}]}},
  {{"kind":"content","html":"<section class=\\"slide\\"><style>.hl-on{{…}} .hl-dim{{…}}</style>…<div data-hl=\\"1\\">…</div><div data-hl=\\"2\\">…</div></section>","steps":[{{"focus":0,"narration":"过渡…"}},{{"focus":1,"narration":"讲第一个要点…"}},{{"focus":2,"narration":"讲第二个要点…"}}]}}
]}}

【文案】
\"\"\"
{text.strip()}
\"\"\""""


def generate_course_html(text: str, use_cache: bool = True) -> Optional[Course]:
    """端到端生成。成功返回 Course；任何失败返回 None。"""
    LAST_STATUS.update(llm=False, note="", cached=False)
    if not settings.llm_available():
        LAST_STATUS["note"] = "未配置 OPENAI_API_KEY"
        return None

    key = _cache_key(text)
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            course = _course_from_payload(cached)
            if course:
                LAST_STATUS.update(llm=True, cached=True, note="命中缓存")
                return course

    payload = None
    last_err = ""
    for attempt in range(2):  # 最多重试 1 次
        try:
            raw = _chat(_SYSTEM, _user_prompt(text))
            payload = _parse_payload(raw)
            if payload and payload.get("slides"):
                break
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            payload = None

    if not payload or not payload.get("slides"):
        LAST_STATUS["note"] = f"端到端生成失败：{last_err or '空结果'}"
        return None

    course = _course_from_payload(payload)
    if not course or not course.slides:
        LAST_STATUS["note"] = "解析课程方案为空"
        return None

    if use_cache:
        _cache_put(key, payload)
    LAST_STATUS.update(llm=True, cached=False, note="")
    return course


# --------------------------------------------------------------------------- #
# 导入适配：用户自带 HTML + 文案 → 适配成系统结构（切分/补高亮/分段口播/最小改动）
# --------------------------------------------------------------------------- #
_IMPORT_SYSTEM = (
    "你是一名资深课程 PPT 工程师。用户提供了一份已经设计好的 HTML 课件，以及配套课程文案。"
    "你的任务不是重新设计，而是把这份 HTML 适配进课程视频生产管线：切分成逐页、为每页补充逐要点高亮标记与样式、"
    "并依据文案为每页生成分段口播稿。尽量保留原设计，只做最小必要改动。严格输出 JSON，不要多余文字。"
)


def import_course_html(html: str, text: str = "", use_cache: bool = True) -> Optional[Course]:
    """把用户上传的 HTML（+可选文案）适配成 Course。失败返回 None。"""
    LAST_STATUS.update(llm=False, note="", cached=False)
    if not settings.llm_available():
        LAST_STATUS["note"] = "未配置 OPENAI_API_KEY"
        return None

    key = _cache_key("IMPORT::" + html + "::" + (text or ""))
    if use_cache:
        cached = _cache_get(key)
        if cached is not None:
            course = _course_from_payload(cached)
            if course:
                LAST_STATUS.update(llm=True, cached=True, note="命中缓存")
                return course

    payload, last_err = None, ""
    for _ in range(2):
        try:
            raw = _chat(_IMPORT_SYSTEM, _import_prompt(html, text))
            payload = _parse_payload(raw)
            if payload and payload.get("slides"):
                break
        except Exception as e:  # noqa: BLE001
            last_err = str(e); payload = None
    if not payload or not payload.get("slides"):
        LAST_STATUS["note"] = f"导入适配失败：{last_err or '空结果'}"
        return None

    course = _course_from_payload(payload)
    if not course or not course.slides:
        LAST_STATUS["note"] = "导入解析为空"
        return None
    if use_cache:
        _cache_put(key, payload)
    LAST_STATUS.update(llm=True, cached=False, note="")
    return course


def _import_prompt(html: str, text: str) -> str:
    w, h = settings.width, settings.height
    text_block = (
        f"\n【配套课程文案（用于改写每页口播稿，使讲解自然、与画面一致）】\n\"\"\"\n{text.strip()}\n\"\"\"\n"
        if text.strip() else "\n（用户未提供文案，请依据 HTML 内容自行撰写自然口播稿。）\n"
    )
    return f"""请把下面这份"已设计好的 HTML 课件"适配进课程视频生产管线。

{slide_design.design_spec_for_prompt()}

【适配任务（尽量保留原设计，只做最小必要改动）】
1. 切分：把整份 HTML 按页切成多页（原稿常用 “NN / 总数” 之类页脚或多个区块分页）。每页输出一个 <section class="slide">…</section> 的 base HTML。
   - 尽量保留原有文字、结构、视觉风格，不要重写设计。
2. 尺寸适配（仅在不合规时改）：目标画布 {w}×{h}px。若原稿字号过小/使用 rem/em/百分比/未留底部字幕区，则改为 px 并适配到该画布、底部留约 {slide_design.CAPTION_SAFE_ZONE}px 字幕安全区；若已合适则不动。
3. 补高亮：为每页"可被逐个讲到"的要点/卡片/区块加 data-hl="1"、"2"…（按讲解顺序），并在该页内补一个 <style> 定义 .hl-on（被讲到时强调）与 .hl-dim（其余弱化）。base 状态不要预先加这两个 class。
4. 分段口播 steps：依据文案与该页内容，为每页生成 steps 数组，每个 {{"focus":数字,"narration":"口播稿"}}：focus=0 为开场/过渡，focus=N 高亮 data-hl="N"。讲稿自然口语、忠实内容，不臆造。
5. 不要在页面里写字幕/讲稿文字；不要 <script>/外链。

【只输出如下 JSON】
{{"title":"课程标题","slides":[{{"kind":"cover|content|summary","html":"<section class=\\"slide\\">…</section>","steps":[{{"focus":0,"narration":"…"}}]}}]}}

【已设计好的 HTML】
\"\"\"
{html.strip()}
\"\"\"{text_block}"""
# --------------------------------------------------------------------------- #
_REVISE_SYSTEM = (
    "你是一名资深课程 PPT 设计师。用户会给你当前整套幻灯片的设计(JSON)，以及一句修改要求。"
    "请按要求修改，返回【完整的、同结构】的 JSON（所有页都要在，未提及的页保持原样）。"
    "严格输出 JSON，不要多余文字。"
)


def revise_course_html(current_payload: dict, instruction: str) -> Optional[dict]:
    """对话式修改：当前设计 payload + 指令 → 更新后的 payload(dict)。失败返回 None。

    payload 结构同 generate：{"title":..., "slides":[{kind,narration,html}]}。
    支持单页或整套修改；未提及页应保持原样（依赖 LLM 遵从）。
    """
    if not settings.llm_available():
        return None
    user = (
        f"{slide_design.design_spec_for_prompt()}\n\n"
        f"【当前设计 JSON】\n{json.dumps(current_payload, ensure_ascii=False)}\n\n"
        f"【修改要求】\n{instruction.strip()}\n\n"
        "请返回修改后的完整 JSON（结构不变：{title, slides:[{kind, html, steps:[{focus,narration}]}]}）。"
        "html 是 base 内容层、根元素 <section class=\"slide\">，可高亮元素用 data-hl=\"1\".. 标记，"
        "页内自带 <style> 定义 .hl-on/.hl-dim 高亮态；steps 的 focus=0 为开场、focus=N 高亮 data-hl=N。"
        "全部用 px，不要含字幕/script(高亮切换由系统完成)/外链。未提及的页保持原样。"
    )
    for _ in range(2):  # 最多重试 1 次
        try:
            raw = _chat(_REVISE_SYSTEM, user)
            payload = _parse_payload(raw)
            if payload and payload.get("slides"):
                # 清洗每页 html
                for s in payload["slides"]:
                    if isinstance(s, dict) and s.get("html"):
                        s["html"] = clean_html(str(s["html"]))
                return payload
        except Exception:  # noqa: BLE001
            continue
    return None


def course_to_payload(course: Course) -> dict:
    """把 Course 还原成设计 payload（供修改/落盘）。含 steps（focus+narration）。"""
    slides = []
    for s in course.slides:
        steps = []
        for seg in s.segments:
            focus = (seg.bullet_index + 1) if seg.kind == "bullet" and seg.bullet_index >= 0 else 0
            steps.append({"focus": focus, "narration": seg.script})
        slides.append({"kind": s.kind, "html": s.content_html or "", "steps": steps})
    return {"title": course.title, "slides": slides}


def course_from_payload(payload: dict) -> Optional[Course]:
    """公开包装：payload → Course。"""
    return _course_from_payload(payload)


# --------------------------------------------------------------------------- #
# LLM 调用
# --------------------------------------------------------------------------- #
def _chat(system: str, user: str) -> str:
    import httpx
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        http_client=httpx.Client(trust_env=False),
    )
    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.6,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content or ""


# --------------------------------------------------------------------------- #
# 解析 / 清洗
# --------------------------------------------------------------------------- #
def _parse_payload(raw: str) -> Optional[dict]:
    """把 LLM 返回解析成 dict。容忍 ```json 围栏与前后多余文字。"""
    if not raw or not raw.strip():
        return None
    text = _strip_fence(raw.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 退一步：截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _course_from_payload(payload: dict) -> Optional[Course]:
    slides_data = payload.get("slides") or []
    if not isinstance(slides_data, list) or not slides_data:
        return None
    course = Course(title=str(payload.get("title", "课程讲解")), subtitle="")
    for i, s in enumerate(slides_data):
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or ("cover" if i == 0 else "content")).strip().lower()
        if kind not in {"cover", "content", "summary"}:
            kind = "cover" if i == 0 else "content"
        content_html = clean_html(str(s.get("html") or ""))
        segments = _segments_from_slide(s)
        course.slides.append(
            Slide(title=_title_from_html(content_html) or f"第 {i + 1} 页",
                  bullets=[], segments=segments, index=i, kind=kind,
                  content_html=content_html or None)
        )
    return course


def _segments_from_slide(s: dict) -> list[Segment]:
    """把一页的 steps（新结构）或 narration（旧结构）转成 Segment 列表。

    step.focus=0 → intro（不高亮）；focus=N → bullet，bullet_index=N-1（高亮 data-hl=N）。
    """
    steps = s.get("steps")
    segments: list[Segment] = []
    if isinstance(steps, list) and steps:
        for st in steps:
            if not isinstance(st, dict):
                continue
            script = str(st.get("narration") or "").strip()
            if not script:
                continue
            try:
                focus = int(st.get("focus", 0))
            except (TypeError, ValueError):
                focus = 0
            if focus > 0:
                segments.append(Segment(kind="bullet", script=script, bullet_index=focus - 1))
            else:
                segments.append(Segment(kind="intro", script=script))
        if segments:
            return segments
    # 兜底：旧结构单段 narration
    narration = str(s.get("narration") or "").strip()
    return [Segment(kind="intro", script=narration)] if narration else []


def clean_html(raw: str) -> str:
    """去除 ```html 围栏、首尾空白。"""
    if not raw:
        return ""
    return _strip_fence(raw.strip()).strip()


def _strip_fence(text: str) -> str:
    # 去掉 ```html ... ``` 或 ``` ... ``` 围栏
    m = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


def _title_from_html(html: str) -> str:
    """从内容层 HTML 里抽个标题(h1/h2)，仅用于内部展示，可空。"""
    if not html:
        return ""
    m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    inner = re.sub(r"<[^>]+>", "", m.group(1))
    return " ".join(inner.split())[:40]


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
def _cache_key(text: str) -> str:
    h = hashlib.sha256()
    h.update(text.strip().encode("utf-8"))
    h.update(b"|")
    h.update(slide_design.DESIGN_SYSTEM_VERSION.encode("utf-8"))
    h.update(b"|")
    h.update((settings.openai_model or "").encode("utf-8"))
    return h.hexdigest()[:24]


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"slidegen_{key}.json"


def _cache_get(key: str) -> Optional[dict]:
    p = _cache_path(key)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_put(key: str, payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
