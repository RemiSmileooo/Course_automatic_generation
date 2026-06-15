"""TTS 模块：把口播稿合成为中文配音音频。

provider 选择优先级：
    minimax -> 失败/缺 Key -> edge -> 失败 -> offline(静音占位)
这样无论是否配置 Key，整条流水线都能跑通并产出可演示视频。
"""
from __future__ import annotations

import asyncio
import struct
import time
import wave
from pathlib import Path

import requests

from .config import settings

_http = requests.Session()
_http.trust_env = False


def synthesize(text: str, out_path: str | Path) -> tuple[str, float]:
    """合成一段音频，返回 (实际文件路径, 时长秒)。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provider = settings.tts_provider

    order = []
    if provider == "minimax":
        order = ["minimax", "edge", "offline"]
    elif provider == "edge":
        order = ["edge", "offline"]
    elif provider == "offline":
        order = ["offline"]
    else:
        order = ["edge", "offline"]

    last_err = None
    for prov in order:
        try:
            if prov == "minimax":
                if not (settings.minimax_api_key and settings.minimax_group_id):
                    raise RuntimeError("缺少 MINIMAX_API_KEY / MINIMAX_GROUP_ID")
                return _minimax(text, out_path.with_suffix(".mp3"))
            if prov == "edge":
                return _edge(text, out_path.with_suffix(".mp3"))
            if prov == "offline":
                return _offline(text, out_path.with_suffix(".wav"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[tts] provider={prov} 失败，尝试下一个: {e}")
    raise RuntimeError(f"所有 TTS 均失败: {last_err}")


# --------------------------------------------------------------------------- #
# MiniMax Speech (T2A v2)
# --------------------------------------------------------------------------- #
def _minimax(text: str, out_path: Path) -> tuple[str, float]:
    url = f"{settings.minimax_api_host}/v1/t2a_v2?GroupId={settings.minimax_group_id}"
    headers = {
        "Authorization": f"Bearer {settings.minimax_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.minimax_model,
        "text": text,
        "stream": False,
        "voice_setting": {
            "voice_id": settings.minimax_voice,
            "speed": settings.minimax_speed,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    # 遇到 RPM 限流(1002)时退避重试，避免降级到其它 TTS 造成音色不一致
    max_retries = 6
    data = {}
    for attempt in range(max_retries):
        r = _http.post(url, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        code = data.get("base_resp", {}).get("status_code", 0)
        if code == 0:
            break
        if code == 1002 and attempt < max_retries - 1:
            wait = 2 ** attempt  # 1,2,4,8,16s
            print(f"[tts] MiniMax 限流，{wait}s 后重试({attempt + 1}/{max_retries})…")
            time.sleep(wait)
            continue
        raise RuntimeError(f"MiniMax 返回错误: {data.get('base_resp')}")
    audio_hex = data["data"]["audio"]
    audio_bytes = bytes.fromhex(audio_hex)
    out_path.write_bytes(audio_bytes)
    # 优先用返回的精确时长
    ms = data.get("extra_info", {}).get("audio_length")
    duration = (ms / 1000.0) if ms else _probe_duration(out_path)
    return str(out_path), duration


# --------------------------------------------------------------------------- #
# Edge TTS（免费，无需 Key）
# --------------------------------------------------------------------------- #
def _edge(text: str, out_path: Path) -> tuple[str, float]:
    import edge_tts

    async def _run():
        communicate = edge_tts.Communicate(text, settings.edge_voice)
        await communicate.save(str(out_path))

    asyncio.run(_run())
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("edge-tts 未生成有效音频（可能无网络）")
    return str(out_path), _probe_duration(out_path)


# --------------------------------------------------------------------------- #
# 离线静音占位：按字数估算时长，保证视频可合成
# --------------------------------------------------------------------------- #
def _offline(text: str, out_path: Path) -> tuple[str, float]:
    # 中文约每秒 4.5 字，加 0.6s 余量；最短 1.2s
    n = max(1, len(text.strip()))
    duration = max(1.2, n / 4.5 + 0.6)
    sr = 16000
    n_frames = int(duration * sr)
    with wave.open(str(out_path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        silence = struct.pack("<h", 0) * n_frames
        w.writeframes(silence)
    return str(out_path), duration


# --------------------------------------------------------------------------- #
# 时长探测
# --------------------------------------------------------------------------- #
def _probe_duration(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "r") as w:
            return w.getnframes() / float(w.getframerate())
    # mp3 等交给 moviepy 的音频读取
    try:
        from moviepy import AudioFileClip

        clip = AudioFileClip(str(path))
        d = float(clip.duration)
        clip.close()
        return d
    except Exception:
        # 兜底估算
        return max(1.5, path.stat().st_size / 16000.0)
