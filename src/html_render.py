"""HTML/CSS 幻灯片渲染器（Playwright 截图）。

与现有契约完全一致：
- 为每页生成一张干净底图，写入 ``slide.base_image``；
- 把整段口播稿按句切成多条字幕（复用 ``slides.build_captions``）；
- 页面 HTML 只渲染一次（A 方案：整页内容一次性显示，无逐点动画），
  每条字幕只是在同一页底部叠加不同文字后再截一帧；
- 每条字幕写入 ``caption.frame_path``，供 ``video.compose_course`` 合成。

页面设计由本模块自带的一套干净 CSS 模板提供（封面 / 要点 / 表格三种版式），
配色取自 ``settings.theme``，与 Pillow 版保持一致的主题观感。

浏览器不可用时由上层（pipeline）回退到 ``slides.render_slide_frames``。
"""
from __future__ import annotations

import html
from pathlib import Path

from .config import settings
from .models import Slide
from .slides import build_captions


# --------------------------------------------------------------------------- #
# 对外接口（与 slides.render_slide_frames 同签名）
# --------------------------------------------------------------------------- #
def render_slide_frames(slide: Slide, out_dir: str | Path, subtitle: bool = True) -> None:
    """用无头浏览器把一页渲染成底图 + 逐字幕帧。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # 缺依赖 → 让上层回退 Pillow
        raise RuntimeError("缺少 HTML 渲染依赖 playwright") from exc

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        try:
            page = browser.new_page(
                viewport={"width": settings.width, "height": settings.height},
                device_scale_factor=1,
            )
            _render_with_page(page, slide, out_dir, subtitle)
        finally:
            browser.close()


def _launch_browser(pw):
    errors = []
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return pw.chromium.launch(headless=True, **kwargs)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc).splitlines()[0])
    raise RuntimeError("无法启动 Chromium/Edge 浏览器用于 HTML 截图：" + "；".join(errors))


def _render_with_page(page, slide: Slide, out_dir: Path, subtitle: bool) -> None:
    # 1) 干净底图（无字幕）。页面 HTML 只渲染一次，并把 HTML 源码落盘便于查看。
    base_html = _page_html(slide, caption_text="", subtitle=False)
    html_path = out_dir / f"slide_{slide.index:02d}.html"
    html_path.write_text(base_html, encoding="utf-8")

    base_path = out_dir / f"slide_{slide.index:02d}_base.png"
    page.set_content(base_html, wait_until="load")
    page.screenshot(path=str(base_path), full_page=False)
    slide.base_image = str(base_path)

    # 2) 逐片段、逐字幕：同一页底部叠加不同字幕文字后各截一帧。
    for si, seg in enumerate(slide.segments):
        if not seg.captions:
            seg.captions = build_captions(seg.script, seg.duration)
        for ci, cap in enumerate(seg.captions):
            fp = out_dir / f"slide_{slide.index:02d}_seg_{si:02d}_cap_{ci:02d}.png"
            page.set_content(
                _page_html(slide, caption_text=cap.text, subtitle=subtitle),
                wait_until="load",
            )
            page.screenshot(path=str(fp), full_page=False)
            cap.frame_path = str(fp)
        seg.frame_path = seg.captions[0].frame_path if seg.captions else None


# --------------------------------------------------------------------------- #
# HTML 组装
# --------------------------------------------------------------------------- #
def _page_html(slide: Slide, caption_text: str, subtitle: bool) -> str:
    body = _cover(slide) if slide.kind == "cover" else _content(slide)
    caption = ""
    if subtitle and caption_text:
        caption = f'<div class="caption">{_escape(caption_text)}</div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>{_css()}</style></head>
<body>
  <main class="slide">
    {body}
    {caption}
  </main>
</body>
</html>"""


def _cover(slide: Slide) -> str:
    subtitle = slide.bullets[0] if slide.bullets else ""
    return f"""
    <section class="cover">
      <div class="eyebrow">COURSE</div>
      <h1>{_escape(slide.title)}</h1>
      <div class="accent-line"></div>
      {f'<p class="cover-sub">{_escape(subtitle)}</p>' if subtitle else ''}
    </section>"""


def _content(slide: Slide) -> str:
    if slide.layout == "table" and slide.table_rows:
        inner = _table(slide)
    else:
        inner = _bullets(slide)
    return f"""
    <section class="content">
      <header>
        <h2>{_escape(slide.title)}</h2>
        <div class="accent-line"></div>
      </header>
      {inner}
    </section>"""


def _bullets(slide: Slide) -> str:
    if not slide.bullets:
        return ""
    n = len(slide.bullets)
    items = []
    for i, text in enumerate(slide.bullets):
        head, body = _split(text)
        body_html = f'<p class="card-text">{_escape(body)}</p>' if body else ""
        items.append(
            f"""<article class="card">
              <div class="badge">{i + 1}</div>
              <div class="card-body"><div class="card-title">{_escape(head)}</div>{body_html}</div>
            </article>"""
        )
    return f'<div class="cards count-{min(n, 6)}">{"".join(items)}</div>'


def _table(slide: Slide) -> str:
    col_count = max(1, len(slide.table_headers), *(len(r) for r in slide.table_rows))
    headers = _pad(slide.table_headers or [f"列 {i + 1}" for i in range(col_count)], col_count)
    rows = [_pad(r, col_count) for r in slide.table_rows]
    head = "".join(f"<th>{_escape(c)}</th>" for c in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_escape(c)}</td>" for c in row) + "</tr>" for row in rows
    )
    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>{head}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


# --------------------------------------------------------------------------- #
# CSS（取主题配色）
# --------------------------------------------------------------------------- #
def _css() -> str:
    th = settings.theme
    bg_top = _rgb(th.bg_top)
    bg_bottom = _rgb(th.bg_bottom)
    title = _rgb(th.title)
    text = _rgb(th.text)
    accent = _rgb(th.accent)
    dark = _is_dark(th.bg_bottom)
    panel = "rgba(255,255,255,.72)" if not dark else "rgba(255,255,255,.08)"
    border = "rgba(20,20,25,.10)" if not dark else "rgba(255,255,255,.14)"
    shadow = "0 24px 60px rgba(18,24,40,.12)" if not dark else "0 24px 70px rgba(0,0,0,.4)"
    # 字幕条按主题明暗自适应
    cap_bg = "rgba(20,22,30,.82)" if not dark else "rgba(245,246,250,.92)"
    cap_text = "#ffffff" if not dark else "#16181f"
    top_bar = (
        f".slide::before{{content:'';position:absolute;left:0;top:0;width:100%;height:10px;background:{accent};}}"
        if th.show_top_bar else ""
    )
    return f"""
    {_font_faces()}
    *{{box-sizing:border-box;}}
    html,body{{margin:0;width:{settings.width}px;height:{settings.height}px;overflow:hidden;}}
    body{{font-family:'CourseCJK','Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif;
      color:{text};background:linear-gradient(145deg,{bg_top},{bg_bottom});}}
    .slide{{position:relative;width:100vw;height:100vh;padding:90px 120px;overflow:hidden;}}
    {top_bar}
    /* 封面 */
    .cover{{height:100%;display:flex;flex-direction:column;justify-content:center;}}
    .eyebrow{{letter-spacing:.28em;font-size:30px;font-weight:800;color:{accent};margin-bottom:30px;}}
    .cover h1{{margin:0;font-size:96px;line-height:1.08;font-weight:850;letter-spacing:-.03em;color:{title};max-width:1400px;}}
    .cover-sub{{margin:40px 0 0;font-size:40px;line-height:1.4;color:{text};max-width:1200px;}}
    /* 内容 */
    .content{{height:100%;display:flex;flex-direction:column;}}
    header h2{{margin:0;font-size:64px;line-height:1.12;font-weight:850;letter-spacing:-.025em;color:{title};}}
    .accent-line{{width:120px;height:8px;border-radius:99px;background:{accent};margin:26px 0 0;}}
    .cover .accent-line{{margin:36px 0;width:180px;height:9px;}}
    /* 卡片 */
    .cards{{flex:1;display:flex;flex-direction:column;gap:24px;justify-content:center;margin-top:48px;max-width:1500px;}}
    .card{{display:grid;grid-template-columns:78px 1fr;gap:26px;align-items:center;
      padding:30px 36px;border-radius:28px;background:{panel};border:1px solid {border};box-shadow:{shadow};}}
    .badge{{width:62px;height:62px;border-radius:18px;display:flex;align-items:center;justify-content:center;
      background:{accent};color:#fff;font-size:30px;font-weight:900;}}
    .card-title{{font-size:38px;line-height:1.22;font-weight:850;color:{title};}}
    .card-text{{margin:8px 0 0;font-size:28px;line-height:1.4;font-weight:600;color:{text};}}
    .cards.count-5 .card,.cards.count-6 .card{{padding:22px 32px;gap:22px;}}
    .cards.count-5 .card-title,.cards.count-6 .card-title{{font-size:32px;}}
    .cards.count-5 .card-text,.cards.count-6 .card-text{{font-size:24px;}}
    /* 表格 */
    .table-wrap{{flex:1;margin-top:48px;border-radius:26px;overflow:hidden;border:1px solid {border};
      background:{panel};box-shadow:{shadow};align-self:start;width:100%;}}
    table{{width:100%;border-collapse:collapse;table-layout:fixed;}}
    th{{background:{accent};color:#fff;font-size:30px;padding:24px 26px;text-align:left;line-height:1.2;}}
    td{{font-size:27px;padding:22px 26px;border-top:1px solid {border};color:{text};line-height:1.34;vertical-align:middle;}}
    tbody tr:nth-child(even) td{{background:rgba(127,127,127,.08);}}
    /* 字幕 */
    .caption{{position:absolute;left:50%;bottom:52px;transform:translateX(-50%);
      max-width:80%;padding:18px 38px;border-radius:20px;background:{cap_bg};color:{cap_text};
      font-size:36px;line-height:1.4;text-align:center;font-weight:780;
      box-shadow:0 18px 54px rgba(0,0,0,.22);white-space:nowrap;}}
    """


def _font_faces() -> str:
    faces = []
    if settings.font_regular:
        faces.append(
            "@font-face{font-family:'CourseCJK';"
            f"src:url('{Path(settings.font_regular).as_uri()}');font-weight:400 800;}}"
        )
    if settings.font_bold and settings.font_bold != settings.font_regular:
        faces.append(
            "@font-face{font-family:'CourseCJK';"
            f"src:url('{Path(settings.font_bold).as_uri()}');font-weight:800 950;}}"
        )
    return "\n".join(faces)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _split(text: str) -> tuple[str, str]:
    """把 '要点：说明' 切成 (要点, 说明)；没有分隔符则整体作为标题。"""
    text = " ".join(str(text or "").split())
    for sep in ("：", ":", "，", ",", "。", "；", ";"):
        if sep in text:
            head, body = text.split(sep, 1)
            if 2 <= len(head) <= 30 and body.strip():
                return head.strip(), body.strip()
    return text, ""


def _pad(row, size: int) -> list[str]:
    values = [str(x) for x in row]
    return values + [""] * (size - len(values))


def _escape(value: str) -> str:
    return html.escape(str(value or ""), quote=True)


def _rgb(value: tuple[int, int, int]) -> str:
    return f"rgb({value[0]},{value[1]},{value[2]})"


def _is_dark(value: tuple[int, int, int]) -> bool:
    return (value[0] * 299 + value[1] * 587 + value[2] * 114) / 1000 < 110
