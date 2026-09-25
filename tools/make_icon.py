# -*- coding: utf-8 -*-
"""生成 assets/icon.ico（多尺寸）。

为什么用 Qt 画而不是 Pillow：本项目本来就依赖 PySide6，这样**不新增任何依赖**，
脚本在任何装好运行依赖的机器上都能重跑，图标也就不再是"只存在于某个二进制里的
唯一真相"。

小尺寸（≤24px）单独画简化版再放大抗锯齿，不要靠 256px 等比缩小——缩到 16px
只会糊成一团。

用法：python tools/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.console import force_utf8_stdout  # noqa: E402

force_utf8_stdout()

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QBrush, QColor, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPen)

# 与界面主题一致的品牌色
BG_TOP = "#4a9fd8"
BG_BOTTOM = "#1f5c96"
FG = "#ffffff"
ACCENT = "#8ae6a1"

SIZES = [16, 20, 24, 32, 48, 64, 128, 256]
SUPERSAMPLE = 4


def _draw_background(p: QPainter, size: float, radius: float) -> None:
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0.0, QColor(BG_TOP))
    g.setColorAt(1.0, QColor(BG_BOTTOM))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.fillPath(path, QBrush(g))


def _draw_simplified(p: QPainter, size: float) -> None:
    """小尺寸版：只有一个粗壮的勾，细节全部砍掉。"""
    pen = QPen(QColor(FG))
    pen.setWidthF(size * 0.16)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([
        QPointF(size * 0.24, size * 0.54),
        QPointF(size * 0.43, size * 0.72),
        QPointF(size * 0.78, size * 0.29),
    ])


def _draw_tag(p: QPainter, size: float) -> None:
    """大尺寸版：白色卡片 + 折角 + 绿色对勾（一眼看出"批处理通过"）。"""
    m = size * 0.19
    w = size - 2 * m
    cut = size * 0.24
    card = QPainterPath()
    card.moveTo(m, m)
    card.lineTo(m + w - cut, m)
    card.lineTo(m + w, m + cut)
    card.lineTo(m + w, m + w)
    card.lineTo(m, m + w)
    card.closeSubpath()

    p.setPen(Qt.NoPen)
    p.fillPath(card, QBrush(QColor(FG)))

    # 折角用深一点的颜色区分，避免整体像一张纯白纸
    fold = QPainterPath()
    fold.moveTo(m + w - cut, m)
    fold.lineTo(m + w, m + cut)
    fold.lineTo(m + w - cut, m + cut)
    fold.closeSubpath()
    p.fillPath(fold, QBrush(QColor("#c9d8e8")))

    pen = QPen(QColor("#1e8449"))
    pen.setWidthF(size * 0.115)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline([
        QPointF(size * 0.31, size * 0.58),
        QPointF(size * 0.44, size * 0.71),
        QPointF(size * 0.72, size * 0.39),
    ])


def render(size: int) -> QImage:
    """渲染单个尺寸（先超采样再降采样，保证边缘平滑）。"""
    ss = size * SUPERSAMPLE
    img = QImage(ss, ss, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    _draw_background(p, ss, ss * 0.22)
    if size <= 24:
        _draw_simplified(p, ss)
    else:
        _draw_tag(p, ss)
    p.end()

    return img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(payloads: list[tuple[int, bytes]], out: Path) -> None:
    """手写 ICO 容器（Vista+ 支持 PNG 压缩条目，所以无需 BMP 那一套）。"""
    count = len(payloads)
    header = struct.pack("<HHH", 0, 1, count)          # reserved=0, type=1(icon), count
    entries = b""
    offset = 6 + 16 * count
    for size, data in payloads:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # 256 记作 0
            size if size < 256 else 0,
            0,                            # 调色板数
            0,                            # reserved
            1,                            # color planes
            32,                           # bits per pixel
            len(data),
            offset,
        )
        offset += len(data)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + entries + b"".join(d for _s, d in payloads))


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - QPainter 需要
    out = ROOT / "assets" / "icon.ico"
    payloads = []
    for size in SIZES:
        img = render(size)
        payloads.append((size, png_bytes(img)))
        print(f"  渲染 {size}x{size}  {len(payloads[-1][1])} 字节")
    write_ico(payloads, out)

    # 同时导出 256px PNG，README / GitHub 社交预览可用
    render(256).save(str(ROOT / "assets" / "icon.png"), "PNG")
    print(f"\n已生成 {out}（{out.stat().st_size} 字节，{len(payloads)} 个尺寸）")
    print(f"已生成 {ROOT / 'assets' / 'icon.png'}")

    # 回读校验：ICO 头必须合法
    raw = out.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert (reserved, kind, count) == (0, 1, len(SIZES)), (reserved, kind, count)
    print("回读校验通过：ICO 头合法。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
