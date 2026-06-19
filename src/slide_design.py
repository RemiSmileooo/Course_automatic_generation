"""设计系统：统一的视觉语言（浅色暖橙风，源自 design_preview.html）。

集中维护三处共用的同一套设计：
- base_css()           整页基础 CSS（变量 token + 通用类），供 LLM 内容层与兜底模板共用
- design_spec_for_prompt()  给 LLM 的"可用变量/类 + 硬约束"速查文本
- fallback_html(slide) 校验失败时的稳定兜底模板（cover / content / table）
- caption_css()        字幕条样式（由代码控制，定位在底部安全区）

LLM 生成的"内容层"HTML 与兜底模板都套用 base_css，从而保证视觉统一；
而布局结构（分栏/网格/时间线/大字/对照…）由 LLM 自由发挥，保证不雷同。
"""
from __future__ import annotations

import html as _html

from .config import settings

# 设计系统版本：变更时使 LLM 结果缓存失效
DESIGN_SYSTEM_VERSION = "5"

# 底部字幕安全区高度（px）。LLM 内容层不得占用该区域。
CAPTION_SAFE_ZONE = 150


def base_css() -> str:
    """整页基础 CSS：配色 token + 通用排版/组件类。尺寸固定为视频分辨率。"""
    w, h = settings.width, settings.height
    return f"""
    {_font_faces()}
    :root{{
      --bg-top:#fbfbfd; --bg-bottom:#eef0f4;
      --title:#16181f; --text:#42454f; --muted:#8a8f9e;
      --accent:#ff8a00; --accent-2:#fb923c;
      --good:#16a34a; --bad:#e0445a;
      --panel:rgba(255,255,255,.78); --border:rgba(20,20,25,.10);
      --shadow:0 24px 60px rgba(18,24,40,.10),0 4px 14px rgba(18,24,40,.05);
      --radius:24px; --radius-sm:16px;
      --font:'CourseCJK','Inter','PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif;
      --serif:'Georgia','Songti SC','Noto Serif CJK SC',serif;
      --safe:{CAPTION_SAFE_ZONE}px;
    }}
    *{{box-sizing:border-box;margin:0;padding:0;}}
    /* rem 兜底：本画布默认放大基准字号，即便 LLM 误用 rem/em 也不至于过小 */
    html{{font-size:28px;}}
    html,body{{width:{w}px;height:{h}px;overflow:hidden;}}
    body{{font-family:var(--font);color:var(--text);font-size:26px;
      background:linear-gradient(150deg,var(--bg-top),var(--bg-bottom));}}
    .slide{{position:relative;width:{w}px;height:{h}px;overflow:hidden;
      display:flex;flex-direction:column;justify-content:center;
      padding:72px 88px calc(var(--safe) + 24px);}}
    /* 文字层级 */
    .kicker{{font-size:18px;letter-spacing:.18em;font-weight:800;color:var(--accent);text-transform:uppercase;margin-bottom:14px;}}
    h1{{font-size:78px;line-height:1.08;font-weight:850;letter-spacing:-.03em;color:var(--title);}}
    h2{{font-size:52px;line-height:1.12;font-weight:850;letter-spacing:-.02em;color:var(--title);}}
    h3{{font-size:30px;line-height:1.2;font-weight:800;color:var(--title);}}
    p{{font-size:24px;line-height:1.5;color:var(--text);}}
    .muted{{color:var(--muted);}}
    .serif{{font-family:var(--serif);}}
    .hl{{color:var(--accent);}}
    .accent-line{{width:96px;height:7px;border-radius:99px;background:var(--accent);margin:22px 0;}}
    /* 通用组件 */
    .panel{{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);}}
    .card{{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
      box-shadow:var(--shadow);padding:30px 34px;}}
    .badge{{width:58px;height:58px;border-radius:16px;background:var(--accent);color:#fff;
      display:flex;align-items:center;justify-content:center;font-size:27px;font-weight:900;}}
    .pill{{display:inline-block;padding:10px 20px;border-radius:99px;background:var(--panel);
      border:1px solid var(--border);font-weight:700;font-size:18px;color:var(--title);}}
    .row{{display:flex;gap:24px;}}
    .grid{{display:grid;gap:24px;}}
    .g2{{grid-template-columns:1fr 1fr;}} .g3{{grid-template-columns:repeat(3,1fr);}} .g4{{grid-template-columns:repeat(4,1fr);}}
    table{{width:100%;border-collapse:collapse;table-layout:fixed;}}
    th{{background:var(--accent);color:#fff;font-size:26px;padding:20px 22px;text-align:left;}}
    td{{font-size:23px;padding:18px 22px;border-top:1px solid var(--border);color:var(--text);}}
    /* —— 丰富版式组件库（供 LLM 自由组合）—— */
    /* 图标卡：顶部大图标/emoji + 标题 + 说明 */
    .icon-card{{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
      box-shadow:var(--shadow);padding:36px 32px;}}
    .icon-card .ic{{font-size:52px;line-height:1;margin-bottom:18px;}}
    /* 左右对照面板：正/反、解决不了/需要做 */
    .vs-bad{{border:1px solid rgba(224,68,90,.30);background:rgba(224,68,90,.06);border-radius:var(--radius);padding:36px 34px;}}
    .vs-good{{border:1px solid rgba(22,163,74,.30);background:rgba(22,163,74,.06);border-radius:var(--radius);padding:36px 34px;}}
    .vs-bad .vt{{color:var(--bad);font-weight:800;font-size:20px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:20px;}}
    .vs-good .vt{{color:var(--good);font-weight:800;font-size:20px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:20px;}}
    .vs-bad li,.vs-good li{{list-style:none;font-size:24px;line-height:1.5;margin-bottom:16px;padding-left:32px;position:relative;color:var(--text);}}
    .vs-bad li::before{{content:"✕";position:absolute;left:0;color:var(--bad);font-weight:900;}}
    .vs-good li::before{{content:"✓";position:absolute;left:0;color:var(--good);font-weight:900;}}
    /* 横向时间线/流程：节点 + 连接线 + 箭头 */
    .flow{{display:flex;align-items:stretch;gap:0;}}
    .flow .step{{flex:1;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:32px 26px;position:relative;}}
    .flow .arrow{{display:flex;align-items:center;justify-content:center;width:56px;flex:0 0 56px;color:var(--accent);font-size:44px;font-weight:900;}}
    .flow .sn{{width:54px;height:54px;border-radius:50%;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:900;margin-bottom:18px;}}
    /* 大字宣言 */
    .manifesto{{font-size:62px;line-height:1.16;font-weight:850;letter-spacing:-.03em;color:var(--title);max-width:1500px;}}
    .manifesto em{{font-style:normal;color:var(--accent);}}
    .manifesto-serif{{font-family:var(--serif);font-style:italic;font-weight:600;}}
    /* 渐变 hero（总结/封面强调块）*/
    .hero{{border-radius:var(--radius);padding:44px;color:#fff;
      background:linear-gradient(145deg,var(--accent),var(--accent-2));box-shadow:var(--shadow);
      display:flex;flex-direction:column;justify-content:flex-end;}}
    .hero .small{{font-size:20px;letter-spacing:.14em;text-transform:uppercase;font-weight:800;opacity:.85;}}
    .hero .big{{font-size:46px;font-weight:850;line-height:1.14;margin-top:16px;}}
    /* 统计大数字 */
    .stat{{text-align:center;}}
    .stat .num{{font-size:84px;font-weight:900;line-height:1;color:var(--accent);letter-spacing:-.03em;}}
    .stat .lbl{{font-size:24px;color:var(--muted);margin-top:12px;}}
    /* 编号大列表行（左大序号 + 右标题说明）*/
    .num-row{{display:grid;grid-template-columns:92px 1fr;gap:30px;align-items:baseline;padding:26px 0;border-top:1px solid var(--border);}}
    .num-row .nn{{font-size:52px;font-weight:900;color:var(--accent);line-height:1;letter-spacing:-.03em;}}
    /* check 清单项 */
    .check-row{{display:grid;grid-template-columns:56px 1fr;gap:22px;align-items:start;background:var(--panel);
      border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:24px 28px;}}
    .check-row .ck{{width:48px;height:48px;border-radius:14px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:900;}}
    """


def caption_css(is_dark: bool = False) -> str:
    """字幕条样式：代码统一控制，定位在底部安全区内。"""
    if is_dark:
        bg, fg = "rgba(245,246,250,.94)", "#16181f"
    else:
        bg, fg = "rgba(20,22,30,.84)", "#ffffff"
    return f"""
    .caption{{position:absolute;left:50%;bottom:48px;transform:translateX(-50%);
      max-width:80%;padding:16px 36px;border-radius:18px;background:{bg};color:{fg};
      font-family:var(--font);font-size:34px;line-height:1.38;text-align:center;font-weight:780;
      box-shadow:0 18px 54px rgba(0,0,0,.22);white-space:nowrap;z-index:50;}}
    """


def design_spec_for_prompt() -> str:
    """给 LLM 的最小技术约束（只保留非删不可的硬规则，不规定任何版式/组件/示例，
    把设计自由完全交给 LLM）。"""
    w, h = settings.width, settings.height
    return f"""【技术约束（仅这些是硬性的，其余设计完全由你自由发挥）】
1. 画布：每页固定 {w}×{h} 像素（16:9 全屏幻灯片，不是网页）。根元素必须是 <section class="slide">…</section>，只输出这一页。
2. 尺寸：一律用绝对像素 px，【禁止 rem/em/%】做字号与间距（本画布默认 1rem 仅 16px，会小到看不清）。
   字号量级供参考：主标题 60–96px、小标题 28–40px、正文 22–32px。内容应充分利用整页空间。
3. 中文字体：所有文字默认会用系统中文字体（已通过 var(--font) 注入），你正常写中文即可，不要引用外部字体。
4. 底部预留约 {CAPTION_SAFE_ZONE}px 字幕安全区，不要在最底部放内容（字幕由系统另行叠加，你不要写字幕/讲稿到页面上）。
5. 安全：禁止 <script>、禁止任何 http(s) 外链/外部图片/外部字体、禁止 position:fixed。
6. 内容忠实于给定文案，不要臆造文案中没有的事实或数据。

【设计自由】
- 配色、布局、版式、排版风格【完全由你决定】，怎么好看怎么来，每页都可以不一样。
- 你可以自带 <style> 写这一页专属的 CSS，自定义任意 class、配色、网格、动效感、字体粗细等（但仍遵守上面的技术约束）。
- 背景默认是浅色，如果你想要其它背景，自己在 .slide 上设即可。
- 追求专业、有设计感、有记忆点的演示效果。
"""


def _unused_old_spec() -> str:
    """（保留旧的设计系统组件参考，当前不再注入 prompt，仅留作内部样式说明）"""
    return ""


def fallback_html(slide) -> str:
    """稳定兜底模板：LLM 产物校验失败时使用。cover / table / bullets 三种。"""
    title = _esc(slide.title)
    if slide.kind == "cover":
        sub = _esc(slide.bullets[0]) if getattr(slide, "bullets", None) else ""
        sub_html = f'<p style="font-size:30px;margin-top:26px;">{sub}</p>' if sub else ""
        return f"""<section class="slide" style="display:flex;flex-direction:column;justify-content:center;">
  <div class="kicker">COURSE OVERVIEW</div>
  <h1>{title}</h1>
  <div class="accent-line" style="width:160px;"></div>
  {sub_html}
</section>"""

    if getattr(slide, "layout", "") == "table" and getattr(slide, "table_rows", None):
        return _fallback_table(slide, title)

    return _fallback_bullets(slide, title)


def _fallback_bullets(slide, title: str) -> str:
    cards = []
    for i, b in enumerate(getattr(slide, "bullets", []) or []):
        head, body = _split(b)
        body_html = f'<p style="margin-top:6px;color:var(--text);">{_esc(body)}</p>' if body else ""
        cards.append(
            f'<div class="card" style="display:grid;grid-template-columns:70px 1fr;gap:24px;align-items:center;">'
            f'<div class="badge">{i + 1}</div><div><h3>{_esc(head)}</h3>{body_html}</div></div>'
        )
    grid = (
        f'<div class="grid" style="grid-template-rows:repeat({max(1, len(cards))},auto);margin-top:40px;">'
        + "".join(cards) + "</div>"
    ) if cards else ""
    return f"""<section class="slide">
  <div class="kicker">CHAPTER</div>
  <h2>{title}</h2>
  <div class="accent-line"></div>
  {grid}
</section>"""


def _fallback_table(slide, title: str) -> str:
    cols = max([len(slide.table_headers)] + [len(r) for r in slide.table_rows] + [1])
    headers = (slide.table_headers or [f"列 {i + 1}" for i in range(cols)])
    headers = list(headers) + [""] * (cols - len(headers))
    thead = "".join(f"<th>{_esc(c)}</th>" for c in headers)
    body = []
    for row in slide.table_rows:
        row = list(row) + [""] * (cols - len(row))
        body.append("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in row) + "</tr>")
    return f"""<section class="slide">
  <div class="kicker">COMPARISON</div>
  <h2>{title}</h2>
  <div class="accent-line"></div>
  <div class="panel" style="margin-top:40px;overflow:hidden;">
    <table><thead><tr>{thead}</tr></thead><tbody>{"".join(body)}</tbody></table>
  </div>
</section>"""


# --------------------------------------------------------------------------- #
def _font_faces() -> str:
    from pathlib import Path
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


def _split(text: str) -> tuple[str, str]:
    text = " ".join(str(text or "").split())
    for sep in ("：", ":", "，", ",", "。", "；", ";"):
        if sep in text:
            head, body = text.split(sep, 1)
            if 2 <= len(head) <= 30 and body.strip():
                return head.strip(), body.strip()
    return text, ""


def _esc(value: str) -> str:
    return _html.escape(str(value or ""), quote=True)
