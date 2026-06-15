"""MinerU 精准解析 API：本地 PDF -> Markdown。"""
from __future__ import annotations

import io
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import settings

ProgressCb = Optional[Callable[[float, str], None]]


class MinerUError(RuntimeError):
    """MinerU 配置、上传、解析或结果下载失败。"""


_http = requests.Session()
_http.trust_env = False


def parse_pdf(filename: str, data: bytes, progress_cb: ProgressCb = None) -> str:
    """上传本地 PDF，轮询解析任务，并从结果 ZIP 中读取 full.md。"""
    if not settings.mineru_api_key:
        raise MinerUError("未配置 MINERU_API_KEY，无法解析 PDF")
    if not data:
        raise MinerUError("PDF 文件为空")

    cb = progress_cb or (lambda _p, _m: None)
    base_url = settings.mineru_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.mineru_api_key}",
        "Content-Type": "application/json",
    }
    safe_name = Path(filename or "document.pdf").name
    data_id = f"course_{uuid.uuid4().hex}"

    cb(0.01, "正在向 MinerU 申请上传地址…")
    payload = {
        "files": [
            {
                "name": safe_name,
                "data_id": data_id,
                "is_ocr": settings.mineru_ocr,
            }
        ],
        "model_version": settings.mineru_model,
        "enable_table": settings.mineru_enable_table,
        "enable_formula": settings.mineru_enable_formula,
        "language": settings.mineru_language,
    }
    result = _request_json(
        "POST",
        f"{base_url}/api/v4/file-urls/batch",
        headers=headers,
        json=payload,
        timeout=30,
    )
    response_data = result.get("data") or {}
    batch_id = response_data.get("batch_id")
    file_urls = response_data.get("file_urls") or []
    if not batch_id or not file_urls:
        raise MinerUError("MinerU 未返回 batch_id 或文件上传地址")

    cb(0.03, "正在上传 PDF 到 MinerU…")
    upload = _http.put(file_urls[0], data=data, timeout=settings.mineru_upload_timeout)
    if upload.status_code not in {200, 201, 204}:
        raise MinerUError(f"MinerU PDF 上传失败：HTTP {upload.status_code}")

    deadline = time.monotonic() + settings.mineru_timeout
    poll_url = f"{base_url}/api/v4/extract-results/batch/{batch_id}"
    while True:
        if time.monotonic() >= deadline:
            raise MinerUError(f"MinerU 解析超时（超过 {settings.mineru_timeout} 秒）")
        time.sleep(settings.mineru_poll_interval)
        task = _request_json("GET", poll_url, headers=headers, timeout=30)
        task_data = task.get("data") or {}
        item = _select_task_item(task_data, data_id)
        state = str(item.get("state", "")).lower()

        if state in {"pending", "waiting", "waiting-file", "converting"}:
            cb(0.05, "MinerU 正在排队解析…")
            continue
        if state == "running":
            progress = item.get("extract_progress") or {}
            extracted = int(progress.get("extracted_pages") or 0)
            total = int(progress.get("total_pages") or 0)
            ratio = (extracted / total) if total else 0.0
            cb(0.05 + min(0.08, ratio * 0.08), f"MinerU 正在解析 PDF：{extracted}/{total or '?'} 页")
            continue
        if state == "failed":
            raise MinerUError(f"MinerU 解析失败：{item.get('err_msg') or '未知错误'}")
        if state == "done":
            zip_url = item.get("full_zip_url")
            if not zip_url:
                raise MinerUError("MinerU 任务已完成，但未返回结果 ZIP 地址")
            cb(0.14, "正在下载 MinerU Markdown 结果…")
            markdown = _download_markdown(zip_url)
            cb(0.16, "MinerU PDF 解析完成")
            return markdown

        cb(0.05, f"MinerU 状态：{state or '未知'}")


def _request_json(method: str, url: str, **kwargs) -> dict:
    try:
        response = _http.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise MinerUError(f"MinerU API 连接失败：{exc}") from exc
    except ValueError as exc:
        raise MinerUError("MinerU API 返回了无效 JSON") from exc
    if payload.get("code") != 0:
        raise MinerUError(f"MinerU API 错误：{payload.get('msg') or payload}")
    return payload


def _select_task_item(data: dict, data_id: str) -> dict:
    """兼容批量结果中 data/results/extract_result 等常见包装字段。"""
    candidates = (
        data.get("extract_result"),
        data.get("extract_results"),
        data.get("results"),
        data.get("files"),
    )
    for candidate in candidates:
        if isinstance(candidate, list) and candidate:
            return next(
                (item for item in candidate if isinstance(item, dict) and item.get("data_id") == data_id),
                candidate[0],
            )
    return data


def _download_markdown(zip_url: str) -> str:
    try:
        response = _http.get(zip_url, timeout=settings.mineru_download_timeout)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = [
                name for name in archive.namelist()
                if not name.endswith("/") and Path(name).name.lower() == "full.md"
            ]
            if not names:
                names = [
                    name for name in archive.namelist()
                    if not name.endswith("/") and name.lower().endswith(".md")
                ]
            if not names:
                raise MinerUError("MinerU 结果 ZIP 中没有 Markdown 文件")
            return archive.read(names[0]).decode("utf-8-sig")
    except requests.RequestException as exc:
        raise MinerUError(f"下载 MinerU 结果失败：{exc}") from exc
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
        raise MinerUError(f"读取 MinerU 结果失败：{exc}") from exc
