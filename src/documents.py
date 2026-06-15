"""课程文档解析：把常见上传文件转换为适合课程拆解的纯文本。"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class DocumentParseError(ValueError):
    """上传文件不受支持、损坏或无法提取有效文本。"""


def supported_file_hint() -> str:
    return "TXT、Markdown（.md/.markdown）、PDF、Word（.docx）"


def parse_document(filename: str, data: bytes, progress_cb=None) -> str:
    """按扩展名解析文档字节，并返回经过规范化的课程文本。"""
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()

    if not suffix:
        raise DocumentParseError("文件缺少扩展名，无法判断文档格式")
    if suffix == ".doc":
        raise DocumentParseError("暂不支持旧版 .doc，请在 Word 中另存为 .docx 后上传")
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParseError(
            f"不支持 {suffix} 文件；当前支持 {supported_file_hint()}"
        )
    if not data:
        raise DocumentParseError("上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentParseError("文件超过 25 MB 上传限制")

    try:
        if suffix == ".txt":
            text = _decode_text(data)
        elif suffix in {".md", ".markdown"}:
            text = _parse_markdown(_decode_text(data))
        elif suffix == ".pdf":
            text = _parse_pdf(data, filename=name, progress_cb=progress_cb)
        else:
            text = _parse_docx(data)
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError(f"解析 {name} 失败：{exc}") from exc

    text = _normalize_text(text)
    if len(re.sub(r"\s+", "", text)) < 10:
        if suffix == ".pdf":
            raise DocumentParseError(
                "PDF 未提取到足够文字；如果这是扫描版 PDF，请先进行 OCR"
            )
        raise DocumentParseError("文档中没有提取到足够的有效文字")
    if suffix == ".pdf" and _fragmented_line_ratio(text) >= 0.25:
        raise DocumentParseError(
            "PDF 文本结构异常，存在大量逐字换行；请先导出为 Word/TXT，或对 PDF 进行 OCR 后再上传"
        )
    return text


def parse_document_path(path: str | Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise DocumentParseError(f"输入文件不存在：{path}")
    return parse_document(path.name, path.read_bytes())


def _decode_text(data: bytes) -> str:
    """优先识别常见中文文本编码，避免 errors=ignore 静默丢字。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("文本编码无法识别，请使用 UTF-8 或 GB18030")


def _parse_markdown(text: str) -> str:
    """去除 Markdown 展示语法，同时保留标题、列表、代码和链接文本。"""
    text = re.sub(r"\A---\s*\n.*?\n---\s*(?:\n|$)", "", text, flags=re.S)
    text = re.sub(
        r"<table\b[^>]*>.*?</table>",
        lambda match: _html_table_to_markdown(match.group(0)),
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"```[^\n]*\n(.*?)```", lambda m: f"\n{m.group(1).strip()}\n", text, flags=re.S)
    text = re.sub(r"~~~[^\n]*\n(.*?)~~~", lambda m: f"\n{m.group(1).strip()}\n", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", lambda m: m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-+*]\s+", "• ", text, flags=re.M)
    text = re.sub(r"^\s*(\d+)[.)]\s+", r"\1. ", text, flags=re.M)
    text = re.sub(r"^(?!.*\|)\s*[-: ]+\s*$", "", text, flags=re.M)
    text = re.sub(r"</?[^>]+>", "", text)
    text = re.sub(r"(?<!\\)([*_~]{1,2})(.*?)\1", r"\2", text)
    text = text.replace("`", "")
    return html.unescape(text)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.rows.append(self.row)
            self.row = None


def _html_table_to_markdown(source: str) -> str:
    parser = _TableParser()
    parser.feed(source)
    rows = parser.rows
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "／") for cell in row) + " |"

    header = normalized[0]
    separator = ["---"] * width
    return "\n\n" + "\n".join([render(header), render(separator), *(render(row) for row in normalized[1:])]) + "\n\n"


def _parse_pdf(data: bytes, filename: str, progress_cb=None) -> str:
    from .mineru import MinerUError, parse_pdf

    try:
        markdown = parse_pdf(filename, data, progress_cb=progress_cb)
    except MinerUError as exc:
        raise DocumentParseError(str(exc)) from exc
    return _parse_markdown(markdown)


def _parse_docx(data: bytes) -> str:
    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise DocumentParseError("缺少 Word 解析依赖，请安装 requirements.txt") from exc

    document = Document(BytesIO(data))
    blocks: list[str] = []

    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style else "").lower()
            properties = paragraph._p.pPr
            has_numbering = properties is not None and properties.numPr is not None
            if "list" in style or has_numbering:
                text = f"• {text}"
            blocks.append(text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells = [_normalize_inline(cell.text) for cell in row.cells]
                if any(cells):
                    rows.append("；".join(cell for cell in cells if cell))
            if rows:
                blocks.append("\n".join(rows))

    return "\n\n".join(blocks)


def _normalize_inline(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]

    output: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if output and not blank:
                output.append("")
            blank = True
            continue
        output.append(line)
        blank = False
    return "\n".join(output).strip()


def _fragmented_line_ratio(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    fragments = sum(
        1 for line in lines
        if len(line) <= 2 and re.fullmatch(r"[\u3400-\u9fff，。！？、；：“”‘’（）《》]+", line)
    )
    return fragments / len(lines)
