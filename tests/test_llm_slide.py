"""端到端生成的解析/清洗单元测试（任务 3，不联网）。"""
from __future__ import annotations

import json

from src import llm_slide


def test_clean_html_strips_fence():
    raw = "```html\n<section class='slide'>x</section>\n```"
    assert llm_slide.clean_html(raw) == "<section class='slide'>x</section>"
    assert llm_slide.clean_html("  <div>1</div>  ") == "<div>1</div>"


def test_parse_payload_plain_json():
    raw = '{"title":"T","slides":[{"kind":"cover","narration":"开场","html":"<section class=\\"slide\\">A</section>"}]}'
    payload = llm_slide._parse_payload(raw)
    assert payload["title"] == "T"
    assert len(payload["slides"]) == 1


def test_parse_payload_with_surrounding_text():
    raw = '这是结果：\n{"title":"T","slides":[{"kind":"cover","narration":"x","html":"<section>a</section>"}]}\n谢谢'
    payload = llm_slide._parse_payload(raw)
    assert payload is not None
    assert payload["title"] == "T"


def test_parse_payload_bad_returns_none():
    assert llm_slide._parse_payload("") is None
    assert llm_slide._parse_payload("not json at all") is None


def test_course_from_payload_maps_fields():
    payload = {
        "title": "大模型入门",
        "slides": [
            {"kind": "cover", "narration": "大家好", "html": "<section class='slide'><h1>大模型入门</h1></section>"},
            {"kind": "content", "narration": "第一点", "html": "<section class='slide'><h2>什么是 LLM</h2></section>"},
            {"kind": "summary", "narration": "总结", "html": "<section class='slide'><h2>回顾</h2></section>"},
        ],
    }
    course = llm_slide._course_from_payload(payload)
    assert course.title == "大模型入门"
    assert len(course.slides) == 3
    # 每页 html 写入 content_html，narration 写入 intro segment
    assert course.slides[0].content_html and "大模型入门" in course.slides[0].content_html
    assert course.slides[0].segments[0].script == "大家好"
    assert course.slides[0].kind == "cover"
    assert course.slides[2].kind == "summary"
    # 标题从 h1/h2 抽取
    assert course.slides[1].title == "什么是 LLM"


def test_course_from_payload_empty_returns_none():
    assert llm_slide._course_from_payload({"slides": []}) is None


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_slide, "CACHE_DIR", tmp_path / ".cache")
    key = llm_slide._cache_key("某段文案")
    assert llm_slide._cache_get(key) is None
    payload = {"title": "T", "slides": [{"kind": "cover", "narration": "x", "html": "<section>a</section>"}]}
    llm_slide._cache_put(key, payload)
    got = llm_slide._cache_get(key)
    assert got == payload


def test_generate_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(llm_slide.settings, "openai_api_key", "")
    assert llm_slide.generate_course_html("文案", use_cache=False) is None


def test_course_from_payload_with_steps():
    """新结构：steps[{focus,narration}] → 分段 segments，focus>0 为 bullet 高亮。"""
    payload = {
        "title": "课程",
        "slides": [
            {"kind": "content",
             "html": '<section class="slide"><div data-hl="1">A</div><div data-hl="2">B</div></section>',
             "steps": [
                 {"focus": 0, "narration": "过渡开场"},
                 {"focus": 1, "narration": "讲第一个要点"},
                 {"focus": 2, "narration": "讲第二个要点"},
             ]},
        ],
    }
    course = llm_slide._course_from_payload(payload)
    segs = course.slides[0].segments
    assert len(segs) == 3
    assert segs[0].kind == "intro"
    assert segs[1].kind == "bullet" and segs[1].bullet_index == 0
    assert segs[2].kind == "bullet" and segs[2].bullet_index == 1
    assert segs[1].script == "讲第一个要点"


def test_course_to_payload_roundtrip_steps():
    payload = {
        "title": "课程",
        "slides": [
            {"kind": "content",
             "html": '<section class="slide"><div data-hl="1">A</div></section>',
             "steps": [{"focus": 0, "narration": "开场"}, {"focus": 1, "narration": "要点一"}]},
        ],
    }
    course = llm_slide._course_from_payload(payload)
    back = llm_slide.course_to_payload(course)
    steps = back["slides"][0]["steps"]
    assert steps[0]["focus"] == 0 and steps[0]["narration"] == "开场"
    assert steps[1]["focus"] == 1 and steps[1]["narration"] == "要点一"
