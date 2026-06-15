"""命令行入口。

用法：
    python main.py --input data/sample_input.txt
    python main.py --input data/sample_input.txt --out runs/demo --no-subtitle
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以正常输出中文与符号
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from src import pipeline
from src.config import settings, THEMES
from src.documents import DocumentParseError, parse_document_path, supported_file_hint


def main():
    parser = argparse.ArgumentParser(description="AI 自动化课程视频生成系统")
    parser.add_argument(
        "--input", "-i", default="data/sample_input.txt",
        help=f"上课文档路径（支持 {supported_file_hint()}）",
    )
    parser.add_argument("--out", "-o", default=None, help="输出目录（默认 runs/时间戳）")
    parser.add_argument("--no-subtitle", action="store_true", help="不叠加字幕")
    parser.add_argument("--theme", choices=list(THEMES.keys()), default=None, help="视觉主题")
    parser.add_argument("--voice", default=None, help="MiniMax 音色 voice_id（见 config.MINIMAX_VOICES）")
    args = parser.parse_args()

    try:
        text = parse_document_path(args.input)
    except DocumentParseError as exc:
        parser.error(str(exc))
    run_dir = Path(args.out) if args.out else Path("runs") / datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print(f"输入文案 : {args.input}")
    print(f"输出目录 : {run_dir}")
    print(f"LLM      : {'OpenAI:' + settings.openai_model if settings.llm_available() else '规则兜底(无 Key)'}")
    print(f"TTS      : {settings.tts_provider}")
    print("=" * 60)

    def progress(p: float, msg: str):
        bar = "█" * int(p * 30) + "─" * (30 - int(p * 30))
        sys.stdout.write(f"\r[{bar}] {p * 100:5.1f}%  {msg:<32}")
        sys.stdout.flush()
        if p >= 1.0:
            sys.stdout.write("\n")

    result = pipeline.run(text, run_dir, progress_cb=progress, subtitle=not args.no_subtitle, theme=args.theme, voice=args.voice)

    print("-" * 60)
    for k, v in result.items():
        print(f"{k:>16}: {v}")
    print("=" * 60)
    print(f"✅ 视频已生成: {result['video']}")


if __name__ == "__main__":
    main()
