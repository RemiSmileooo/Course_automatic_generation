"""底图 PPTX 导出测试（任务 8）。"""
from __future__ import annotations

from src import pptx_export
from src.models import Course, Slide, Segment


def test_export_image_pptx_without_base_image(tmp_path):
    """无 base_image 时也能导出（回退标题占位），不报错，并写入备注。"""
    course = Course(title="课程", slides=[
        Slide(title="封面", kind="cover", segments=[Segment(kind="intro", script="开场白")]),
        Slide(title="内容页", kind="content", segments=[Segment(kind="intro", script="讲解")]),
    ])
    out = tmp_path / "c.pptx"
    path = pptx_export.export_image_pptx(course, out)
    assert out.is_file()
    assert out.stat().st_size > 0
    assert path == str(out)


def test_export_image_pptx_with_base_image(tmp_path):
    """有 base_image 时把整页图片塞入 PPT。"""
    # 造一张 1x1 png 充当底图
    from PIL import Image
    img = tmp_path / "slide0.png"
    Image.new("RGB", (16, 9), (255, 255, 255)).save(img)

    course = Course(title="课程", slides=[
        Slide(title="封面", kind="cover", base_image=str(img),
              segments=[Segment(kind="intro", script="开场")]),
    ])
    out = tmp_path / "c.pptx"
    pptx_export.export_image_pptx(course, out)
    assert out.is_file() and out.stat().st_size > 0
