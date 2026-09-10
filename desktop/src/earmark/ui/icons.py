"""Icons drawn at runtime, so the app ships without image files."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap

_ACCENT = QColor("#4f8cff")
_IDLE = QColor("#8a8f98")


def speaker_icon(size: int = 64, active: bool = True) -> QIcon:
    """A speaker with sound waves. Filled when playing, grey when idle."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = _ACCENT if active else _IDLE
    painter.setBrush(QBrush(colour))
    painter.setPen(Qt.NoPen)

    unit = size / 64.0
    body = QPainterPath()
    body.moveTo(14 * unit, 25 * unit)
    body.lineTo(22 * unit, 25 * unit)
    body.lineTo(32 * unit, 15 * unit)
    body.lineTo(32 * unit, 49 * unit)
    body.lineTo(22 * unit, 39 * unit)
    body.lineTo(14 * unit, 39 * unit)
    body.closeSubpath()
    painter.drawPath(body)

    pen = painter.pen()
    pen.setColor(colour)
    pen.setWidthF(3.2 * unit)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    for i, radius in enumerate((10, 17)):
        if not active and i:
            break
        rect = QRectF((32 - radius) * unit, (32 - radius) * unit,
                      radius * 2 * unit, radius * 2 * unit)
        painter.drawArc(rect, -55 * 16, 110 * 16)
    painter.end()
    return QIcon(pixmap)


def glyph_icon(kind: str, size: int = 32) -> QIcon:
    """Small transport glyphs for the mini player buttons."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QBrush(QColor("#e8eaed")))
    painter.setPen(Qt.NoPen)
    u = size / 32.0

    if kind == "play":
        path = QPainterPath()
        path.moveTo(11 * u, 8 * u)
        path.lineTo(24 * u, 16 * u)
        path.lineTo(11 * u, 24 * u)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "pause":
        painter.drawRoundedRect(QRectF(10 * u, 8 * u, 4 * u, 16 * u), u, u)
        painter.drawRoundedRect(QRectF(18 * u, 8 * u, 4 * u, 16 * u), u, u)
    elif kind == "stop":
        painter.drawRoundedRect(QRectF(10 * u, 10 * u, 12 * u, 12 * u), u, u)
    elif kind in ("next", "prev"):
        path = QPainterPath()
        if kind == "next":
            path.moveTo(9 * u, 8 * u)
            path.lineTo(19 * u, 16 * u)
            path.lineTo(9 * u, 24 * u)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawRoundedRect(QRectF(20 * u, 8 * u, 3 * u, 16 * u), u, u)
        else:
            path.moveTo(23 * u, 8 * u)
            path.lineTo(13 * u, 16 * u)
            path.lineTo(23 * u, 24 * u)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawRoundedRect(QRectF(9 * u, 8 * u, 3 * u, 16 * u), u, u)
    painter.end()
    return QIcon(pixmap)
