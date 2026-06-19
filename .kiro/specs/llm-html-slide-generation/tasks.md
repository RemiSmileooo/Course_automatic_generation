# Implementation Plan: LLM 自主生成幻灯片 HTML（架构 Y）

## Overview

按 requirements.md / design.md 实现「LLM 端到端生成每页 HTML + 口播稿」。改动集中在新增三个模块（设计系统 / 端到端生成 / LLM-HTML 渲染器）与 pipeline 接线、PPTX 底图导出，字幕切分、TTS、视频合成沿用现有契约不动。已敲定：开关用 `SLIDE_RENDERER=llm`；PPTX 改用底图导出，与视频画面一致。

## Task Dependency Graph

```text
1 (pytest 脚手架)
│
2 (设计系统 slide_design：CSS / prompt 规范 / 兜底模板 / 字幕样式)
│
3 (端到端生成 llm_slide：调用 / 清洗 / 解析 / 缓存) ── 4 (Slide.content_html 字段)
│                                                        │
5 (渲染器 llm_html_render：校验 + 组装 + 截图 + 字幕帧)
│
6 (config 开关 SLIDE_RENDERER=llm + 缓存目录)
│
7 (pipeline 接线：端到端分流 + 渲染器选择 + 回退 + 状态记录)
│
8 (PPTX 改用底图导出)
│
9 (.env.example + README 文档)
│
10 (端到端冒烟 + 人工抽检)
```

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2", "4"] },
    { "wave": 3, "tasks": ["3"] },
    { "wave": 4, "tasks": ["5"] },
    { "wave": 5, "tasks": ["6"] },
    { "wave": 6, "tasks": ["7"] },
    { "wave": 7, "tasks": ["8"] },
    { "wave": 8, "tasks": ["9"] },
    { "wave": 9, "tasks": ["10"] },
    { "wave": 10, "tasks": ["11", "12", "13"] },
    { "wave": 11, "tasks": ["14"] },
    { "wave": 12, "tasks": ["15"] },
    { "wave": 13, "tasks": ["16"] },
    { "wave": 14, "tasks": ["17"] },
    { "wave": 15, "tasks": ["18"] }
  ]
}
```

## Tasks

- [x] 1. 搭建/确认 pytest 测试脚手架
  - 确认 `requirements.txt` 含 `pytest`；`tests/` 目录与 `conftest.py` 就绪（项目根在 sys.path）
  - 占位冒烟测试：导入 `src.config` / `src.models` 成功
  - _需求: 测试策略前置_

- [x] 2. 实现设计系统 `src/slide_design.py`
  - `base_css()`：浅色暖橙设计系统的 CSS 变量(--accent 等) + 基础类(.slide/.card/.badge/.kicker/...)，取自 design_preview.html
  - `caption_css(is_dark=False)`：字幕条样式（代码控制，定位在底部安全区）
  - `fallback_html(slide)`：稳定兜底模板（cover / content(bullets) / table 三种），产出含标题的有效 HTML
  - `design_spec_for_prompt()`：给 LLM 的"可用变量/类 + 硬约束(画布尺寸、字幕安全区≈140px、禁止 script/外链/字幕)"速查文本
  - `DESIGN_SYSTEM_VERSION`、`CAPTION_SAFE_ZONE` 常量
  - _需求: 2.1, 2.2, 2.3, 2.6, 3.4, 4.2_
  - 单元测试：base_css 含 --accent；design_spec 含字幕安全区与禁止项；fallback_html 三类均含标题

- [x] 4. 给 `Slide` 增加运行期字段 `content_html`
  - `src/models.py`：`Slide.content_html: Optional[str] = None`
  - 确认 `Course.to_json` / `from_dict` 兼容新字段（序列化不报错、缺省可读）
  - _需求: 5.1, 5.2_
  - 单元测试：含/不含 content_html 的 Course 序列化与回读一致

- [x] 3. 实现端到端生成 `src/llm_slide.py`
  - `generate_course_html(text) -> Course | None`：单次 LLM 调用（复用 OpenAI 兼容客户端），system+user 注入 `design_spec_for_prompt()`，要求返回 JSON：{title, slides:[{kind, narration, html}]}
  - prompt 约束：分页 6~12、首页 cover/末页 summary、html 只含内容层不含字幕、内容忠实文案、鼓励相邻页不同布局
  - 响应处理：解析 JSON → Course；每页 html 写入 `slide.content_html`，narration 写入该页 intro `Segment.script`
  - HTML 清洗：去 ```html 围栏、首尾空白
  - 缓存（D-3）：key=hash(text+DESIGN_SYSTEM_VERSION+model)，命中跳过调用；落盘 `runs/.cache/`
  - 失败/无 Key/解析失败 → 返回 None
  - 最多重试 1 次（需求 6.2）
  - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 6.1, 6.2, D-1, D-3, D-4, D-5_
  - 单元测试（不联网，喂模拟 JSON 字符串走解析/清洗分支）：多页解析正确；围栏清洗；空/坏 JSON → None

- [x] 5. 实现渲染器 `src/llm_html_render.py`
  - `render_slide_frames(slide, out_dir, subtitle=True)`：与 `slides.render_slide_frames` 同签名
  - 取 `slide.content_html`；轻量校验（非空、有根 section/div、标签基本闭合、无 script/外链）→ 失败用 `slide_design.fallback_html(slide)`
  - 整页组装：base_css + CJK @font-face + caption_css + 内容层 (+字幕)；固定视频分辨率
  - 截干净底图 → `slide.base_image`；落盘 `slides/slide_XX.html`
  - 复用 `slides.build_captions` 切句；逐 Caption 叠字幕条截帧 → `caption.frame_path`
  - Playwright 启动失败 → 抛错交上层回退 Pillow
  - _需求: 1.3, 2.4, 2.5, 3.1, 3.2, 3.3, 3.4, 4.1, 4.3, 5.1, 5.2, 5.3_
  - 单元测试：校验逻辑(合法/空/无根/含script/含外链)；整页组装含 CJK 字体+固定尺寸+字幕条

- [x] 6. config 开关与缓存目录 `src/config.py`
  - 新增/扩展 `SLIDE_RENDERER`：支持 `llm`（默认走 LLM-HTML）/ `html` / `pillow`
  - 缓存目录常量（如 `runs/.cache`）
  - _需求: 4.4, 4.5, 6.1_

- [x] 7. pipeline 接线 `src/pipeline.py`
  - 结构化阶段：`settings.llm_available()` 且开关=llm → `llm_slide.generate_course_html`；返回 None 或无 Key → `llm._rule_based_structure` 兜底
  - 渲染阶段：选 `llm_html_render.render_slide_frames`，异常逐页/整体回退 `slides`（Pillow）
  - 状态记录：`00_status.json` 写每页渲染方式与回退原因
  - TTS / 合成阶段不动
  - _需求: 3.5, 4.2, 4.4, 4.5, 4.6, D-5_

- [x] 8. PPTX 改用底图导出
  - `src/pptx_export.py`：新增/启用「用 `slide.base_image` 整页图导出」路径；pipeline 渲染后再导出（移动导出时机到渲染之后）
  - 无底图时回退现有 `export_pptx`
  - 演讲者备注仍写各页口播稿
  - _需求: 5.4_

- [x] 9. 文档与配置说明
  - `.env.example`：`SLIDE_RENDERER=llm` 说明、缓存说明
  - `README.md`：说明 LLM 端到端生成、设计系统、兜底链、字幕仍由代码对齐
  - _需求: 文档_

- [x] 10. 端到端冒烟与人工抽检
  - 用样例文案在 `SLIDE_RENDERER=llm` 下生成（如无 Key 则验证兜底路径出片）
  - 人工检查：多页布局多样且统一、中文不缺字、字幕不压内容、溢出时兜底、PPTX 与视频一致
  - 清理临时产物
  - _需求: 1.x, 2.x, 3.x, 4.x（集成验证）_

## Tasks（第二阶段：设计-预览-对话修改-确认 工作流）

- [ ] 11. 强化 LLM 设计能力（slide_design 组件库 + 示例 + 引导）
  - `base_css` 扩充组件类：图标卡、左右红绿对照面板、横向时间线(连接线+节点)、大字宣言块、渐变 hero 总结块、统计大数字
  - `design_spec_for_prompt` 升级：多个不同版式示例（封面/对照/时间线/宣言/总结）+ "内容性质→推荐版式"对照 + "顶级设计师/相邻页不雷同/避免卡片墙"创意引导（全部 px）
  - _需求: 7.4, 2.x_
  - 单元测试：base_css 含新组件类；design_spec 含多个示例与映射关键词

- [ ] 12. pipeline 抽出可复用生产段 `run_production`
  - 从 `pipeline.run` 抽出"生产部分"（TTS → 渲染截图 → PPTX → 合成）为 `run_production(course, run_dir, progress_cb, subtitle, ...)`
  - `pipeline.run` 改为：结构化/设计 → 调 `run_production`，保持现有一条龙行为不变
  - _需求: 10.1, 10.2, 10.3, 10.4_
  - 单元测试：run_production 用 offline TTS 跑通一个最小 Course（可跳过真实截图或用 pillow）

- [ ] 13. llm_slide 新增"修改设计"生成
  - `revise_course_html(course_payload, instruction) -> dict | None`：输入当前设计 JSON + 自然语言指令，LLM 返回更新后的同结构 JSON；支持单页/整套；清洗/解析复用
  - 失败返回 None（上层保留旧版）
  - _需求: 9.2, 9.3, 9.5_
  - 单元测试（不联网，stub）：解析改后 JSON；坏结果 → None

- [ ] 14. 设计会话服务 `src/design_session.py`
  - `DesignSession` 数据结构 + create / get / revise / produce
  - create：调 `llm_slide.generate_course_html`；revise：调 `llm_slide.revise_course_html`，失败保留旧版；produce：装回 Course → `pipeline.run_production`
  - 会话存储：进程内存 + 落盘 `runs/.sessions/<sid>.json`
  - _需求: 7.1, 7.2, 9.1, 9.4, 9.5, 10.1, 10.2_
  - 单元测试：create/revise（stub LLM）；revise 失败保留旧版；落盘往返

- [ ] 15. Web 后端接口 `app.py`
  - `POST /api/design`（文案→sid+slides）、`GET /api/design/{sid}`、`POST /api/design/{sid}/revise`（{instruction}→更新）、`POST /api/design/{sid}/produce`（→job_id，复用 JOBS 进度）
  - 保留旧 `POST /api/generate`
  - _需求: 7.1, 8.5, 9.1, 10.1_

- [ ] 16. Web 前端三区界面 `app.py`
  - 顶部：页签 + 确认按钮；中部：左 HTML 源代码(只读等宽可滚) + 右 iframe srcdoc 实时渲染；底部：对话框
  - 切页签同步左右；发送修改→revise→刷新；确认→produce→切到现有进度/预览/下载视图
  - iframe srcdoc 注入 base_css，transform:scale 适配宽度
  - _需求: 8.1, 8.2, 8.3, 8.4, 8.5, 9.1_

- [ ] 17. 文档更新（第二阶段）
  - README/.env：说明设计-预览-修改-确认工作流与接口
  - _需求: 文档_

- [ ] 18. 端到端冒烟（第二阶段）
  - 设计→预览→对话改一次→确认生产，验证全链路；无 Key 则验证兜底
  - 人工抽检前端三区交互与最终视频
  - 清理临时产物
  - _需求: 7.x, 8.x, 9.x, 10.x（集成验证）_

## Notes

- 字幕红线：无论口播稿是否来自 LLM，字幕切分/计时/叠加始终由代码完成。
- 三层兜底必须始终可用：无 Key→规则拆课；端到端失败→整体回退；单页校验失败→该页兜底模板；浏览器不可用→Pillow。
- 任务 3 依赖任务 2（要用 design_spec）；任务 5 依赖 2/3/4；任务 7 依赖 5/6。
- 任务 10 为人工抽检，需目视确认观感，无法完全自动断言。
- 无 LLM API Key 时，端到端路径无法真实验证 LLM 产物质量，只能验证兜底链；这点在冒烟阶段如实说明。
