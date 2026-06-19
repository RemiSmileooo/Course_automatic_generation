"""冒烟测试：确认 pytest 可运行，且 src 轻量模块可导入。"""
from __future__ import annotations


def test_import_src_package():
    import src  # noqa: F401


def test_import_lightweight_modules():
    from src import config, models  # noqa: F401

    assert hasattr(config, "settings")
    assert hasattr(models, "Course")
