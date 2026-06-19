"""LLM-HTML 渲染器：把 LLM 生成的内容层 HTML 渲染成底图 + 逐句字幕帧。

与 slides.render_slide_frames 同签名，可被 pipeline 直接替换使用。

流程（每页）：
  1. 取 slide.content_html；轻量校验，失败 → slide_design.fallback_html(slide)
  2. 组装整页 = base_css + 字幕样式 + 内容层 (+ 可选字幕条)
  3. 截干净底图 → slide.base_image；落盘 slides/slide_XX.html
  4. 复用 slides.build_captions 切句；逐 Caption 叠字幕条截一帧 → caption.frame_path

字幕层始终由代码叠加，保证与 TTS 对齐（不依赖 LLM）。
浏览器不可用 → 抛 RuntimeError，由上层回退 Pillow。
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import settings
from .models import Slide
from .slides import build_captions
from . import slide_design


def render_slide_frames(slide: Slide, out_dir: str | Path, subtitle: bool = True) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("缺少 HTML 渲染依赖 playwright") from exc

    content_html = _validated_content(slide)

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        try:
            page = browser.new_page(
                viewport={"width": settings.width, "height": settings.height},
                device_scale_factor=1,
            )
            _render(page, slide, content_html, out_dir, subtitle)
        finally:
            browser.close()


def _render(page, slide: Slide, content_html: str, out_dir: Path, subtitle: bool) -> None:
    # 1) 干净底图（无高亮）+ 落盘 HTML
    base_html = _assemble(content_html, caption_text="", subtitle=False, focus=0)
    (out_dir / f"slide_{slide.index:02d}.html").write_text(base_html, encoding="utf-8")
    base_path = out_dir / f"slide_{slide.index:02d}_base.png"
    page.set_content(base_html, wait_until="load")
    page.screenshot(path=str(base_path), full_page=False)
    slide.base_image = str(base_path)

    # 2) 逐片段、逐字幕。bullet 段高亮对应 data-hl=(bullet_index+1)。
    for si, seg in enumerate(slide.segments):
        focus = (seg.bullet_index + 1) if seg.kind == "bullet" and seg.bullet_index >= 0 else 0
        if not seg.captions:
            seg.captions = build_captions(seg.script, seg.duration)
        for ci, cap in enumerate(seg.captions):
            fp = out_dir / f"slide_{slide.index:02d}_seg_{si:02d}_cap_{ci:02d}.png"
            page.set_content(_assemble(content_html, cap.text, subtitle, focus=focus), wait_until="load")
            page.screenshot(path=str(fp), full_page=False)
            cap.frame_path = str(fp)
        seg.frame_path = seg.captions[0].frame_path if seg.captions else None


# --------------------------------------------------------------------------- #
# 校验与组装
# --------------------------------------------------------------------------- #
def _validated_content(slide: Slide) -> str:
    """返回可用的内容层 HTML：LLM 产物通过校验则用之，否则兜底模板。"""
    html = (slide.content_html or "").strip()
    if is_valid_content(html):
        return html
    return slide_design.fallback_html(slide)


def is_valid_content(html: str) -> bool:
    """轻量校验（D-2）：非空、有根 section/div、标签基本闭合、无 script/外链。"""
    if not html or len(html) < 20:
        return False
    low = html.lower()
    if "<script" in low:
        return False
    if re.search(r'(src|href)\s*=\s*["\']https?://', low):
        return False
    if "<section" not in low and "<div" not in low:
        return False
    # 标签基本闭合：开/闭标签数量大致匹配（粗校验，不做完整 DOM 解析）
    opens = len(re.findall(r"<[a-zA-Z][^>/]*?>", html))
    closes = len(re.findall(r"</[a-zA-Z][^>]*?>", html))
    selfclose = len(re.findall(r"<[a-zA-Z][^>]*?/>", html))
    # 允许一定的自闭合/void 元素误差
    if closes == 0:
        return False
    if opens - selfclose - closes > 6:
        return False
    return True


def _assemble(content_html: str, caption_text: str, subtitle: bool, focus: int = 0) -> str:
    is_dark = False  # 设计系统为浅色暖橙；如未来支持深色主题，这里按主题取值
    css = (
        slide_design.base_css()
        + "\n" + _default_highlight_css()
        + "\n" + slide_design.caption_css(is_dark)
    )
    caption = ""
    if subtitle and caption_text:
        caption = f'<div class="caption">{_escape(caption_text)}</div>'
    # 系统脚本：根据 focus 给 [data-hl=focus] 加 .hl-on、其余 [data-hl] 加 .hl-dim。
    # 截图前同步执行（set_content wait_until=load 后脚本已运行）。
    hl_script = f"""<script>
(function(){{
  var f = {int(focus)};
  var nodes = document.querySelectorAll('[data-hl]');
  nodes.forEach(function(el){{
    el.classList.remove('hl-on','hl-dim');
    if(f>0){{
      if(String(el.getAttribute('data-hl'))===String(f)) el.classList.add('hl-on');
      else el.classList.add('hl-dim');
    }}
  }});
}})();
</script>"""
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>{css}</style></head>
<body>
  {content_html}
  {caption}
  {hl_script}
</body>
</html>"""


def _default_highlight_css() -> str:
    """系统默认的高亮态兜底样式。LLM 在页内自带的 .hl-on/.hl-dim 会覆盖它（同名后定义/同特异性）。

    注意：放在 base_css 之后、内容(LLM 的 <style>)之前注入，故 LLM 自定义优先。
    """
    return """
    [data-hl]{transition:none;}
    .hl-on{outline:4px solid var(--accent);outline-offset:6px;border-radius:14px;
      box-shadow:0 0 0 6px color-mix(in srgb,var(--accent) 22%,transparent);}
    .hl-dim{opacity:.38;filter:saturate(.7);}
    """


def _launch_browser(pw):
    errors = []
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return pw.chromium.launch(headless=True, **kwargs)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc).splitlines()[0])
    raise RuntimeError("无法启动 Chromium/Edge 浏览器用于 HTML 截图：" + "；".join(errors))


def _escape(value: str) -> str:
    import html as _h
    return _h.escape(str(value or ""), quote=True)
