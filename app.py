"""FastAPI 前端：上传文案 -> 后台生成 -> 进度轮询 -> 下载视频。

启动：
    uvicorn app:app --reload --port 8000
然后浏览器打开 http://localhost:8000
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse

from src import pipeline, design_session
from src.config import settings, MINIMAX_VOICES, MINIMAX_VOICE_IDS, THEMES
from src.documents import DocumentParseError, parse_document, supported_file_hint

app = FastAPI(title="AI 课程视频生成系统")

RUNS = Path("runs")
RUNS.mkdir(exist_ok=True)

# job_id -> {progress, message, status, result/error}
JOBS: dict[str, dict] = {}


def _recover_completed_job(job_id: str, job: dict | None = None) -> dict | None:
    """编码完成但清理临时文件报错时，从磁盘恢复任务结果。"""
    run_dir = RUNS / job_id
    video_path = run_dir / "output.mp4"
    frames_path = run_dir / "04_with_frames.json"
    if not video_path.is_file() or video_path.stat().st_size == 0 or not frames_path.is_file():
        return None

    try:
        data = json.loads(frames_path.read_text(encoding="utf-8"))
        slides = data.get("slides", [])
        segments = [seg for slide in slides for seg in slide.get("segments", [])]
        result = {
            "video": str(video_path),
            "pptx": str(run_dir / "course.pptx") if (run_dir / "course.pptx").is_file() else None,
            "llm_used": None,
            "warning": "视频已完成，已从编码后的磁盘产物恢复任务状态",
            "course_title": data.get("title", "课程视频"),
            "slides": len(slides),
            "segments": len(segments),
            "video_seconds": round(sum(float(seg.get("duration", 0)) for seg in segments), 1),
            "elapsed_seconds": None,
            "run_dir": str(run_dir),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None

    recovered = job or {}
    recovered.update(
        status="done",
        progress=1.0,
        message="完成（已从磁盘恢复）",
        result=result,
    )
    recovered.pop("error", None)
    JOBS[job_id] = recovered
    return recovered


def _worker(
    job_id: str,
    text: str,
    subtitle: bool,
    theme: str,
    voice: str,
    upload_name: str | None = None,
    upload_data: bytes | None = None,
):
    job = JOBS[job_id]
    run_dir = RUNS / job_id

    def cb(p: float, m: str):
        job["progress"] = round(p, 3)
        job["message"] = m

    try:
        job["status"] = "running"
        content = text
        progress_offset = 0.0
        if upload_name is not None and upload_data is not None:
            content = parse_document(upload_name, upload_data, progress_cb=cb)
            progress_offset = 0.16 if Path(upload_name).suffix.lower() == ".pdf" else 0.02
        result = pipeline.run(
            content,
            run_dir,
            progress_cb=lambda p, m: cb(progress_offset + (1.0 - progress_offset) * p, m),
            subtitle=subtitle,
            theme=theme,
            voice=voice,
        )
        job["status"] = "done"
        job["result"] = result
        job["progress"] = 1.0
        job["message"] = "完成"
    except Exception as e:  # noqa: BLE001
        if not _recover_completed_job(job_id, job):
            job["status"] = "error"
            job["error"] = str(e)
            job["message"] = f"出错: {e}"


@app.post("/api/generate")
async def generate(
    file: UploadFile | None = File(default=None),
    text: str = Form(default=""),
    subtitle: bool = Form(default=True),
    theme: str = Form(default="apple"),
    voice: str = Form(default=""),
):
    content = text.strip()
    upload_name = None
    upload_data = None
    if file is not None:
        upload_name = file.filename or ""
        upload_data = await file.read()
        suffix = Path(upload_name).suffix.lower()
        if suffix not in {".txt", ".md", ".markdown", ".pdf", ".docx"}:
            raise HTTPException(400, f"不支持 {suffix or '无扩展名'} 文件；当前支持 {supported_file_hint()}")
        if not upload_data:
            raise HTTPException(400, "上传文件为空")
        if len(upload_data) > 25 * 1024 * 1024:
            raise HTTPException(400, "文件超过 25 MB 上传限制")
    if not content and upload_data is None:
        raise HTTPException(400, f"请上传文案文件（{supported_file_hint()}）或粘贴文案内容")

    theme = theme if theme in THEMES else "apple"
    voice = voice if voice in MINIMAX_VOICE_IDS else settings.minimax_voice
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    JOBS[job_id] = {"progress": 0.0, "message": "排队中…", "status": "queued"}
    threading.Thread(
        target=_worker,
        args=(job_id, content, subtitle, theme, voice, upload_name, upload_data),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") == "error":
        job = _recover_completed_job(job_id, job)
    if not job:
        raise HTTPException(404, "任务不存在")
    return JSONResponse(job)


@app.get("/api/video/{job_id}")
async def video(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") == "error":
        job = _recover_completed_job(job_id, job)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "视频尚未就绪")
    path = Path(job["result"]["video"])
    if not path.exists():
        raise HTTPException(404, "视频文件丢失")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/api/pptx/{job_id}")
async def pptx(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") == "error":
        job = _recover_completed_job(job_id, job)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "PPT 尚未就绪")
    p = job["result"].get("pptx")
    if not p or not Path(p).exists():
        raise HTTPException(404, "PPT 文件不存在")
    return FileResponse(
        p,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{job_id}.pptx",
    )


# --------------------------------------------------------------------------- #
# 设计-预览-对话修改-确认 工作流
# --------------------------------------------------------------------------- #
def _design_produce_worker(job_id: str, sid: str, subtitle: bool, voice: str):
    job = JOBS[job_id]
    run_dir = RUNS / job_id

    def cb(p: float, m: str):
        job["progress"] = round(p, 3)
        job["message"] = m

    try:
        job["status"] = "running"
        result = design_session.produce(sid, run_dir, progress_cb=cb, subtitle=subtitle, voice=voice)
        if result is None:
            raise RuntimeError("设计会话不存在或无法生产")
        job["status"] = "done"
        job["result"] = result
        job["progress"] = 1.0
        job["message"] = "完成"
    except Exception as e:  # noqa: BLE001
        if not _recover_completed_job(job_id, job):
            job["status"] = "error"
            job["error"] = str(e)
            job["message"] = f"出错: {e}"


@app.post("/api/design")
async def design_create(
    text: str = Form(default=""),
    html: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    html_file: UploadFile | None = File(default=None),
):
    content = text.strip()
    html_src = html.strip()

    # 文案文件（txt/md/pdf/docx）
    if file is not None:
        name = file.filename or ""
        data = await file.read()
        suffix = Path(name).suffix.lower()
        if suffix not in {".txt", ".md", ".markdown", ".pdf", ".docx"}:
            raise HTTPException(400, f"不支持 {suffix or '无扩展名'} 文件；当前支持 {supported_file_hint()}")
        if not data:
            raise HTTPException(400, "上传文案文件为空")
        if len(data) > 25 * 1024 * 1024:
            raise HTTPException(400, "文件超过 25 MB 上传限制")
        content = parse_document(name, data)

    # HTML 文件（.html/.htm）
    if html_file is not None:
        hname = html_file.filename or ""
        hsuf = Path(hname).suffix.lower()
        if hsuf not in {".html", ".htm"}:
            raise HTTPException(400, f"HTML 上传仅支持 .html/.htm，收到 {hsuf or '无扩展名'}")
        hdata = await html_file.read()
        if not hdata:
            raise HTTPException(400, "上传的 HTML 文件为空")
        if len(hdata) > 25 * 1024 * 1024:
            raise HTTPException(400, "HTML 文件超过 25 MB 上传限制")
        try:
            html_src = hdata.decode("utf-8")
        except UnicodeDecodeError:
            html_src = hdata.decode("gb18030", errors="ignore")

    if not settings.llm_available():
        raise HTTPException(400, "未配置 OPENAI_API_KEY，无法使用 LLM 设计模式")

    if html_src:
        # 导入适配模式：用自带 HTML（+可选文案）
        sess = design_session.create_session_from_html(html_src, content)
        if sess is None:
            raise HTTPException(500, "HTML 导入适配失败，请稍后重试")
    else:
        if not content:
            raise HTTPException(400, f"请粘贴文案/上传文案文件，或上传/粘贴已设计的 HTML")
        sess = design_session.create_session(content)
        if sess is None:
            raise HTTPException(500, "设计生成失败，请稍后重试")
    return {"sid": sess.sid, "title": sess.title, "slides": sess.slides}


@app.get("/api/design/{sid}")
async def design_get(sid: str):
    sess = design_session.get_session(sid)
    if sess is None:
        raise HTTPException(404, "设计会话不存在")
    return {"sid": sess.sid, "title": sess.title, "slides": sess.slides, "history": sess.history}


@app.post("/api/design/{sid}/revise")
async def design_revise(sid: str, instruction: str = Form(...)):
    if not instruction.strip():
        raise HTTPException(400, "修改指令为空")
    sess = design_session.revise_session(sid, instruction)
    if sess is None:
        raise HTTPException(404, "设计会话不存在")
    last = sess.history[-1]["content"] if sess.history else ""
    return {"sid": sess.sid, "title": sess.title, "slides": sess.slides, "reply": last}


@app.post("/api/design/{sid}/produce")
async def design_produce(sid: str, subtitle: bool = Form(default=True), voice: str = Form(default="")):
    sess = design_session.get_session(sid)
    if sess is None:
        raise HTTPException(404, "设计会话不存在")
    voice = voice if voice in MINIMAX_VOICE_IDS else settings.minimax_voice
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    JOBS[job_id] = {"progress": 0.0, "message": "排队中…", "status": "queued"}
    threading.Thread(
        target=_design_produce_worker,
        args=(job_id, sid, subtitle, voice),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.get("/api/design-css")
async def design_css():
    """返回设计系统 base CSS，供前端预览 iframe 注入。"""
    from src import slide_design
    return JSONResponse({"css": slide_design.base_css()})


@app.get("/design", response_class=HTMLResponse)
async def design_page():
    return DESIGN_HTML


@app.get("/", response_class=HTMLResponse)
async def index():
    llm_label = f"OpenAI · {settings.openai_model}" if settings.llm_available() else "规则兜底（未配置 Key）"
    opts = []
    for vid, name, desc in MINIMAX_VOICES:
        sel = " selected" if vid == settings.minimax_voice else ""
        opts.append(f'<option value="{vid}"{sel}>{name} · {desc}</option>')

    chips = []
    for key, th in THEMES.items():
        active = " active" if key == settings.theme_name else ""
        text_col = "#fff" if max(th.bg_top) < 128 else "#1d1d1f"
        chips.append(
            f'<button type="button" data-theme="{key}" class="chip{active}">'
            f'<span class="dot" style="background:{th.swatch}"></span>{th.label}</button>'
        )
    return (
        HTML.replace("{{LLM}}", llm_label)
        .replace("{{TTS}}", settings.tts_provider)
        .replace("{{VOICES}}", "".join(opts))
        .replace("{{THEMES}}", "".join(chips))
    )


HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AI 课程视频生成</title>
<style>
  :root{
    --bg:#fbfbfd; --surface:#ffffff; --line:#d2d2d7;
    --ink:#1d1d1f; --muted:#6e6e73; --blue:#0071e3; --blue-d:#0077ed;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--ink); min-height:100vh;
    -webkit-font-smoothing:antialiased;
  }
  .wrap{max-width:820px;margin:0 auto;padding:72px 24px 96px;}
  .hero{text-align:center;margin-bottom:44px;}
  h1{font-size:48px;line-height:1.07;letter-spacing:-.02em;font-weight:600;margin:0 0 14px;}
  .hero p{font-size:20px;color:var(--muted);margin:0;font-weight:400;}
  .badges{display:flex;gap:8px;justify-content:center;margin-top:20px;flex-wrap:wrap;}
  .badge{font-size:12.5px;color:var(--muted);background:#f5f5f7;border-radius:980px;padding:6px 14px;}
  .card{background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:28px;margin-bottom:22px;box-shadow:0 1px 3px rgba(0,0,0,.04);}
  .label{font-size:13px;font-weight:600;color:var(--muted);margin:0 0 10px;letter-spacing:.01em;}
  textarea{width:100%;min-height:190px;background:#fff;border:1px solid var(--line);border-radius:14px;color:var(--ink);padding:16px;font-size:15px;line-height:1.7;resize:vertical;font-family:inherit;}
  textarea:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 4px rgba(0,113,227,.12);}
  .field{margin-top:22px;}
  .file-row{display:flex;align-items:center;gap:12px;}
  .file-btn{font-size:14px;color:var(--blue);cursor:pointer;font-weight:500;}
  input[type=file]{display:none;}
  .file-status{display:flex;align-items:center;gap:12px;margin-top:12px;padding:13px 15px;border:1px solid var(--line);border-radius:12px;background:#f5f5f7;color:var(--muted);transition:.2s;}
  .file-status.selected{border-color:#8bd49d;background:#f0fbf3;color:#176c2f;box-shadow:0 0 0 3px rgba(52,199,89,.09);}
  .file-icon{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#e5e5ea;color:var(--muted);font-size:15px;font-weight:700;flex:0 0 auto;}
  .file-status.selected .file-icon{background:#34c759;color:#fff;}
  .file-info{min-width:0;}
  .fname{display:block;font-size:14px;font-weight:600;color:inherit;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .fmeta{display:block;font-size:12px;color:var(--muted);margin-top:2px;}
  /* 主题选择：可换行的色卡 chips */
  .chips{display:flex;flex-wrap:wrap;gap:10px;}
  .chip{display:inline-flex;align-items:center;border:1px solid var(--line);background:#fff;color:var(--ink);font-size:14px;font-weight:500;padding:9px 16px;border-radius:980px;cursor:pointer;transition:.15s;font-family:inherit;}
  .chip:hover{border-color:#b9b9c0;}
  .chip.active{border-color:var(--blue);box-shadow:0 0 0 3px rgba(0,113,227,.14);}
  .chip .dot{display:inline-block;width:14px;height:14px;border-radius:50%;margin-right:8px;border:1px solid rgba(0,0,0,.12);}
  /* 下拉选择 */
  .select-wrap{position:relative;display:block;}
  select{appearance:none;-webkit-appearance:none;width:100%;background:#fff;border:1px solid var(--line);border-radius:12px;padding:13px 40px 13px 16px;font-size:15px;color:var(--ink);font-family:inherit;cursor:pointer;}
  select:focus{outline:none;border-color:var(--blue);box-shadow:0 0 0 4px rgba(0,113,227,.12);}
  .select-wrap:after{content:"⌄";position:absolute;right:16px;top:46%;transform:translateY(-50%);color:var(--muted);pointer-events:none;font-size:18px;}
  /* 开关 */
  .switch{position:relative;display:inline-block;width:46px;height:28px;}
  .switch input{opacity:0;width:0;height:0;}
  .slider{position:absolute;inset:0;background:#e3e3e8;border-radius:980px;transition:.2s;cursor:pointer;}
  .slider:before{content:"";position:absolute;height:24px;width:24px;left:2px;top:2px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 1px 3px rgba(0,0,0,.2);}
  .switch input:checked + .slider{background:#34c759;}
  .switch input:checked + .slider:before{transform:translateX(18px);}
  .toggle-row{display:flex;align-items:center;justify-content:space-between;}
  .toggle-row .t{font-size:15px;}
  .actions{text-align:center;margin-top:8px;}
  .btn{background:var(--blue);color:#fff;border:0;border-radius:980px;padding:14px 40px;font-size:17px;font-weight:500;cursor:pointer;transition:.15s;font-family:inherit;}
  .btn:hover{background:var(--blue-d);}
  .btn:disabled{opacity:.4;cursor:not-allowed;}
  .progress{height:6px;background:#e9e9ec;border-radius:980px;overflow:hidden;}
  .bar{height:100%;width:0;background:var(--blue);border-radius:980px;transition:width .3s;}
  .pmsg{color:var(--muted);font-size:14px;margin-top:14px;text-align:center;}
  video{width:100%;border-radius:16px;margin-top:4px;background:#000;display:block;}
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px;}
  .stat{text-align:center;}
  .stat b{display:block;font-size:28px;font-weight:600;letter-spacing:-.02em;}
  .stat span{font-size:13px;color:var(--muted);}
  .dl{display:block;text-align:center;margin-top:22px;}
  .dl a{color:var(--blue);text-decoration:none;font-size:16px;font-weight:500;}
  .warn{background:#fff4e5;border:1px solid #ffd08a;color:#8a5a00;border-radius:12px;padding:12px 16px;font-size:14px;margin-bottom:16px;line-height:1.6;}
  .hidden{display:none;}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>课程视频，自动生成。</h1>
    <p>输入一篇上课文案，自动产出带配音、重点高亮与字幕的讲解视频。</p>
    <div class="badges">
      <span class="badge">LLM · {{LLM}}</span>
      <span class="badge">TTS · {{TTS}}</span>
    </div>
    <div style="margin-top:18px;">
      <a href="/design" style="color:var(--blue);text-decoration:none;font-weight:600;font-size:15px;">✨ 进入「PPT 设计工作台」：先设计、对话微调、满意再生成 →</a>
    </div>
  </div>

  <div class="card">
    <div class="label">上课文案</div>
    <textarea id="text" placeholder="在此粘贴上课文案，或上传 TXT、Markdown、PDF、Word 文档…"></textarea>

    <div class="field">
      <div class="file-row">
        <label class="file-btn" for="file">＋ 选择文件</label>
        <input type="file" id="file" accept=".txt,.md,.markdown,.pdf,.docx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"/>
      </div>
      <div class="file-status" id="file-status">
        <span class="file-icon" id="file-icon">＋</span>
        <span class="file-info">
          <span class="fname" id="fname">尚未选择文件</span>
          <span class="fmeta" id="fmeta">支持 TXT、Markdown、PDF、Word</span>
        </span>
      </div>
      <div class="label" style="margin-top:9px;font-weight:400;">支持 .txt、.md、.markdown、.pdf、.docx，最大 25 MB；PDF 将通过 MinerU 解析</div>
    </div>

    <div class="field">
      <div class="label">PPT 风格</div>
      <div class="chips" id="seg">{{THEMES}}</div>
    </div>

    <div class="field">
      <div class="label">配音音色（MiniMax）</div>
      <div class="select-wrap">
        <select id="voice">{{VOICES}}</select>
      </div>
    </div>

    <div class="field toggle-row">
      <span class="t">叠加字幕</span>
      <label class="switch"><input type="checkbox" id="subtitle" checked/><span class="slider"></span></label>
    </div>
  </div>

  <div class="actions">
    <button class="btn" id="go">开始生成</button>
  </div>

  <div class="card hidden" id="prog-card" style="margin-top:22px;">
    <div class="progress"><div class="bar" id="bar"></div></div>
    <div class="pmsg" id="pmsg">准备中…</div>
  </div>

  <div class="card hidden" id="result-card">
    <div id="warn" class="warn hidden"></div>
    <video id="video" controls></video>
    <div class="stats" id="stats"></div>
    <div class="dl">
      <a id="dl" href="#" download>下载 MP4 ↓</a>
      <a id="dlppt" href="#" download style="margin-left:24px;">下载 PPT ↓</a>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let timer = null;
const activeChip = document.querySelector("#seg .chip.active") || document.querySelector("#seg .chip");
let theme = activeChip ? activeChip.dataset.theme : "apple";
const resumeJob = new URLSearchParams(window.location.search).get("job");

document.querySelectorAll("#seg .chip").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll("#seg .chip").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    theme = b.dataset.theme;
  };
});

$("file").onchange = () => {
  const file = $("file").files[0];
  if(file){
    const ext = file.name.includes(".") ? file.name.split(".").pop().toUpperCase() : "文件";
    const size = file.size < 1024*1024
      ? (file.size/1024).toFixed(1) + " KB"
      : (file.size/1024/1024).toFixed(2) + " MB";
    $("file-status").classList.add("selected");
    $("file-icon").textContent = "✓";
    $("fname").textContent = file.name;
    $("fmeta").textContent = `${ext} · ${size} · 已选择，生成时将优先使用此文件`;
  } else {
    $("file-status").classList.remove("selected");
    $("file-icon").textContent = "＋";
    $("fname").textContent = "尚未选择文件";
    $("fmeta").textContent = "支持 TXT、Markdown、PDF、Word";
  }
};

$("go").onclick = async () => {
  const text = $("text").value.trim();
  const file = $("file").files[0];
  if(!text && !file){ alert("请粘贴文案或选择文件"); return; }
  const fd = new FormData();
  if(file) fd.append("file", file);
  fd.append("text", text);
  fd.append("subtitle", $("subtitle").checked);
  fd.append("theme", theme);
  fd.append("voice", $("voice").value);

  $("go").disabled = true;
  $("prog-card").classList.remove("hidden");
  $("result-card").classList.add("hidden");
  setBar(0.02, "提交任务…");

  const r = await fetch("/api/generate", {method:"POST", body:fd});
  if(!r.ok){ const e = await r.json(); alert(e.detail||"提交失败"); $("go").disabled=false; return; }
  const {job_id} = await r.json();
  poll(job_id);
};

function setBar(p, msg){
  $("bar").style.width = (p*100).toFixed(1)+"%";
  $("pmsg").textContent = msg + "  ·  " + (p*100).toFixed(0) + "%";
}

function poll(job_id){
  timer = setInterval(async () => {
    const r = await fetch("/api/status/"+job_id);
    const j = await r.json();
    setBar(j.progress||0, j.message||"");
    if(j.status === "done"){
      clearInterval(timer);
      showResult(job_id, j.result);
      $("go").disabled = false;
    } else if(j.status === "error"){
      clearInterval(timer);
      $("pmsg").textContent = "生成失败：" + (j.error||"");
      $("go").disabled = false;
    }
  }, 1200);
}

function showResult(job_id, res){
  $("result-card").classList.remove("hidden");
  const url = "/api/video/"+job_id;
  $("video").src = url;
  $("dl").href = url;
  $("dlppt").href = "/api/pptx/"+job_id;
  $("dlppt").style.display = res.pptx ? "inline" : "none";
  if(res.warning){
    $("warn").textContent = "⚠ " + res.warning + "（PPT/讲解质量会下降，请检查 API Key 后重试）";
    $("warn").classList.remove("hidden");
  } else {
    $("warn").classList.add("hidden");
  }
  $("stats").innerHTML = `
    <div class="stat"><b>${res.slides}</b><span>页 PPT</span></div>
    <div class="stat"><b>${res.video_seconds}s</b><span>视频时长</span></div>
    <div class="stat"><b>${res.elapsed_seconds == null ? "—" : res.elapsed_seconds + "s"}</b><span>生成耗时</span></div>`;
}

if(resumeJob){
  $("prog-card").classList.remove("hidden");
  setBar(0.98, "正在恢复已完成任务…");
  poll(resumeJob);
}
</script>
</body>
</html>"""


DESIGN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>PPT 设计工作台</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--panel2:#1e222b;--line:#2a2f3a;--ink:#e8ebf2;--muted:#8a909e;--accent:#ff8a00;--blue:#3b82f6;}
  *{box-sizing:border-box;}
  html,body{margin:0;height:100%;overflow:hidden;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink);}
  /* 起始输入态 */
  .start{max-width:760px;margin:0 auto;padding:64px 24px;height:100%;overflow:auto;}
  .start h1{font-size:34px;font-weight:700;margin:0 0 8px;}
  .start p{color:var(--muted);margin:0 0 24px;}
  textarea{width:100%;min-height:220px;background:var(--panel);border:1px solid var(--line);border-radius:12px;color:var(--ink);padding:16px;font-size:15px;line-height:1.7;resize:vertical;font-family:inherit;}
  .btn{background:var(--accent);color:#1a1205;border:0;border-radius:10px;padding:12px 28px;font-size:16px;font-weight:700;cursor:pointer;}
  .btn:disabled{opacity:.5;cursor:not-allowed;}
  .btn.ghost{background:transparent;color:var(--ink);border:1px solid var(--line);}
  .btn.blue{background:var(--blue);color:#fff;}
  .row{display:flex;gap:12px;align-items:center;}
  /* 工作台三区 */
  .studio{display:none;height:100vh;flex-direction:column;}
  .topbar{display:flex;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line);background:var(--panel);flex:0 0 auto;}
  .tabs{display:flex;gap:6px;overflow-x:auto;flex:1;}
  .tab{padding:7px 14px;border-radius:8px;background:var(--panel2);border:1px solid var(--line);color:var(--muted);font-size:13px;cursor:pointer;white-space:nowrap;}
  .tab.active{color:var(--ink);border-color:var(--accent);}
  .main{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:minmax(0,1fr);min-height:0;overflow:hidden;}
  .pane{min-width:0;min-height:0;overflow:hidden;display:flex;flex-direction:column;border-right:1px solid var(--line);}
  .pane h4{margin:0;padding:8px 14px;font-size:12px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;border-bottom:1px solid var(--line);background:var(--panel);flex:0 0 auto;}
  pre{margin:0;flex:1;min-height:0;overflow:auto;padding:16px;font-family:"SF Mono","Consolas",monospace;font-size:12.5px;line-height:1.6;color:#cdd3df;white-space:pre-wrap;word-break:break-word;}
  .preview-wrap{flex:1;min-height:0;overflow:hidden;background:#0a0a0a;display:flex;align-items:center;justify-content:center;}
  .scaler{position:relative;}
  iframe{border:0;background:#fff;width:1920px;height:1080px;display:block;}
  .steps{display:flex;gap:6px;padding:8px 14px;border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap;flex:0 0 auto;}
  .step-btn{padding:5px 12px;border-radius:7px;background:var(--panel2);border:1px solid var(--line);color:var(--muted);font-size:12px;cursor:pointer;}
  .step-btn.active{color:#1a1205;background:var(--accent);border-color:var(--accent);font-weight:700;}
  .chatbar{flex:0 0 auto;display:flex;gap:10px;padding:12px 16px;border-top:1px solid var(--line);background:var(--panel);}
  .chatbar input{flex:1;background:var(--panel2);border:1px solid var(--line);border-radius:10px;color:var(--ink);padding:11px 14px;font-size:14px;font-family:inherit;}
  .chatbar input:focus{outline:none;border-color:var(--accent);}
  .msg{padding:8px 16px;font-size:13px;color:var(--muted);}
  .overlay{position:fixed;inset:0;background:rgba(8,10,14,.82);display:none;align-items:center;justify-content:center;flex-direction:column;gap:18px;z-index:50;}
  .overlay.show{display:flex;}
  .prog{width:420px;height:8px;background:#262b35;border-radius:99px;overflow:hidden;}
  .prog>span{display:block;height:100%;width:0;background:var(--accent);transition:width .3s;}
  video{max-width:80vw;max-height:70vh;border-radius:10px;background:#000;}
  a.dl{color:var(--accent);text-decoration:none;font-weight:600;margin:0 12px;}
  .spin{color:var(--muted);font-size:14px;}
</style>
</head>
<body>

<!-- 起始：输入文案 -->
<div class="start" id="start">
  <h1>PPT 设计工作台</h1>
  <p>两种方式：① 只给文案，LLM 从头设计；② 同时给「文案 + 已设计好的 HTML」，系统直接适配你的设计（自动切分、补高亮、配口播），省去从头沟通。</p>
  <textarea id="text" placeholder="在此粘贴课程文案（用于改写口播稿）…"></textarea>
  <div style="margin-top:14px;">
    <div style="font-size:13px;color:var(--muted);margin-bottom:6px;">可选：已设计好的 HTML（粘贴源代码，或上传 .html 文件）。提供后系统不再从头设计，只做工程化适配。</div>
    <textarea id="html" placeholder="可选：在此粘贴已设计好的 PPT HTML 源代码…" style="min-height:120px;font-family:'Consolas',monospace;font-size:13px;"></textarea>
    <div class="row" style="margin-top:8px;">
      <label class="btn ghost" style="font-size:13px;padding:8px 16px;cursor:pointer;">＋ 上传 .html 文件
        <input type="file" id="html-file" accept=".html,.htm" style="display:none;"/>
      </label>
      <span class="spin" id="html-file-name"></span>
    </div>
  </div>
  <div class="row" style="margin-top:18px;">
    <button class="btn" id="design-btn">开始设计 / 适配</button>
    <span class="spin" id="start-msg"></span>
    <a class="dl" href="/" style="margin-left:auto;">← 返回经典模式</a>
  </div>
</div>

<!-- 工作台 -->
<div class="studio" id="studio">
  <div class="topbar">
    <div class="tabs" id="tabs"></div>
    <button class="btn ghost" id="reset-btn">重新设计</button>
    <button class="btn blue" id="produce-btn">✅ 确认，生成视频</button>
  </div>
  <div class="main">
    <div class="pane">
      <h4>HTML 源代码（当前页）</h4>
      <pre id="code"></pre>
    </div>
    <div class="pane" style="border-right:0;">
      <h4>实时渲染预览</h4>
      <div class="steps" id="steps"></div>
      <div class="preview-wrap"><div class="scaler" id="scaler"><iframe id="preview"></iframe></div></div>
    </div>
  </div>
  <div class="msg" id="chat-msg"></div>
  <div class="chatbar">
    <input id="chat" placeholder="对话微调，如：把第3页改成时间线 / 整体配色淡一点 / 封面再大气些"/>
    <button class="btn" id="send-btn">发送</button>
  </div>
</div>

<!-- 生产进度/结果 -->
<div class="overlay" id="overlay">
  <div id="ov-prog-box">
    <div class="prog"><span id="bar"></span></div>
    <div class="spin" id="ov-msg" style="text-align:center;margin-top:12px;">准备中…</div>
  </div>
  <div id="ov-result" style="display:none;text-align:center;">
    <video id="video" controls></video>
    <div style="margin-top:14px;">
      <a class="dl" id="dl-mp4" download>下载 MP4 ↓</a>
      <a class="dl" id="dl-ppt" download>下载 PPT ↓</a>
      <a class="dl" href="/design" style="cursor:pointer;">再做一个</a>
    </div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
let SID=null, SLIDES=[], CUR=0, FOCUS=0, BASECSS="", timer=null;

const HL_CSS = `.hl-on{outline:4px solid var(--accent);outline-offset:6px;border-radius:14px;box-shadow:0 0 0 6px color-mix(in srgb,var(--accent) 22%,transparent);} .hl-dim{opacity:.38;filter:saturate(.7);}`;

async function loadCss(){ const r=await fetch("/api/design-css"); BASECSS=(await r.json()).css; }
loadCss();

function renderTabs(){
  $("tabs").innerHTML = SLIDES.map((s,i)=>`<div class="tab${i===CUR?' active':''}" data-i="${i}">第 ${i+1} 页</div>`).join("");
  document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{CUR=+t.dataset.i; FOCUS=0; showPage();});
}
function renderSteps(){
  const s=SLIDES[CUR]||{}; const steps=s.steps||[];
  let html = `<span class="step-btn${FOCUS===0?' active':''}" data-f="0">基础页</span>`;
  // 收集所有 focus>0 的编号
  const focuses=[...new Set(steps.map(x=>x.focus).filter(f=>f>0))].sort((a,b)=>a-b);
  html += focuses.map(f=>`<span class="step-btn${FOCUS===f?' active':''}" data-f="${f}">高亮 ${f}</span>`).join("");
  $("steps").innerHTML = html;
  document.querySelectorAll("#steps .step-btn").forEach(b=>b.onclick=()=>{FOCUS=+b.dataset.f; showPage();});
}
function showPage(){
  renderTabs(); renderSteps();
  const s=SLIDES[CUR]||{};
  $("code").textContent = s.html || "(空)";
  const applier=`<script>(function(){var f=${FOCUS};document.querySelectorAll('[data-hl]').forEach(function(el){el.classList.remove('hl-on','hl-dim');if(f>0){if(String(el.getAttribute('data-hl'))===String(f))el.classList.add('hl-on');else el.classList.add('hl-dim');}});})();<\\/script>`;
  const doc=`<!doctype html><html><head><meta charset="utf-8"><style>${BASECSS}\n${HL_CSS}</style></head><body>${s.html||""}${applier}</body></html>`;
  $("preview").srcdoc=doc;
  fitPreview();
}
function fitPreview(){
  const ifr=$("preview"), scaler=$("scaler"), wrap=ifr.closest(".preview-wrap");
  if(!wrap) return;
  const pad=24;
  const scale=Math.min((wrap.clientWidth-pad)/1920, (wrap.clientHeight-pad)/1080);
  ifr.style.transform=`scale(${scale})`;
  ifr.style.transformOrigin="top left";
  scaler.style.width=(1920*scale)+"px";
  scaler.style.height=(1080*scale)+"px";
}
window.addEventListener("resize", fitPreview);

let HTMLFILE=null;
$("html-file").onchange=()=>{
  HTMLFILE=$("html-file").files[0]||null;
  $("html-file-name").textContent = HTMLFILE ? ("已选择："+HTMLFILE.name) : "";
};

$("design-btn").onclick=async()=>{
  const text=$("text").value.trim();
  const html=$("html").value.trim();
  if(!text && !html && !HTMLFILE){ alert("请至少粘贴文案，或提供已设计的 HTML"); return; }
  $("design-btn").disabled=true;
  $("start-msg").textContent = (html||HTMLFILE) ? "正在适配你的 HTML…（约十几秒）" : "LLM 正在设计每一页…（约十几秒）";
  const fd=new FormData();
  fd.append("text", text);
  if(html) fd.append("html", html);
  if(HTMLFILE) fd.append("html_file", HTMLFILE);
  const r=await fetch("/api/design",{method:"POST",body:fd});
  $("design-btn").disabled=false; $("start-msg").textContent="";
  if(!r.ok){ const e=await r.json(); alert(e.detail||"设计失败"); return; }
  const j=await r.json(); SID=j.sid; SLIDES=j.slides; CUR=0; FOCUS=0;
  $("start").style.display="none"; $("studio").style.display="flex";
  showPage();
};

$("send-btn").onclick=async()=>{
  const instr=$("chat").value.trim();
  if(!instr||!SID) return;
  $("send-btn").disabled=true; $("chat-msg").textContent="正在按要求修改…";
  const fd=new FormData(); fd.append("instruction", instr);
  const r=await fetch(`/api/design/${SID}/revise`,{method:"POST",body:fd});
  $("send-btn").disabled=false;
  if(!r.ok){ $("chat-msg").textContent="修改失败"; return; }
  const j=await r.json(); SLIDES=j.slides; $("chat").value=""; FOCUS=0;
  $("chat-msg").textContent = j.reply || "已更新";
  showPage();
};
$("chat").addEventListener("keydown",e=>{ if(e.key==="Enter") $("send-btn").click(); });

$("reset-btn").onclick=()=>{ location.href="/design"; };

$("produce-btn").onclick=async()=>{
  if(!SID) return;
  const fd=new FormData(); fd.append("subtitle","true");
  const r=await fetch(`/api/design/${SID}/produce`,{method:"POST",body:fd});
  if(!r.ok){ const e=await r.json(); alert(e.detail||"无法生成"); return; }
  const {job_id}=await r.json();
  $("overlay").classList.add("show");
  poll(job_id);
};

function poll(job_id){
  timer=setInterval(async()=>{
    const r=await fetch("/api/status/"+job_id); const j=await r.json();
    $("bar").style.width=((j.progress||0)*100).toFixed(1)+"%";
    $("ov-msg").textContent=(j.message||"")+"  ·  "+((j.progress||0)*100).toFixed(0)+"%";
    if(j.status==="done"){ clearInterval(timer); showResult(job_id,j.result); }
    else if(j.status==="error"){ clearInterval(timer); $("ov-msg").textContent="生成失败："+(j.error||""); }
  },1200);
}
function showResult(job_id,res){
  $("ov-prog-box").style.display="none"; $("ov-result").style.display="block";
  $("video").src="/api/video/"+job_id;
  $("dl-mp4").href="/api/video/"+job_id;
  $("dl-ppt").href="/api/pptx/"+job_id;
  $("dl-ppt").style.display=res && res.pptx ? "inline":"none";
}
</script>
</body>
</html>"""
