"""设计系统单元测试（任务 2）。"""
from __future__ import annotations

from src import slide_design as sd
from src.models import Slide


def test_base_css_has_accent_token():
    css = sd.base_css()
    assert "--accent:#ff8a00" in css.replace(" ", "")
    assert ".card" in css and ".slide" in css


def test_design_spec_mentions_safe_zone_and_bans():
    spec = sd.design_spec_for_prompt()
    assert str(sd.CAPTION_SAFE_ZONE) in spec
    assert "<script>" in spec  # 明确禁止 script
    assert "section" in spec   # 要求根元素 section.slide


def test_base_css_has_rich_components():
    css = sd.base_css()
    for cls in (".icon-card", ".vs-bad", ".vs-good", ".flow", ".manifesto", ".hero", ".stat", ".num-row", ".check-row"):
        assert cls in css, f"缺少组件类 {cls}"


def test_design_spec_is_freedom_oriented():
    """新规范应只含技术约束，不再强塞固定版式示例。"""
    spec = sd.design_spec_for_prompt()
    assert "px" in spec and "rem" in spec      # 尺寸铁律仍在
    assert "设计自由" in spec                   # 强调自由发挥
    assert "示例A" not in spec and "示例B" not in spec  # 不再固定版式示例


def test_caption_css_dark_vs_light_differ():
    light = sd.caption_css(is_dark=False)
    dark = sd.caption_css(is_dark=True)
    assert light != dark
    assert ".caption" in light


def test_fallback_cover_has_title():
    s = Slide(title="课程标题", kind="cover", bullets=["副标题"])
    html = sd.fallback_html(s)
    assert "课程标题" in html
    assert 'class="slide"' in html


def test_fallback_bullets_has_all_points():
    s = Slide(title="要点页", kind="content", bullets=["A：a", "B：b", "C：c"])
    html = sd.fallback_html(s)
    assert "A" in html and "B" in html and "C" in html
    assert html.count("badge") == 3


def test_fallback_table_renders_rows():
    s = Slide(title="对比", kind="content", layout="table",
              table_headers=["公司", "部门"], table_rows=[["G", "Ads"], ["M", "Feed"]])
    html = sd.fallback_html(s)
    assert "公司" in html and "Ads" in html and "Feed" in html
    assert "<table>" in html


def test_fallback_escapes_html():
    s = Slide(title="<script>x</script>", kind="content", bullets=[])
    html = sd.fallback_html(s)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
