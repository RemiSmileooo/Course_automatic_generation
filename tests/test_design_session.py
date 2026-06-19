"""设计会话与对话修改单元测试（任务 13/14，stub LLM，不联网）。"""
from __future__ import annotations

import pytest

from src import design_session, llm_slide
from src.models import Course, Slide, Segment


def _fake_course():
    c = Course(title="测试课程")
    c.slides.append(Slide(title="封面", kind="cover",
                          segments=[Segment(kind="intro", script="开场")],
                          content_html='<section class="slide"><h1>封面</h1></section>'))
    c.slides.append(Slide(title="正文", kind="content",
                          segments=[Segment(kind="intro", script="讲解")],
                          content_html='<section class="slide"><h2>正文</h2></section>'))
    return c


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(design_session, "SESSION_DIR", tmp_path / ".sessions")
    design_session._SESSIONS.clear()


def test_course_to_payload_roundtrip():
    c = _fake_course()
    payload = llm_slide.course_to_payload(c)
    assert payload["title"] == "测试课程"
    assert len(payload["slides"]) == 2
    # 新结构：steps 承载口播
    assert payload["slides"][0]["steps"][0]["narration"] == "开场"
    assert "封面" in payload["slides"][0]["html"]
    # 反向
    c2 = llm_slide.course_from_payload(payload)
    assert c2.slides[0].content_html == payload["slides"][0]["html"]
    assert c2.slides[0].segments[0].script == "开场"


def test_create_and_get_session(monkeypatch):
    monkeypatch.setattr(llm_slide, "generate_course_html", lambda text: _fake_course())
    sess = design_session.create_session("一段文案")
    assert sess is not None
    assert len(sess.slides) == 2
    # 可从内存/磁盘读取
    got = design_session.get_session(sess.sid)
    assert got is not None and got.sid == sess.sid


def test_create_returns_none_when_llm_unavailable(monkeypatch):
    monkeypatch.setattr(llm_slide, "generate_course_html", lambda text: None)
    assert design_session.create_session("x") is None


def test_revise_success_updates(monkeypatch):
    monkeypatch.setattr(llm_slide, "generate_course_html", lambda text: _fake_course())
    sess = design_session.create_session("文案")
    new_payload = {"title": "改后标题", "slides": [
        {"kind": "cover", "narration": "新开场", "html": '<section class="slide"><h1>新封面</h1></section>'},
        {"kind": "content", "narration": "讲解", "html": '<section class="slide"><h2>正文</h2></section>'},
    ]}
    monkeypatch.setattr(llm_slide, "revise_course_html", lambda payload, instr: new_payload)
    updated = design_session.revise_session(sess.sid, "把封面改一下")
    assert updated.title == "改后标题"
    assert "新封面" in updated.slides[0]["html"]


def test_revise_failure_keeps_old(monkeypatch):
    monkeypatch.setattr(llm_slide, "generate_course_html", lambda text: _fake_course())
    sess = design_session.create_session("文案")
    old_html = sess.slides[0]["html"]
    monkeypatch.setattr(llm_slide, "revise_course_html", lambda payload, instr: None)
    updated = design_session.revise_session(sess.sid, "乱改")
    # 失败保留旧版
    assert updated.slides[0]["html"] == old_html
