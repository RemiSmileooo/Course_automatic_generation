"""LLM-HTML 渲染器：校验与组装单元测试（任务 5，不启动浏览器）。"""
from __future__ import annotations

from src import llm_html_render as r
from src.config import settings
from src.models import Slide


def test_valid_content_accepts_good_html():
    assert r.is_valid_content('<section class="slide"><h2>标题</h2><p>正文内容在此</p></section>')


def test_valid_content_rejects_empty():
    assert not r.is_valid_content("")
    assert not r.is_valid_content("<section>")  # 太短/无闭合


def test_valid_content_rejects_script():
    assert not r.is_valid_content('<section class="slide"><script>alert(1)</script>正文正文正文</section>')


def test_valid_content_rejects_external_link():
    assert not r.is_valid_content('<section class="slide"><img src="https://x.com/a.png">内容内容内容内容</section>')


def test_valid_content_rejects_no_root():
    assert not r.is_valid_content("纯文本没有任何标签结构内容内容内容内容")


def test_assemble_contains_css_and_caption():
    out = r._assemble('<section class="slide"><h2>X</h2></section>', "这是一句字幕", subtitle=True)
    assert "CourseCJK" in out  # 字体注入
    assert f"{settings.width}px" in out  # 固定尺寸
    assert "这是一句字幕" in out and "caption" in out


def test_assemble_no_caption_when_disabled():
    out = r._assemble('<section class="slide"><h2>X</h2></section>', "字幕", subtitle=False)
    assert 'class="caption"' not in out


def test_assemble_focus_injects_highlight_script():
    out = r._assemble('<section class="slide"><div data-hl="1">A</div></section>', "", subtitle=False, focus=1)
    assert "hl-on" in out and "hl-dim" in out
    assert "var f = 1;" in out  # 系统高亮脚本注入了 focus
    # 默认高亮兜底样式存在
    assert ".hl-on{" in out


def test_assemble_focus_zero_no_highlight():
    out = r._assemble('<section class="slide"><div data-hl="1">A</div></section>', "", subtitle=False, focus=0)
    assert "var f = 0;" in out


def test_validated_content_falls_back(monkeypatch):
    # content_html 非法 → 用兜底模板（含标题）
    s = Slide(title="兜底标题", kind="content", bullets=["A：a"], content_html="<bad")
    html = r._validated_content(s)
    assert "兜底标题" in html
    assert 'class="slide"' in html
