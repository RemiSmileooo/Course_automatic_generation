# 设计文档：LLM 自主生成幻灯片 HTML（架构 Y）

## 概述

把"页面设计 + 口播稿"的决定权端到端交给 LLM：输入一段原始文案 + 设计系统规范，LLM 一次性产出一份「课程方案」——包含若干页，每页带【内容层 HTML 设计 + 该页口播稿】。代码负责解析、字幕切分对齐、TTS、渲染截图、视频合成，以及全程的校验与兜底。

设计遵循 requirements.md 的关键决策：
- **D-1**：LLM 直接返回 HTML（纯文本）。
- **D-2**：仅轻量语法/尺寸校验。
- **D-3**：启用缓存。
- **D-4**：复用 OpenAI 兼容接口，HTML 纯文本返回。
- **D-5**：架构 Y（端到端）。
- 字幕层永远由代码控制并对齐 TTS（需求 3，红线）。

设计系统以 `design_preview.html`（浅色暖橙）为基础。

### 目标
1. LLM 端到端决定分页、每页 HTML 设计、每页口播稿。
2. 设计系统约束视觉统一；布局放开以求多样、不雷同。
3. 字幕由代码逐句切分、与 TTS 对齐、叠加到 LLM 设计的页面上。
4. 任何环节异常都能逐页/整体兜底，永远出片。
5. TTS、视频合成沿用现有契约，不改。

### 非目标
- 不改视频编码、不改 MoviePy 合成逻辑。
- 不让 LLM 介入字幕切分与计时。
- 不追求像素级可编辑 PPTX（PPTX 用底图导出即可）。

## 架构

### 端到端流程

```text
原始文案
   │  generate_course_html()  ← 新增，单次 LLM 调用（端到端）
   ▼
课程方案 JSON：{ title, slides:[ {kind, narration, html} , ... ] }
   │  解析 → Course / Slide / Segment（每页一个 intro segment，script=narration）
   ▼
逐片段 TTS（现有 tts.synthesize，不变）
   ▼
逐页渲染（新增 llm_html_render）：
   每页:
     ├─ 取 LLM 生成的内容层 HTML  →  校验
     │      校验失败 → 兜底固定模板 HTML
     ├─ 注入设计系统 CSS + CJK 字体 + 字幕安全区  → 截干净底图(base_image) + 落盘 .html
     └─ 对每条 Caption(build_captions 切分): 代码叠加字幕条 → 截一帧
   ▼
视频合成（现有 video.compose_course，不变）
```

### 与现有流水线的接缝

`pipeline.run()` 改动点：
- **结构化阶段**：原 `llm.structure_course` 替换/分流为「架构 Y 端到端生成」。若 LLM 可用 → 调 `llm_slide.generate_course_html`；否则 → 现有 `_rule_based_structure` 兜底。
- **渲染阶段**：用新的 `llm_html_render.render_slide_frames`（与 `slides.render_slide_frames` 同签名），失败回退 `slides`（Pillow）。
- **TTS / 合成阶段**：完全不动。

## 组件与接口

### 1. `src/llm_slide.py`（新增）— 端到端课程生成

```python
def generate_course_html(text: str) -> Course | None:
    """单次 LLM 调用：文案 → 课程方案（每页 html + narration）。
    失败/无 Key/解析失败 → 返回 None，由上层走规则兜底。"""
```

**LLM 输出契约**（要求模型返回 JSON，便于解析；但每页的 `html` 字段是纯 HTML 文本——符合 D-4「HTML 以纯文本承载」，只是装在 JSON 数组里便于一次拿到多页 + 口播稿）：

```json
{
  "title": "课程标题",
  "slides": [
    {
      "kind": "cover|content|summary",
      "narration": "这一页的口播稿（自然口语，讲的就是本页展示内容）",
      "html": "<section class=\"slide ...\"> ... 内容层 ... </section>"
    }
  ]
}
```

> 说明：D-1/D-4 的"纯文本 HTML"指 **不让模型把 HTML 再转义进结构化布局字段**，而是整段 HTML 原样作为一个字符串。用一个外层 JSON 数组承载"多页 + 每页口播稿"是必要的，否则无法在一次调用里同时拿到分页和口播。代码对每页 `html` 做围栏清洗（去除 ```html ``` 标记）。

**Prompt 设计要点**（system + user）：
- 角色：资深课程设计师 + 信息可视化设计师 + 前端。
- 给出**设计系统规范**：画布固定 `VIDEO_WIDTH×VIDEO_HEIGHT`；可用的 CSS 变量(配色 token)与基础类；字体用 `var(--font)`；**底部预留字幕安全区（约 140px）不得放内容**；不得自带 `<script>`、外链、`position:fixed` 顶满底部。
- 自主性引导：鼓励每页用不同布局手法（分栏/网格/时间线/大字/对照/卡片流…），相邻页不重复。
- 约束：分页 6~12 页（随文长）；首页 cover、末页 summary；`html` 只含内容层，**不得包含字幕**；内容忠实文案，不臆造。
- 输出：只输出 JSON。

**缓存（D-3）**：以 `hash(text + 设计系统版本 + model)` 为 key，把成功的课程方案 JSON 落盘到 `runs/.cache/` 或内存；命中则跳过 LLM。

### 2. `src/slide_design.py`（新增）— 设计系统

集中维护设计系统，供「prompt 规范」「整页组装 CSS」「兜底模板」三处共用同一套视觉语言：

```python
DESIGN_SYSTEM_VERSION = "1"
CANVAS = (settings.width, settings.height)
CAPTION_SAFE_ZONE = 140  # px，底部预留

def base_css() -> str: ...        # 浅色暖橙设计系统的 CSS 变量 + 基础类(.slide/.card/.badge/...)
def design_spec_for_prompt() -> str: ...  # 给 LLM 的"可用变量/类 + 硬约束"速查文本
def fallback_html(slide) -> str: ...      # 兜底固定模板（基于 design_preview.html 的稳定版式）
def caption_css(is_dark: bool=False) -> str: ...  # 字幕条样式（代码控制）
```

CSS 变量取自 `design_preview.html`：`--accent:#ff8a00`、暖灰渐变背景、卡片圆角阴影等。

### 3. `src/llm_html_render.py`（新增）— 渲染器（与现有同签名）

```python
def render_slide_frames(slide: Slide, out_dir, subtitle: bool = True) -> None:
    """1) 取 slide 上 LLM 生成的内容层 html（或兜底）；校验。
       2) 组装整页 = 设计系统CSS + CJK字体 + 内容层 + (可选)字幕条。
       3) 截干净底图 → slide.base_image；落盘 slide_XX.html。
       4) 逐 Caption：叠字幕条 → 截帧 → caption.frame_path。"""
```

内容层 HTML 存哪：在 `Slide` 上加一个**可选运行期字段** `content_html: str | None`（不破坏序列化，默认 None）。`generate_course_html` 解析时写入。

**校验（D-2，轻量）**：
- 非空、长度合理；
- 含一个根 `<section`（或 `<div`）；
- 标签基本闭合（简单计数/正则，不做完整 DOM 解析）；
- 不含 `<script`、外链 `http(s)://`（防注入与渲染卡顿）；
- 失败 → `slide_design.fallback_html(slide)`。

**整页组装**：
```html
<!doctype html><html><head><meta charset utf-8>
<style> {base_css + cjk @font-face + caption_css} </style></head>
<body><main class="deck">{content_html}{caption?}</main></body></html>
```
固定尺寸、隐藏滚动条；字幕条由代码插入，定位在安全区内。

### 4. 数据模型微调（`src/models.py`）

- `Slide` 增加运行期可选字段：`content_html: Optional[str] = None`。
- 其余不变。`Segment.script` 存该页口播稿（每页一个 intro segment）。

### 5. pipeline 接线（`src/pipeline.py`）

```python
course = llm_slide.generate_course_html(input_text) if settings.llm_available() else None
if course is None:
    course = llm._rule_based_structure(input_text)   # 兜底
# 渲染器选择：llm_html_render → 失败回退 slides(Pillow)
```

记录每页渲染方式与回退原因到 `00_status.json`（需求 4.6）。

## 错误处理与兜底（需求 4）

| 故障点 | 处理 |
|---|---|
| 无 OPENAI_API_KEY | 跳过端到端，走规则拆课 + 兜底模板（需求 4.5） |
| 端到端 LLM 调用失败/超时/JSON 解析失败 | 整体回退规则拆课（需求 4.2 / D-5） |
| 单页 html 校验失败 | 该页用 `fallback_html`，其它页不受影响（需求 4.2/逐页兜底） |
| 浏览器/Playwright 不可用 | 回退 Pillow 渲染（需求 4.4） |
| LLM 重试 | 最多 1 次（需求 6.2）；仍失败则兜底 |

## 测试策略

引入/复用 `pytest`，覆盖不依赖浏览器与网络的纯逻辑：

1. **响应解析**：给定一段模拟 LLM JSON（含多页 html+narration），`generate_course_html` 能正确解析成 Course；每页 html 写入 `content_html`，narration 写入 segment.script。
2. **HTML 清洗**：去除 ```html 围栏、首尾空白、多余 markdown。
3. **校验逻辑**：合法 HTML 通过；空/无根元素/含 `<script>`/含外链 → 判失败。
4. **兜底模板**：`fallback_html(slide)` 对 cover/content/table 均产出含标题的有效 HTML。
5. **设计系统**：`base_css` 含关键 token（--accent 等）；`design_spec_for_prompt` 含字幕安全区与禁止项说明。
6. **整页组装**：产物含 CJK @font-face、固定尺寸、字幕条（subtitle 时）；内容层被正确嵌入。
7. **字幕仍由代码切分**：渲染逐 Caption 时帧数 == captions 数（用 stub HTML，必要时跳过真实截图）。

**人工抽检**：真实跑一篇文案，目视多页布局是否多样、是否统一、中文不缺字、字幕不压内容、溢出时是否兜底。

## 实施影响面

| 文件 | 改动 |
|---|---|
| `src/slide_design.py`（新增） | 设计系统：CSS、prompt 规范、兜底模板、字幕样式 |
| `src/llm_slide.py`（新增） | 端到端 LLM 课程生成 + 缓存 + 解析 + 清洗 |
| `src/llm_html_render.py`（新增） | LLM-HTML 渲染器（校验 + 组装 + 截图 + 字幕帧） |
| `src/models.py` | Slide 增加 `content_html` 可选字段 |
| `src/pipeline.py` | 结构化阶段分流到端到端；渲染阶段选用新渲染器并保留回退 |
| `src/config.py` | 可能新增开关（如 `SLIDE_RENDERER=llm`）与缓存目录 |
| `tests/`（新增） | 上述单元测试 |
| `.env.example` / `README.md` | 文档与开关说明 |

## 待设计阶段细化的小决定
- 端到端模式的开关命名（如 `SLIDE_RENDERER=llm` 或独立 `LLM_SLIDE_DESIGN=true`）。
- 缓存落盘位置与失效策略（随 `DESIGN_SYSTEM_VERSION` 失效）。
- PPTX 是否改用底图导出以保持与视频一致（建议改）。


---

# 增补设计：设计-预览-对话修改-确认 工作流（第二阶段）

## 决策（已定）
- **会话存储**：进程内存字典 + 落盘 JSON 备份（`runs/.sessions/<sid>.json`），重启可恢复。
- **预览**：前端用 `<iframe srcdoc>` 直接渲染 LLM 的 HTML（拼上设计系统 CSS），所见即所得；最终出片仍由后端 Playwright 截图。
- **旧路径保留**：`pipeline.run` 一条龙模式保留作兼容；新工作流为 Web 默认体验。

## 后端：设计会话服务 `src/design_session.py`（新增）

```python
@dataclass
class DesignSession:
    sid: str
    source_text: str
    title: str
    slides: list[dict]   # [{kind, narration, html}]
    history: list[dict]  # 对话历史 [{role, content}]

def create_session(text) -> DesignSession        # 调 llm_slide.generate_course_html → 存会话
def revise_session(sid, instruction) -> DesignSession  # 当前设计 + 指令 → LLM 改 → 更新会话
def get_session(sid) -> DesignSession | None
def produce(sid, run_dir, ...) -> dict            # 用会话设计走生产（TTS→字幕→渲染→合成）
```

- `create_session`：复用现有 `llm_slide.generate_course_html`（已含设计系统强化）。
- `revise_session`：新增"修改" prompt——输入【完整当前设计 JSON + 用户自然语言指令】，要求 LLM 返回更新后的同结构 JSON；可只改部分页。失败则保留上一版。
- `produce`：把会话的 slides 装回 `Course`（html→`content_html`，narration→intro segment），调用现有 `pipeline` 的生产段（TTS→`llm_html_render`→`video`）。为此把 `pipeline.run` 的"生产部分"抽成可复用函数 `run_production(course, run_dir, ...)`。

## LLM 设计能力强化（需求 7.4）

在 `slide_design.py`：
- **扩充 `base_css` 组件库**：新增图标卡、左右红绿对照面板、横向时间线(连接线+节点)、大字宣言块、渐变 hero 总结块、统计大数字等类，对应 design_preview.html 的丰富版式。
- **`design_spec_for_prompt` 升级**：给**多个不同版式的示例**（封面/对照/时间线/宣言/总结各一），附"内容性质→推荐版式"对照表，并强调"你是顶级设计师，大胆排版、每页有记忆点、相邻页不得雷同、避免千篇一律的卡片墙"。
- 新增"修改指令"prompt 模板（供 `revise_session`）。

## Web 接口 `app.py`（新增/改造）

| 接口 | 作用 |
|---|---|
| `POST /api/design` | 提交文案 → 创建设计会话，返回 sid + slides |
| `GET /api/design/{sid}` | 取会话当前设计 |
| `POST /api/design/{sid}/revise` | body: {instruction} → 对话修改，返回更新后的 slides |
| `POST /api/design/{sid}/produce` | 确认 → 后台线程走生产，返回 job_id（复用现有 JOBS 进度机制） |

旧 `POST /api/generate`（一条龙）保留。

## 前端三区界面（`app.py` 内嵌页面改造）

```
顶部：页签[第1页..第N页]  |  [＋对话修改]  |  [✅ 确认，生成视频]
中部：左 = HTML 源代码(等宽字体, 只读, 可滚)   右 = <iframe srcdoc> 实时渲染
底部：对话框输入 + 发送（多轮）
```
- 切页签 → 左右同步换该页代码与预览。
- 发送修改指令 → 调 revise → 刷新所有页签内容。
- 点确认 → 调 produce → 切到原有"进度条 + 视频预览 + 下载"视图（复用现有 UI）。

## 渲染预览的 CSS 注入

iframe `srcdoc` = `<style>{slide_design.base_css()}</style>` + 该页 `content_html`。
保证预览与最终截图同款设计系统。viewport 缩放：iframe 用 `transform:scale()` 适配卡片宽度（1920 → 容器宽）。

## 测试策略（增补）
1. `design_session`：create 用 stub（monkeypatch `generate_course_html`）→ 会话含 slides；revise 用 stub LLM 返回改后 JSON → 会话更新；revise 失败 → 保留旧版。
2. 生产段抽取后 `run_production` 仍能跑通（offline TTS + 现有渲染）。
3. 修改 prompt 解析：当前设计+指令拼接正确。
4. 接口层：`/api/design` 返回 sid+slides；`/api/design/{sid}/revise` 更新；produce 触发 job。

## 实施影响面（增补）
| 文件 | 改动 |
|---|---|
| `src/slide_design.py` | 扩充组件库 CSS + 多样化示例/映射/创意引导 + 修改 prompt |
| `src/design_session.py`（新增） | 设计会话：create/revise/get/produce + 落盘 |
| `src/llm_slide.py` | 新增 revise 生成函数（当前设计+指令→新设计） |
| `src/pipeline.py` | 抽出 `run_production(course, run_dir, ...)` 供会话生产复用 |
| `app.py` | 新增 4 个设计接口 + 三区前端界面；保留旧 generate |
| `tests/` | 会话/修改/生产单元测试 |
