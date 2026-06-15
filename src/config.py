"""全局配置：从环境变量 / .env 读取，并提供字体、配色等可视化常量。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # python-dotenv 未安装时静默跳过
    pass

ROOT = Path(__file__).resolve().parent.parent


def _find_cjk_font(bold: bool = False) -> str:
    """按平台寻找一个可用的中文字体，找不到则回退到 PIL 默认。"""
    candidates_regular = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    candidates_bold = [
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for p in (candidates_bold if bold else candidates_regular):
        if os.path.exists(p):
            return p
    # 允许用环境变量覆盖
    env = os.getenv("CJK_FONT_PATH")
    if env and os.path.exists(env):
        return env
    return ""


# 可选 MiniMax 音色： (voice_id, 中文名, 适用场景)
MINIMAX_VOICES = [
    ("Chinese (Mandarin)_Gentle_Senior", "温柔学姐", "自然亲切的课程讲解"),
    ("Chinese (Mandarin)_Warm_Bestie", "温暖闺蜜", "陪伴感、轻松科普"),
    ("Chinese (Mandarin)_News_Anchor", "新闻女声", "正式、清晰，播音感较强"),
    ("Chinese (Mandarin)_Wise_Women", "阅历姐姐", "成熟、沉稳的讲解"),
    ("Chinese (Mandarin)_Crisp_Girl", "清脆少女", "清晰、年轻、有精神"),
    ("Chinese (Mandarin)_Soft_Girl", "柔和少女", "柔和、舒缓"),
    ("Chinese (Mandarin)_Gentleman", "温润男声", "稳定、耐听"),
    ("Chinese (Mandarin)_Radio_Host", "电台男主播", "叙述性强"),
    ("Chinese (Mandarin)_Reliable_Executive", "沉稳高管", "商务课程、严肃内容"),
    ("Chinese (Mandarin)_Gentle_Youth", "温润青年", "自然温和的知识讲解"),
]
MINIMAX_VOICE_IDS = {v[0] for v in MINIMAX_VOICES}


@dataclass
class Theme:
    """一套视觉主题。"""
    name: str
    label: str              # 中文展示名（前端用）
    swatch: str             # 前端色卡 hex（代表该主题观感）
    bg_top: tuple            # 背景渐变上色（纯色时与 bg_bottom 相同）
    bg_bottom: tuple
    title: tuple             # 标题色
    text: tuple             # 正文色
    accent: tuple           # 主色（高亮 / 强调）
    bullet: tuple           # 要点圆点色
    subtitle_bg: tuple      # 字幕条底色(rgb)
    subtitle_alpha: int     # 字幕条不透明度 0-255
    subtitle_text: tuple    # 字幕文字色
    show_top_bar: bool      # 顶部品牌色条
    highlight_fill_alpha: int  # 高亮框内填充透明度


THEMES = {
    # —— 浅色系 ——
    # Apple 简约亮色风（默认）
    "apple": Theme(
        name="apple", label="简约亮色", swatch="#f0f0f2",
        bg_top=(251, 251, 253), bg_bottom=(238, 238, 242),
        title=(29, 29, 31), text=(66, 66, 69),
        accent=(255, 138, 0), bullet=(134, 134, 139),
        subtitle_bg=(29, 29, 31), subtitle_alpha=170, subtitle_text=(255, 255, 255),
        show_top_bar=False, highlight_fill_alpha=30,
    ),
    # 宣纸学术风（暖米白 + 朱红）
    "ink": Theme(
        name="ink", label="宣纸学术", swatch="#f3ece0",
        bg_top=(247, 244, 236), bg_bottom=(237, 231, 219),
        title=(40, 34, 28), text=(78, 70, 60),
        accent=(193, 68, 46), bullet=(150, 138, 122),
        subtitle_bg=(40, 34, 28), subtitle_alpha=170, subtitle_text=(255, 255, 255),
        show_top_bar=False, highlight_fill_alpha=32,
    ),
    # 薄荷清新风（浅绿 + 翡翠）
    "mint": Theme(
        name="mint", label="薄荷清新", swatch="#e7f3ed",
        bg_top=(244, 250, 247), bg_bottom=(229, 242, 235),
        title=(20, 48, 42), text=(58, 84, 76),
        accent=(16, 185, 129), bullet=(130, 162, 150),
        subtitle_bg=(18, 40, 35), subtitle_alpha=170, subtitle_text=(255, 255, 255),
        show_top_bar=False, highlight_fill_alpha=34,
    ),
    # —— 深色系 ——
    # 深色专业风（原版，藏蓝 + 橙）
    "dark": Theme(
        name="dark", label="深色专业", swatch="#18213a",
        bg_top=(24, 33, 56), bg_bottom=(12, 17, 33),
        title=(255, 255, 255), text=(222, 228, 240),
        accent=(255, 138, 0), bullet=(255, 170, 60),
        subtitle_bg=(0, 0, 0), subtitle_alpha=150, subtitle_text=(255, 255, 255),
        show_top_bar=True, highlight_fill_alpha=46,
    ),
    # 深海蓝风（深蓝 + 青）
    "ocean": Theme(
        name="ocean", label="深海蓝", swatch="#0b2a4a",
        bg_top=(11, 42, 74), bg_bottom=(7, 22, 42),
        title=(255, 255, 255), text=(206, 224, 240),
        accent=(54, 197, 240), bullet=(120, 174, 214),
        subtitle_bg=(0, 0, 0), subtitle_alpha=150, subtitle_text=(255, 255, 255),
        show_top_bar=True, highlight_fill_alpha=46,
    ),
    # 霓夜紫风（深紫 + 亮紫）
    "violet": Theme(
        name="violet", label="霓夜紫", swatch="#241a3a",
        bg_top=(36, 26, 58), bg_bottom=(19, 12, 35),
        title=(255, 255, 255), text=(224, 216, 240),
        accent=(168, 85, 247), bullet=(176, 146, 214),
        subtitle_bg=(0, 0, 0), subtitle_alpha=150, subtitle_text=(255, 255, 255),
        show_top_bar=True, highlight_fill_alpha=46,
    ),
}


@dataclass
class Settings:
    # ---- LLM ----
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

    # ---- MinerU PDF 解析 ----
    mineru_api_key: str = field(default_factory=lambda: os.getenv("MINERU_API_KEY", ""))
    mineru_base_url: str = field(default_factory=lambda: os.getenv("MINERU_BASE_URL", "https://mineru.net").rstrip("/"))
    mineru_model: str = field(default_factory=lambda: os.getenv("MINERU_MODEL", "vlm"))
    mineru_language: str = field(default_factory=lambda: os.getenv("MINERU_LANGUAGE", "ch"))
    mineru_ocr: bool = field(default_factory=lambda: os.getenv("MINERU_OCR", "true").lower() in {"1", "true", "yes", "on"})
    mineru_enable_table: bool = field(default_factory=lambda: os.getenv("MINERU_ENABLE_TABLE", "true").lower() in {"1", "true", "yes", "on"})
    mineru_enable_formula: bool = field(default_factory=lambda: os.getenv("MINERU_ENABLE_FORMULA", "true").lower() in {"1", "true", "yes", "on"})
    mineru_timeout: int = field(default_factory=lambda: int(os.getenv("MINERU_TIMEOUT", "900")))
    mineru_poll_interval: float = field(default_factory=lambda: float(os.getenv("MINERU_POLL_INTERVAL", "3")))
    mineru_upload_timeout: int = field(default_factory=lambda: int(os.getenv("MINERU_UPLOAD_TIMEOUT", "180")))
    mineru_download_timeout: int = field(default_factory=lambda: int(os.getenv("MINERU_DOWNLOAD_TIMEOUT", "180")))

    # ---- TTS ----
    tts_provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "minimax").lower())
    minimax_api_key: str = field(default_factory=lambda: os.getenv("MINIMAX_API_KEY", ""))
    minimax_group_id: str = field(default_factory=lambda: os.getenv("MINIMAX_GROUP_ID", ""))
    # 国际站: https://api.minimax.io  | 国内站: https://api.minimax.chat
    minimax_api_host: str = field(default_factory=lambda: os.getenv("MINIMAX_API_HOST", "https://api.minimax.io").rstrip("/"))
    minimax_model: str = field(default_factory=lambda: os.getenv("MINIMAX_MODEL", "speech-01-turbo"))
    minimax_voice: str = field(default_factory=lambda: os.getenv("MINIMAX_VOICE", "female-shaonv"))
    minimax_speed: float = field(default_factory=lambda: float(os.getenv("MINIMAX_SPEED", "1.0")))
    edge_voice: str = field(default_factory=lambda: os.getenv("EDGE_VOICE", "zh-CN-XiaoxiaoNeural"))

    # ---- 视频 ----
    width: int = field(default_factory=lambda: int(os.getenv("VIDEO_WIDTH", "1920")))
    height: int = field(default_factory=lambda: int(os.getenv("VIDEO_HEIGHT", "1080")))
    fps: int = field(default_factory=lambda: int(os.getenv("VIDEO_FPS", "24")))

    # ---- 字体 ----
    font_regular: str = field(default_factory=lambda: _find_cjk_font(bold=False))
    font_bold: str = field(default_factory=lambda: _find_cjk_font(bold=True))

    # ---- 主题 ----
    theme_name: str = field(default_factory=lambda: os.getenv("THEME", "apple").lower())

    @property
    def theme(self) -> Theme:
        return THEMES.get(self.theme_name, THEMES["apple"])

    def llm_available(self) -> bool:
        return bool(self.openai_api_key)


settings = Settings()
