from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QStyleOptionGraphicsItem, QWidget

from stickon.scene.items.image_item import new_node_id


class DrawNodeItem(QGraphicsPathItem):
    """Markup stroke or shape (line/rect/ellipse/arrow) stored as path."""

    def __init__(self, path: QPainterPath | None = None, node_id: str | None = None) -> None:
        super().__init__(path or QPainterPath())
        self.node_id = node_id or new_node_id()
        # Draw-mode session identifier; strokes from one draw session share a layer id.
        self.draw_layer_id: str | None = None
        self.draw_layer_name: str | None = None
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges,
        )
        pen = QPen(QColor(255, 60, 60), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        self.setPen(pen)

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        super().paint(painter, option, widget)
        if self.isSelected():
            p = QPen(QColor(80, 160, 255), 1, Qt.PenStyle.DashLine)
            painter.setPen(p)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect())
