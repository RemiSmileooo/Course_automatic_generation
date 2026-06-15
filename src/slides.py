"""幻灯片渲染（PIL）。

核心思路：把"一页 + 当前讲解片段"渲染成一张完整画面（frame）。
同一页的不同片段，区别只在于"哪个要点被橙色高亮"以及"底部字幕文字"，
因此每个片段就是一张静态图，配上该片段音频即可，无需在视频层做动态合成。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import re

from PIL import Image, ImageDraw, ImageFont

from .config import settings
from .models import Slide, Segment, Caption


# --------------------------------------------------------------------------- #
# 字体缓存
# --------------------------------------------------------------------------- #
_font_cache: dict = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    path = settings.font_bold if bold else settings.font_regular
    try:
        f = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    _font_cache[key] = f
    return f


def _tokenize(text: str) -> List[str]:
    """切分为换行单元：英文/数字串作为整体（不拆词），中文逐字，空白单独成元。"""
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_.+#]*|[^\S\r\n]+|[^\sA-Za-z0-9]", text)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    """按宽度换行；保留显式换行，但不把多行文本传给 textlength。"""
    lines: List[str] = []
    paragraphs = re.split(r"\r\n|\r|\n", str(text))
    for paragraph in paragraphs:
        line = ""
        for tok in _tokenize(paragraph):
            candidate = line + tok
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line.rstrip())
                line = "" if tok.isspace() else tok
            else:
                line = candidate
        if line.strip():
            lines.append(line.rstrip())
    return lines or [""]


def _fit_font(draw, text: str, max_width: int, base_size: int, bold: bool,
              min_size: int, max_lines: int = 1):
    """自动缩放字号：在 max_lines 行内放下文字，返回 (font, lines)。"""
    size = base_size
    while size > min_size:
        f = _font(size, bold)
        lines = _wrap(draw, text, f, max_width)
        if len(lines) <= max_lines:
            return f, lines
        size -= 3
    f = _font(min_size, bold)
    return f, _wrap(draw, text, f, max_width)


# --------------------------------------------------------------------------- #
# 背景
# --------------------------------------------------------------------------- #
def _gradient_bg(w: int, h: int) -> Image.Image:
    th = settings.theme
    top, bot = th.bg_top, th.bg_bottom
    base = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        r = y / max(1, h - 1)
        col = tuple(int(top[c] + (bot[c] - top[c]) * r) for c in range(3))
        draw.line([(0, y), (w, y)], fill=col)
    if th.show_top_bar:
        draw.rectangle([0, 0, w, 10], fill=th.accent)
    return base


@dataclass
class _Layout:
    bullet_boxes: List[Tuple[int, int, int, int]]  # 每个要点的 (x,y,w,h)


# --------------------------------------------------------------------------- #
# 单页布局 + 绘制（不含高亮 / 字幕）
# --------------------------------------------------------------------------- #
def _draw_page_body(img: Image.Image, slide: Slide) -> _Layout:
    w, h = img.size
    th = settings.theme
    draw = ImageDraw.Draw(img)
    margin = int(w * 0.07)

    if slide.kind == "cover":
        return _draw_cover(img, slide, margin)

    # 标题（自动缩放，保证单行不溢出）
    title_w = w - 2 * margin
    title_text = re.sub(r"\s+", " ", slide.title).strip()
    t_font, t_lines = _fit_font(draw, title_text, title_w, int(h * 0.062), True, int(h * 0.042), max_lines=1)
    draw.text((margin, int(h * 0.08)), t_lines[0], font=t_font, fill=th.title)
    # 标题下强调短线
    underline_y = int(h * 0.08) + t_font.getbbox("国")[3] + 18
    draw.rounded_rectangle(
        [margin, underline_y, margin + int(w * 0.16), underline_y + 8],
        radius=4, fill=th.accent,
    )

    if slide.layout == "table" and slide.table_rows:
        return _draw_table_body(img, slide, margin, underline_y)

    # 要点区
    bullet_count = max(1, len(slide.bullets))
    bullet_size = int(h * (0.040 if bullet_count <= 4 else 0.033))
    b_font = _font(bullet_size)
    content_x = margin + 56
    content_w = w - margin - content_x - int(w * 0.04)
    y = int(h * 0.26)
    line_gap = 14
    block_gap = int(h * 0.035)
    boxes: List[Tuple[int, int, int, int]] = []

    for bullet in slide.bullets:
        lines = _wrap(draw, bullet, b_font, content_w)
        line_h = b_font.getbbox("国")[3] + line_gap
        block_h = line_h * len(lines)
        box = (margin + 8, y - 14, w - margin - (margin + 8), block_h + 28)
        boxes.append(box)

        # 要点圆点
        dot_r = 9
        dot_cy = y + line_h // 2 - line_gap // 2
        draw.ellipse(
            [margin + 18, dot_cy - dot_r, margin + 18 + 2 * dot_r, dot_cy + dot_r],
            fill=th.bullet,
        )
        # 文本
        ty = y
        for ln in lines:
            draw.text((content_x, ty), ln, font=b_font, fill=th.text)
            ty += line_h
        y += block_h + block_gap

    return _Layout(bullet_boxes=boxes)


def _draw_table_body(img: Image.Image, slide: Slide, margin: int, underline_y: int) -> _Layout:
    w, h = img.size
    th = settings.theme
    draw = ImageDraw.Draw(img)
    headers = slide.table_headers or [f"列 {i + 1}" for i in range(max(len(r) for r in slide.table_rows))]
    col_count = max(1, len(headers), *(len(row) for row in slide.table_rows))
    rows = [row + [""] * (col_count - len(row)) for row in slide.table_rows]
    headers = headers + [""] * (col_count - len(headers))

    left, right = margin, w - margin
    top = max(int(h * 0.21), underline_y + 35)
    bottom = int(h * 0.86)
    table_w = right - left
    table_h = bottom - top
    header_h = max(58, int(table_h * 0.13))
    row_h = max(58, (table_h - header_h) // max(1, len(rows)))

    if col_count == 2:
        widths = [int(table_w * 0.30), table_w - int(table_w * 0.30)]
    else:
        widths = [table_w // col_count] * col_count
        widths[-1] += table_w - sum(widths)

    header_font = _font(max(22, int(h * 0.030)), bold=True)
    body_font = _font(max(18, int(h * (0.027 if len(rows) <= 6 else 0.022))))
    x = left
    for ci, header in enumerate(headers):
        cw = widths[ci]
        draw.rectangle([x, top, x + cw, top + header_h], fill=th.accent)
        header_lines = _wrap(draw, header, header_font, cw - 24)[:2]
        line_h = header_font.getbbox("国")[3] + 5
        ty = top + max(8, (header_h - line_h * len(header_lines)) // 2)
        for line in header_lines:
            tw = draw.textlength(line, font=header_font)
            draw.text((x + max(12, (cw - tw) / 2), ty), line, font=header_font, fill=(255, 255, 255))
            ty += line_h
        x += cw

    boxes: List[Tuple[int, int, int, int]] = []
    for ri, row in enumerate(rows):
        y = top + header_h + ri * row_h
        fill = tuple(min(255, c + (10 if ri % 2 == 0 else 18)) for c in th.bg_top)
        draw.rectangle([left, y, right, y + row_h], fill=fill, outline=th.bullet, width=1)
        boxes.append((left, y, table_w, row_h))
        x = left
        for ci, cell in enumerate(row):
            cw = widths[ci]
            if ci:
                draw.line([(x, y), (x, y + row_h)], fill=th.bullet, width=1)
            lines = _wrap(draw, cell, body_font, cw - 24)
            max_lines = max(1, (row_h - 16) // max(1, body_font.getbbox("国")[3] + 5))
            lines = lines[:max_lines]
            line_h = body_font.getbbox("国")[3] + 5
            ty = y + max(8, (row_h - line_h * len(lines)) // 2)
            for line in lines:
                draw.text((x + 12, ty), line, font=body_font, fill=th.text)
                ty += line_h
            x += cw
    return _Layout(bullet_boxes=boxes)


def _draw_cover(img: Image.Image, slide: Slide, margin: int) -> _Layout:
    w, h = img.size
    th = settings.theme
    draw = ImageDraw.Draw(img)
    sub_font = _font(int(h * 0.038))

    # 标题自动缩放：优先单行（缩小到 0.058h 仍放不下才允许两行），英文整词不拆
    title_w = w - 2 * margin
    title_text = re.sub(r"\s+", " ", slide.title).strip()
    title_font, lines = _fit_font(draw, title_text, title_w, int(h * 0.085), True, int(h * 0.058), max_lines=1)
    if len(lines) > 1:  # 单行实在放不下，退回较大字号的两行排版
        title_font, lines = _fit_font(draw, title_text, title_w, int(h * 0.085), True, int(h * 0.060), max_lines=2)
    line_h = title_font.getbbox("国")[3] + 24
    total_h = line_h * len(lines)
    y = (h - total_h) // 2 - 40
    for ln in lines:
        tw = draw.textlength(ln, font=title_font)
        draw.text(((w - tw) / 2, y), ln, font=title_font, fill=th.title)
        y += line_h

    # 装饰线
    cx = w // 2
    draw.rounded_rectangle([cx - 90, y + 16, cx + 90, y + 24], radius=4, fill=th.accent)

    subtitle = (slide.bullets[0] if slide.bullets else "") or ""
    if subtitle:
        subtitle_lines = _wrap(draw, subtitle, sub_font, w - 2 * margin)[:2]
        sy = y + 50
        sub_line_h = sub_font.getbbox("国")[3] + 12
        for subtitle_line in subtitle_lines:
            sw = draw.textlength(subtitle_line, font=sub_font)
            draw.text(((w - sw) / 2, sy), subtitle_line, font=sub_font, fill=th.text)
            sy += sub_line_h
    return _Layout(bullet_boxes=[])


# --------------------------------------------------------------------------- #
# 高亮 + 字幕（叠加在某个片段上）
# --------------------------------------------------------------------------- #
def _draw_highlight(img: Image.Image, box: Tuple[int, int, int, int]) -> None:
    th = settings.theme
    x, y, bw, bh = box
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # 半透明橙色填充
    od.rounded_rectangle([x, y, x + bw, y + bh], radius=16, fill=th.accent + (th.highlight_fill_alpha,))
    img.alpha_composite(overlay)
    # 实心橙色描边
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x + bw, y + bh], radius=16, outline=th.accent + (255,), width=5)


def _draw_subtitle(img: Image.Image, text: str) -> None:
    if not text:
        return
    w, h = img.size
    th = settings.theme
    draw = ImageDraw.Draw(img)
    font = _font(int(h * 0.034))
    margin = int(w * 0.10)
    max_w = w - 2 * margin
    lines = _wrap(draw, text, font, max_w)
    lines = lines[-2:]  # 字幕最多两行
    line_h = font.getbbox("国")[3] + 12
    block_h = line_h * len(lines)
    pad = 22
    bar_top = h - block_h - pad * 2 - int(h * 0.04)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(
        [margin - 30, bar_top, w - margin + 30, bar_top + block_h + pad * 2],
        radius=14, fill=th.subtitle_bg + (th.subtitle_alpha,),
    )
    img.alpha_composite(overlay)

    ty = bar_top + pad
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text(((w - tw) / 2, ty), ln, font=font, fill=th.subtitle_text)
        ty += line_h


# --------------------------------------------------------------------------- #
# 字幕分条：把一段口播稿切成若干短句，并按字数比例分配时长
# --------------------------------------------------------------------------- #
def build_captions(script: str, total_duration: float, max_chars: int = 30) -> list[Caption]:
    """按句末标点切句，过长的再按逗号切，最终合并到每条 <= max_chars。"""
    text = script.strip()
    if not text:
        return [Caption(text="", start=0.0, duration=total_duration)]

    # 先按句末标点切，保留标点
    sentences = [s for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]
    pieces: list[str] = []
    for s in sentences:
        s = s.strip()
        if len(s) <= max_chars:
            pieces.append(s)
            continue
        # 句子过长：按逗号/顿号再切，并贪心合并到 max_chars
        clauses = [c for c in re.split(r"(?<=[，,、；;])", s) if c.strip()]
        buf = ""
        for c in clauses:
            c = c.strip()
            if buf and len(buf) + len(c) > max_chars:
                pieces.append(buf)
                buf = c
            else:
                buf += c
        if buf:
            pieces.append(buf)

    pieces = pieces or [text]
    total_chars = sum(len(p) for p in pieces) or 1

    captions: list[Caption] = []
    acc = 0.0
    for i, p in enumerate(pieces):
        if i == len(pieces) - 1:
            dur = max(0.1, total_duration - acc)  # 最后一条吃掉余量，避免累计误差
        else:
            dur = total_duration * len(p) / total_chars
        captions.append(Caption(text=p, start=round(acc, 3), duration=round(dur, 3)))
        acc += dur
    return captions


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def render_slide_frames(slide: Slide, out_dir: str | Path, subtitle: bool = True) -> None:
    """为一页的每个片段渲染一张完整画面，写回 segment.frame_path。

    同时把不带高亮的基础页存为 slide.base_image（用于预览/调试）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w, h = settings.width, settings.height

    # 基础页（一次布局，复用 bbox）
    base = _gradient_bg(w, h).convert("RGBA")
    layout = _draw_page_body(base, slide)
    base_path = out_dir / f"slide_{slide.index:02d}_base.png"
    base.convert("RGB").save(base_path)
    slide.base_image = str(base_path)

    for si, seg in enumerate(slide.segments):
        # 带高亮的页面底图（同一片段内所有字幕共用）
        seg_base = base.copy()
        if seg.kind in {"bullet", "table"} and 0 <= seg.bullet_index < len(layout.bullet_boxes):
            _draw_highlight(seg_base, layout.bullet_boxes[seg.bullet_index])

        # 把整段口播稿切成多条字幕，逐条渲染一帧
        if not seg.captions:
            seg.captions = build_captions(seg.script, seg.duration)

        for ci, cap in enumerate(seg.captions):
            frame = seg_base.copy()
            if subtitle and cap.text:
                _draw_subtitle(frame, cap.text)
            fp = out_dir / f"slide_{slide.index:02d}_seg_{si:02d}_cap_{ci:02d}.png"
            frame.convert("RGB").save(fp)
            cap.frame_path = str(fp)

        # 片段首帧路径（兼容旧字段/预览）
        seg.frame_path = seg.captions[0].frame_path if seg.captions else None
