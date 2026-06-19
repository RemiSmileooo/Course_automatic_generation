"""端到端流水线编排。

文案 -> 结构化 -> 口播稿改写 -> 逐片段 TTS -> 逐页渲染 -> 视频合成 -> MP4
每个阶段的中间产物都会落盘到 run_dir，便于调试与断点排查。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from . import llm, llm_slide, tts, slides, video, pptx_export
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

    # 1) 结构化 / 端到端生成
    cb(0.05, "LLM 设计课程页面…")
    use_llm_design = settings.slide_renderer == "llm"
    course = None
    design_note = ""
    if use_llm_design:
        course = llm_slide.generate_course_html(input_text)
        if course is None:
            design_note = llm_slide.LAST_STATUS.get("note") or "端到端生成不可用，已回退规则拆课"
    if course is None:
        # 回退：现有拆课（LLM 结构化 或 规则兜底）
        course = llm.structure_course(input_text)
        llm_used = llm.LAST_STATUS["llm"]
        warning = llm.LAST_STATUS["note"]
        if design_note:
            warning = f"{design_note}；{warning}" if warning else design_note
        course = llm.polish_narration(course)
    else:
        llm_used = True
        warning = ""

    (run_dir / "00_status.json").write_text(
        json.dumps(
            {
                "llm_used": llm_used,
                "llm_note": warning,
                "design_mode": "llm_end_to_end" if (use_llm_design and not design_note) else "structured",
                "theme": settings.theme_name,
                "voice": settings.minimax_voice,
                "slide_renderer": settings.slide_renderer,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if warning:
        cb(0.06, "⚠ " + warning)
    course.to_json(run_dir / "01_structure.json")

    # 2) 口播稿快照（端到端模式口播稿已由 LLM 生成，无需再改写）
    course.to_json(run_dir / "02_scripts.json")

    # 3~5) 生产：TTS → 渲染 → PPT → 合成
    result = run_production(course, run_dir, progress_cb=cb, subtitle=subtitle, t0=t0)
    result["llm_used"] = llm_used
    result["warning"] = warning
    return result


def run_production(
    course: Course,
    run_dir: str | Path,
    progress_cb: ProgressCb = None,
    subtitle: bool = True,
    t0: float | None = None,
) -> dict:
    """生产段：把已确定的 Course（含口播稿、可含 content_html）配音、渲染、合成为 MP4 + PPTX。

    供 pipeline.run（一条龙）与 design_session.produce（确认设计后）共用。
    """
    cb = progress_cb or _noop
    run_dir = Path(run_dir)
    (run_dir / "slides").mkdir(parents=True, exist_ok=True)
    (run_dir / "audio").mkdir(parents=True, exist_ok=True)
    if t0 is None:
        t0 = time.time()

    segments = [seg for s in course.slides for seg in s.segments]
    n_seg = max(1, len(segments))

    # 3) 逐片段 TTS
    for i, seg in enumerate(segments):
        cb(0.20 + 0.45 * (i / n_seg), f"配音 {i + 1}/{n_seg}（{settings.tts_provider}）…")
        audio_path, dur = tts.synthesize(seg.script, run_dir / "audio" / f"seg_{i:03d}")
        seg.audio_path = audio_path
        seg.duration = dur
    course.to_json(run_dir / "03_with_audio.json")

    # 4) 逐页渲染（含逐句字幕帧）。渲染器：llm / html / pillow，异常逐级回退。
    cb(0.68, "渲染 PPT 页面与字幕帧…")
    render_fn, render_mode = _select_renderer()

    for s in course.slides:
        try:
            render_fn(s, run_dir / "slides", subtitle=subtitle)
        except Exception as e:  # noqa: BLE001
            if render_mode == "pillow":
                raise
            print(f"[pipeline] {render_mode} 渲染失败，回退 Pillow: {e}")
            render_mode = "pillow"
            render_fn = slides.render_slide_frames
            render_fn(s, run_dir / "slides", subtitle=subtitle)
    course.to_json(run_dir / "04_with_frames.json")

    # 4.5) 导出 PPT。渲染后用每页底图导出，保证 PPT 与视频画面一致；无底图则回退可编辑版。
    cb(0.78, "导出 PPT 文件…")
    pptx_path = run_dir / "course.pptx"
    try:
        if any(getattr(s, "base_image", None) for s in course.slides):
            pptx_export.export_image_pptx(course, pptx_path)
        else:
            pptx_export.export_pptx(course, pptx_path)
    except Exception as e:  # 导出失败不应中断主流程
        print(f"[pipeline] PPT 导出失败: {e}")
        pptx_path = None

    # 5) 合成视频
    cb(0.80, "合成视频…")
    out_mp4 = run_dir / "output.mp4"
    video.compose_course(course, out_mp4, progress_cb=lambda m: cb(0.85, m))

    total_dur = sum(seg.duration for seg in segments)
    cb(1.0, "完成")
    return {
        "video": str(out_mp4),
        "pptx": str(pptx_path) if pptx_path else None,
        "llm_used": None,
        "warning": "",
        "course_title": course.title,
        "slides": len(course.slides),
        "segments": len(segments),
        "video_seconds": round(total_dur, 1),
        "elapsed_seconds": round(time.time() - t0, 1),
        "run_dir": str(run_dir),
    }


def _select_renderer():
    """按 settings.slide_renderer 选择渲染函数，返回 (render_fn, mode)。
    llm → llm_html_render；html → html_render；pillow/其它 → slides(Pillow)。
    导入失败时回退 Pillow。"""
    mode = settings.slide_renderer
    if mode == "llm":
        try:
            from . import llm_html_render
            return llm_html_render.render_slide_frames, "llm"
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] LLM-HTML 渲染器不可用，回退 Pillow: {e}")
            return slides.render_slide_frames, "pillow"
    if mode == "html":
        try:
            from . import html_render
            return html_render.render_slide_frames, "html"
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] HTML 渲染器不可用，回退 Pillow: {e}")
            return slides.render_slide_frames, "pillow"
    return slides.render_slide_frames, "pillow"
