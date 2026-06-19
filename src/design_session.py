"""设计会话服务：管理"设计-预览-对话修改-确认生产"工作流的状态。

- create_session(text)        文案 → LLM 生成整套设计 → 新会话
- get_session(sid)            读取会话
- revise_session(sid, instr)  对话式修改当前设计（失败保留旧版）
- produce(sid, run_dir, ...)  用当前设计走生产（TTS→渲染→合成）

会话存进程内存 + 落盘 runs/.sessions/<sid>.json（重启可恢复）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional

from . import llm_slide, pipeline

SESSION_DIR = Path("runs") / ".sessions"

# sid -> DesignSession（进程内存）
_SESSIONS: dict[str, "DesignSession"] = {}


@dataclass
class DesignSession:
    sid: str
    source_text: str
    title: str
    slides: list[dict] = field(default_factory=list)   # [{kind, narration, html}]
    history: list[dict] = field(default_factory=list)   # [{role, content}]

    def payload(self) -> dict:
        return {"title": self.title, "slides": self.slides}


# --------------------------------------------------------------------------- #
def create_session(text: str) -> Optional[DesignSession]:
    """生成设计并创建会话。LLM 不可用/失败返回 None。"""
    course = llm_slide.generate_course_html(text)
    if course is None:
        return None
    payload = llm_slide.course_to_payload(course)
    sid = uuid.uuid4().hex[:12]
    sess = DesignSession(
        sid=sid,
        source_text=text,
        title=payload.get("title", "课程讲解"),
        slides=payload.get("slides", []),
        history=[{"role": "system", "content": "初始设计已生成"}],
    )
    _save(sess)
    return sess


def get_session(sid: str) -> Optional[DesignSession]:
    if sid in _SESSIONS:
        return _SESSIONS[sid]
    # 尝试从磁盘恢复
    p = _path(sid)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sess = DesignSession(**data)
            _SESSIONS[sid] = sess
            return sess
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    return None


def revise_session(sid: str, instruction: str) -> Optional[DesignSession]:
    """对话式修改。成功更新并返回会话；失败返回当前(未改)会话或 None。"""
    sess = get_session(sid)
    if sess is None:
        return None
    new_payload = llm_slide.revise_course_html(sess.payload(), instruction)
    sess.history.append({"role": "user", "content": instruction})
    if not new_payload or not new_payload.get("slides"):
        sess.history.append({"role": "assistant", "content": "（修改失败，已保留上一版）"})
        _save(sess)
        return sess  # 保留旧版
    sess.title = new_payload.get("title", sess.title)
    sess.slides = new_payload["slides"]
    sess.history.append({"role": "assistant", "content": "已按要求更新设计"})
    _save(sess)
    return sess


def produce(
    sid: str,
    run_dir: str | Path,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    subtitle: bool = True,
    voice: str | None = None,
) -> Optional[dict]:
    """用会话当前设计走生产。会话不存在返回 None。"""
    sess = get_session(sid)
    if sess is None:
        return None
    from .config import settings
    if voice:
        settings.minimax_voice = voice
    course = llm_slide.course_from_payload(sess.payload())
    if course is None:
        return None
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "00_input.txt").write_text(sess.source_text, encoding="utf-8")
    course.to_json(run_dir / "01_structure.json")
    return pipeline.run_production(course, run_dir, progress_cb=progress_cb, subtitle=subtitle)


# --------------------------------------------------------------------------- #
def _path(sid: str) -> Path:
    return SESSION_DIR / f"{sid}.json"


def _save(sess: DesignSession) -> None:
    _SESSIONS[sess.sid] = sess
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        _path(sess.sid).write_text(json.dumps(asdict(sess), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
