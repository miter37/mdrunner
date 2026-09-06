"""Hand-drawn monochrome line icons (no icon-font dependency).

Each icon is stroked on a 24x24 grid with a 2px round pen in the caller's
colour, so it matches whatever theme is active. Cached per (name, colour).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_CACHE: dict[tuple[str, str], QIcon] = {}


def _poly(painter: QPainter, pts: list[tuple[float, float]], close: bool = False) -> None:
    path = QPainterPath()
    path.moveTo(*pts[0])
    for x, y in pts[1:]:
        path.lineTo(x, y)
    if close:
        path.closeSubpath()
    painter.drawPath(path)


def _draw(name: str, painter: QPainter) -> None:
    if name == "add":
        painter.drawLine(12, 5, 12, 19)
        painter.drawLine(5, 12, 19, 12)
    elif name == "edit":
        _poly(painter, [(5, 19), (5, 15), (15, 5), (19, 9), (9, 19)], close=True)
        painter.drawLine(13, 7, 17, 11)
    elif name == "delete":
        painter.drawLine(5, 7, 19, 7)
        painter.drawLine(10, 7, 10, 5)
        painter.drawLine(14, 7, 14, 5)
        painter.drawLine(10, 5, 14, 5)
        _poly(painter, [(6, 7), (7, 20), (17, 20), (18, 7)])
        painter.drawLine(10, 10, 10, 17)
        painter.drawLine(14, 10, 14, 17)
    elif name == "run":
        _poly(painter, [(7, 5), (19, 12), (7, 19)], close=True)
    elif name == "toggle":
        painter.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 240 * 16)
        painter.drawLine(12, 4, 12, 12)
    elif name == "settings":
        for y in (7, 12, 17):
            painter.drawLine(5, y, 19, y)
        for x, y in ((9, 7), (15, 12), (10, 17)):
            painter.drawEllipse(QPointF(x, y), 2.1, 2.1)
    elif name == "refresh":
        painter.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 260 * 16)
        painter.drawLine(18, 4, 18, 9)
        painter.drawLine(18, 9, 13, 9)
    elif name == "health":
        _poly(painter, [(4, 12), (9, 12), (11, 7), (14, 17), (16, 12), (20, 12)])
    elif name == "quota":
        painter.drawArc(QRectF(5, 5, 14, 14), 0, 360 * 16)
        painter.drawLine(12, 12, 12, 6)
        painter.drawLine(12, 12, 17, 14)
    elif name == "preview":
        painter.drawEllipse(QRectF(4, 7, 16, 10))
        painter.drawEllipse(QPointF(12, 12), 2.4, 2.4)


def icon(name: str, color: str, size: int = 40) -> QIcon:
    key = (name, color)
    if key in _CACHE:
        return _CACHE[key]
    pm = QPixmap(size, size)
    pm.setDevicePixelRatio(1)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)
    pen = QPen(QColor(color))
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    _draw(name, painter)
    painter.end()
    ico = QIcon(pm)
    _CACHE[key] = ico
    return ico


def clear_cache() -> None:
    _CACHE.clear()
