"""Custom painting for the task grid — the status-pill and the two-line
name cell that give the window its operations-console read."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import theme

# item data slots
SUBTITLE_ROLE = Qt.ItemDataRole.UserRole + 2   # str shown dim under the name
STATUS_ROLE = Qt.ItemDataRole.UserRole + 3     # ("ok"|"err"|"running"|"idle"|"disabled", label)


class StatusPillDelegate(QStyledItemDelegate):
    """Renders a cell as a small dot + label pill in a semantic colour."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pal = theme.LIGHT

    def set_palette(self, p: dict) -> None:
        self._pal = p

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(self._pal["sel"]))
        data = index.data(STATUS_ROLE)
        kind, label = data if data else ("idle", index.data() or "—")
        col = QColor(theme.status_color(self._pal, kind))

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        f: QFont = option.font
        f.setPointSizeF(max(8.0, f.pointSizeF() - 0.5))
        painter.setFont(f)
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(label)
        dot = 7
        pad = 9
        gap = 6
        pill_w = pad + dot + gap + tw + pad
        pill_h = fm.height() + 6
        r = option.rect
        x = r.x() + 10
        y = r.y() + (r.height() - pill_h) / 2
        rect = QRectF(x, y, pill_w, pill_h)

        bg = QColor(col)
        bg.setAlpha(28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, pill_h / 2, pill_h / 2)

        painter.setBrush(col)
        painter.drawEllipse(QRectF(x + pad, y + (pill_h - dot) / 2, dot, dot))

        painter.setPen(col)
        painter.drawText(
            QRectF(x + pad + dot + gap, y, tw + 4, pill_h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            label,
        )
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 30))
        return s


class TwoLineDelegate(QStyledItemDelegate):
    """Name on top, a dim monospace subtitle (id · prompt file) beneath."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pal = theme.LIGHT

    def set_palette(self, p: dict) -> None:
        self._pal = p

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(self._pal["sel"]))

        r = option.rect.adjusted(12, 4, -8, -4)
        name = index.data() or ""
        subtitle = index.data(SUBTITLE_ROLE) or ""
        disabled = not (option.state & QStyle.StateFlag.State_Enabled)

        name_f: QFont = QFont(option.font)
        name_f.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_f)
        painter.setPen(QColor(self._pal["text_dim"] if disabled else self._pal["text"]))
        fm = QFontMetrics(name_f)
        painter.drawText(
            r.left(), r.top(), r.width(), fm.height(),
            Qt.AlignmentFlag.AlignVCenter, fm.elidedText(name, Qt.TextElideMode.ElideRight, r.width()),
        )

        if subtitle:
            sub_f = theme.mono_font(9)
            painter.setFont(sub_f)
            painter.setPen(QColor(self._pal["text_faint"]))
            sfm = QFontMetrics(sub_f)
            painter.drawText(
                r.left(), r.top() + fm.height() - 1, r.width(), sfm.height() + 2,
                Qt.AlignmentFlag.AlignVCenter,
                sfm.elidedText(subtitle, Qt.TextElideMode.ElideMiddle, r.width()),
            )
        painter.restore()

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 44))
        return s
