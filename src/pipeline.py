"""端到端流水线编排。

文案 -> 结构化 -> 口播稿改写 -> 逐片段 TTS -> 逐页渲染 -> 视频合成 -> MP4
每个阶段的中间产物都会落盘到 run_dir，便于调试与断点排查。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from . import llm, tts, slides, video, pptx_export
from .config import settings
from .models import Course

ProgressCb = Optional[Callable[[float, str], None]]


def _noop(_p: float, _m: str) -> None:
    pass


def run(
    input_text: str,
    run_dir: str | Path,
    progress_cb: ProgressCb = None,
    subtitle: bool = True,
    theme: str | None = None,
    voice: str | None = None,
) -> dict:
    cb = progress_cb or _noop
    if theme:
        settings.theme_name = theme  # 按本次请求切换视觉主题
    if voice:
        settings.minimax_voice = voice  # 按本次请求切换配音音色
    run_dir = Path(run_dir)
    (run_dir / "slides").mkdir(parents=True, exist_ok=True)
    (run_dir / "audio").mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    # 保存实际送入课程拆解的标准化文本，便于定位 PDF/Word 解析质量。
    (run_dir / "00_input.txt").write_text(input_text, encoding="utf-8")

    # 1) 结构化
    cb(0.05, "LLM 拆解课程结构…")
    course = llm.structure_course(input_text)
    llm_used = llm.LAST_STATUS["llm"]
    warning = llm.LAST_STATUS["note"]
    (run_dir / "00_status.json").write_text(
        json.dumps(
            {
                "llm_used": llm_used,
                "llm_note": warning,
                "theme": settings.theme_name,
                "voice": settings.minimax_voice,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if warning:
        cb(0.06, "⚠ " + warning)
    course.to_json(run_dir / "01_structure.json")

    # 2) 口播稿改写
    cb(0.18, "改写自然口播稿…")
    course = llm.polish_narration(course)
    course.to_json(run_dir / "02_scripts.json")

    # 2.5) 导出可编辑 PPT（含口播稿作为演讲者备注）
    cb(0.22, "导出 PPT 文件…")
    pptx_path = run_dir / "course.pptx"
    try:
        pptx_export.export_pptx(course, pptx_path)
    except Exception as e:  # 导出失败不应中断主流程
        print(f"[pipeline] PPT 导出失败: {e}")
        pptx_path = None

    segments = [seg for s in course.slides for seg in s.segments]
    n_seg = max(1, len(segments))

    # 3) 逐片段 TTS
    for i, seg in enumerate(segments):
        cb(0.20 + 0.45 * (i / n_seg), f"配音 {i + 1}/{n_seg}（{settings.tts_provider}）…")
        audio_path, dur = tts.synthesize(seg.script, run_dir / "audio" / f"seg_{i:03d}")
        seg.audio_path = audio_path
        seg.duration = dur
    course.to_json(run_dir / "03_with_audio.json")

    # 4) 逐页渲染（含逐要点高亮帧 + 字幕）
    cb(0.68, "渲染 PPT 页面与高亮帧…")
    for s in course.slides:
        slides.render_slide_frames(s, run_dir / "slides", subtitle=subtitle)
    course.to_json(run_dir / "04_with_frames.json")

    # 5) 合成视频
    cb(0.80, "合成视频…")
    out_mp4 = run_dir / "output.mp4"
    video.compose_course(course, out_mp4, progress_cb=lambda m: cb(0.85, m))

    total_dur = sum(seg.duration for seg in segments)
    cb(1.0, "完成")
    return {
        "video": str(out_mp4),
        "pptx": str(pptx_path) if pptx_path else None,
        "llm_used": llm_used,
        "warning": warning,
        "course_title": course.title,
        "slides": len(course.slides),
        "segments": len(segments),
        "video_seconds": round(total_dur, 1),
        "elapsed_seconds": round(time.time() - t0, 1),
        "run_dir": str(run_dir),
    }
