"""Slide.content_html 序列化往返测试（任务 4）。"""
from __future__ import annotations

import json

from src.models import Course, Slide, Segment


def test_content_html_roundtrip(tmp_path):
    course = Course(title="课程", subtitle="")
    course.slides.append(Slide(title="封面", kind="cover",
                               segments=[Segment(kind="intro", script="开场")],
                               content_html="<section class='slide'>x</section>"))
    course.slides.append(Slide(title="正文", kind="content"))  # content_html 缺省 None

    p = tmp_path / "c.json"
    course.to_json(p)
    loaded = Course.load_json(p)

    assert loaded.slides[0].content_html == "<section class='slide'>x</section>"
    assert loaded.slides[1].content_html is None


def test_to_json_serializable():
    from dataclasses import asdict

    course = Course(title="t", slides=[Slide(title="a", content_html="<div>1</div>")])
    s = json.dumps(asdict(course), ensure_ascii=False)
    assert "content_html" in s
