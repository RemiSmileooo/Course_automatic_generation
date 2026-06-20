# Course Automatic Generation

这是一个基于 Python 的课程自动生成工具。输入课程文稿或教学资料后，系统可以自动完成文档解析、课程结构拆分、PPT/HTML 幻灯片设计、口播稿生成、语音合成、字幕切分、画面渲染和视频合成，最终输出课程视频与可编辑课件。

项目提供两种使用方式：

- **经典一键生成**：粘贴文案或上传 TXT、Markdown、PDF、DOCX，直接生成 MP4 和 PPTX。
- **PPT 设计工作台**：先让 LLM 设计或导入已有 HTML 课件，再在 Web 页面中预览、微调，确认后生成视频。

## 主要功能

- 支持 TXT、Markdown、PDF、DOCX 输入。
- PDF 通过 MinerU API 转成 Markdown 后进入课程生成流程。
- 支持 OpenAI-compatible LLM，用于课程结构化、口播稿生成和 HTML 幻灯片设计。
- 支持 MiniMax TTS、Edge TTS 和本地静音占位降级。
- 支持 LLM HTML 幻灯片、内置 HTML 模板和 Pillow 位图渲染三种页面渲染模式。
- 输出 MP4 视频、PPTX 课件、音频、图片帧和每个处理阶段的 JSON 快照。
- Web 端支持任务进度、视频预览、MP4/PPTX 下载和设计会话修改。

## 技术栈

- Python 3.13
- FastAPI + Uvicorn
- OpenAI Python SDK
- MiniMax Speech API / edge-tts
- MinerU PDF 解析 API
- Pillow
- python-pptx
- MoviePy + imageio-ffmpeg
- pytest

## 项目结构

```text
.
├── app.py                  # FastAPI 服务和内嵌 Web 前端
├── main.py                 # CLI 入口
├── requirements.txt        # Python 依赖
├── .env.example            # 环境变量模板
├── data/
│   └── sample_input.txt    # 示例输入
├── src/
│   ├── config.py           # 配置、主题、音色、字体探测
│   ├── documents.py        # TXT/Markdown/PDF/DOCX 文档解析
│   ├── mineru.py           # MinerU PDF 解析
│   ├── models.py           # Course/Slide/Segment/Caption 数据模型
│   ├── llm.py              # 经典模式的课程拆分和口播润色
│   ├── llm_slide.py        # LLM HTML 幻灯片设计、导入和修订
│   ├── slide_design.py     # HTML 幻灯片设计规范与兜底样式
│   ├── html_render.py      # HTML 模板渲染
│   ├── llm_html_render.py  # LLM HTML 渲染
│   ├── slides.py           # Pillow 位图渲染和字幕帧生成
│   ├── pptx_export.py      # PPTX 导出
│   ├── tts.py              # TTS 合成和降级
│   ├── video.py            # MP4 合成
│   └── pipeline.py         # 端到端流水线
└── tests/                  # 单元测试
```

## 快速开始

```powershell
git clone https://github.com/RemiSmileooo/Course_automatic_generation.git
cd Course_automatic_generation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

启动 Web 服务：

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

然后打开：

```text
http://127.0.0.1:8000
```

进入设计工作台：

```text
http://127.0.0.1:8000/design
```

## 命令行使用

使用示例文稿生成课程：

```powershell
python main.py --input data/sample_input.txt --out runs/demo
```

选择主题和音色：

```powershell
python main.py --input data/sample_input.txt --theme ocean --voice "Chinese (Mandarin)_Radio_Host"
```

关闭字幕：

```powershell
python main.py --input data/sample_input.txt --no-subtitle
```

支持参数：

| 参数 | 说明 |
|---|---|
| `--input`, `-i` | 输入文件路径，支持 txt、md、markdown、pdf、docx |
| `--out`, `-o` | 输出目录，默认写入 `runs/<时间戳>` |
| `--no-subtitle` | 不渲染字幕 |
| `--theme` | 视觉主题，可选值来自 `src/config.py` 的 `THEMES` |
| `--voice` | MiniMax voice_id |

## Web API

### 经典生成

`POST /api/generate`

使用 `multipart/form-data` 创建生成任务。

| 字段 | 说明 |
|---|---|
| `text` | 直接粘贴的课程文稿 |
| `file` | 上传 `.txt`、`.md`、`.markdown`、`.pdf`、`.docx`，最大 25 MB |
| `subtitle` | 是否生成字幕 |
| `theme` | 视觉主题 |
| `voice` | MiniMax voice_id |

相关接口：

- `GET /api/status/{job_id}`：查询任务状态。
- `GET /api/video/{job_id}`：下载或预览 MP4。
- `GET /api/pptx/{job_id}`：下载 PPTX。

### 设计工作台

`POST /api/design`

创建设计会话。可以只提交文案，让 LLM 从零设计；也可以提交文案加已有 HTML，让系统按现有设计做工程化适配。

| 字段 | 说明 |
|---|---|
| `text` | 课程文稿 |
| `html` | 已设计好的 HTML 源码，可选 |
| `file` | 文案文件，可选 |
| `html_file` | `.html` 或 `.htm` 文件，可选 |

相关接口：

- `GET /api/design/{sid}`：读取设计会话。
- `POST /api/design/{sid}/revise`：用自然语言修订当前页或整套幻灯片。
- `POST /api/design/{sid}/produce`：确认设计并生成视频。
- `GET /api/design-css`：获取设计系统 CSS。

## 环境变量

复制 `.env.example` 为 `.env` 后填写本地配置。真实密钥只应写入 `.env`，不要提交到仓库。

### LLM

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | OpenAI-compatible API Key |
| `OPENAI_BASE_URL` | API 地址，例如 OpenAI 或 DeepSeek 兼容网关 |
| `OPENAI_MODEL` | 模型名 |

LLM 未配置时，经典模式会使用规则兜底；设计工作台需要可用 LLM。

### MinerU PDF 解析

| 变量 | 说明 |
|---|---|
| `MINERU_API_KEY` | MinerU API Token |
| `MINERU_BASE_URL` | MinerU API 根地址 |
| `MINERU_MODEL` | 解析模型，默认 `vlm` |
| `MINERU_LANGUAGE` | 文档语言 |
| `MINERU_OCR` | 是否启用 OCR |
| `MINERU_ENABLE_TABLE` | 是否识别表格 |
| `MINERU_ENABLE_FORMULA` | 是否识别公式 |
| `MINERU_TIMEOUT` | 整体解析超时 |

PDF 输入依赖 MinerU 在线 API；没有 Token 时无法处理 PDF。

### TTS

| 变量 | 说明 |
|---|---|
| `TTS_PROVIDER` | `minimax`、`edge` 或 `offline` |
| `MINIMAX_API_KEY` | MiniMax API Key |
| `MINIMAX_GROUP_ID` | MiniMax Group ID |
| `MINIMAX_API_HOST` | MiniMax API 地址 |
| `MINIMAX_MODEL` | 语音模型 |
| `MINIMAX_VOICE` | 默认 voice_id |
| `MINIMAX_SPEED` | 语速 |
| `EDGE_VOICE` | Edge TTS 音色 |

TTS 降级顺序通常为：MiniMax -> Edge TTS -> 本地静音 WAV。

### 视频和渲染

| 变量 | 说明 |
|---|---|
| `THEME` | 默认视觉主题 |
| `VIDEO_WIDTH` | 视频宽度 |
| `VIDEO_HEIGHT` | 视频高度 |
| `VIDEO_FPS` | 视频帧率 |
| `CJK_FONT_PATH` | 自定义中文字体路径 |
| `SLIDE_RENDERER` | `llm`、`html` 或 `pillow` |

`SLIDE_RENDERER=llm` 会让 LLM 直接设计 HTML 幻灯片；浏览器渲染不可用时会降级到 Pillow。

## 输出目录

每次生成会写入一个 `runs/<run_id>/` 目录：

```text
runs/<run_id>/
├── 00_input.txt
├── 00_status.json
├── 01_structure.json
├── 02_scripts.json
├── 03_with_audio.json
├── 04_with_frames.json
├── course.pptx
├── output.mp4
├── audio/
└── slides/
```

这些文件用于排查每个阶段的问题，也方便复用中间产物。

## 测试

```powershell
python -m pytest
```

如果本机默认临时目录权限异常，可以指定项目内临时目录：

```powershell
python -m pytest -p no:cacheprovider --basetemp=tmp_pytest
```

## Git 上传注意事项

仓库已经忽略以下本地文件和生成产物：

```text
.env
.venv/
.pytest_cache/
tmp_pytest/
runs/
*.mp4
*.mp3
*.wav
*.pptx
*.pdf
```

提交前建议检查：

```powershell
git status --short
git diff --check
```

推送：

```powershell
git push origin main
```
