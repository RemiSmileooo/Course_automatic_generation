"""课程数据结构定义。

层级关系：
    Course (整门课)
      └─ Slide (一页 PPT)
           └─ Segment (一页内的一个讲解片段：开场白 / 单个要点)

设计要点：把"讲解"切到 Segment 粒度，是为了让"口播讲到哪、就高亮哪"
成为可能——每个 Segment 对应一段音频、一条字幕、一个被高亮的要点。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional


@dataclass
class Caption:
    """一条字幕：一个讲解片段会按句子切成多条字幕，逐条与语音同步显示。"""
    text: str
    start: float = 0.0       # 相对所属片段音频的起始秒
    duration: float = 0.0
    frame_path: Optional[str] = None


@dataclass
class Segment:
    """一页内的一个讲解片段。"""
    kind: str            # "intro"(开场，无要点高亮) 或 "bullet"(对应某个要点)
    script: str          # 口播稿（用于 TTS 与字幕）
    bullet_index: int = -1   # 当 kind == "bullet" 时，指向 slide.bullets 的下标

    # 运行期填充
    audio_path: Optional[str] = None
    duration: float = 0.0
    frame_path: Optional[str] = None
    captions: List[Caption] = field(default_factory=list)


@dataclass
class Slide:
    """一页 PPT。"""
    title: str
    bullets: List[str] = field(default_factory=list)
    layout: str = "bullets"  # bullets / table
    table_headers: List[str] = field(default_factory=list)
    table_rows: List[List[str]] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    index: int = 0
    kind: str = "content"  # cover / content / summary

    # 运行期填充
    base_image: Optional[str] = None


@dataclass
class Course:
    """一整门课程。"""
    title: str
    subtitle: str = ""
    slides: List[Slide] = field(default_factory=list)

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def from_dict(d: dict) -> "Course":
        slides = []
        for i, s in enumerate(d.get("slides", [])):
            segs = []
            for seg in s.get("segments", []):
                if isinstance(seg, dict):
                    caps = [Caption(**c) if isinstance(c, dict) else c for c in seg.get("captions", [])]
                    seg = {**seg, "captions": caps}
                    segs.append(Segment(**seg))
                else:
                    segs.append(seg)
            slides.append(
                Slide(
                    title=s["title"],
                    bullets=list(s.get("bullets", [])),
                    layout=s.get("layout", "table" if s.get("table_rows") else "bullets"),
                    table_headers=list(s.get("table_headers", [])),
                    table_rows=[list(row) for row in s.get("table_rows", [])],
                    segments=segs,
                    index=s.get("index", i),
                    kind=s.get("kind", "content"),
                    base_image=s.get("base_image"),
                )
            )
        return Course(title=d["title"], subtitle=d.get("subtitle", ""), slides=slides)

    @staticmethod
    def load_json(path: str | Path) -> "Course":
        return Course.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
