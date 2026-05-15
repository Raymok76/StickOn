from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem, QGraphicsItemGroup

from stickon.scene.items.image_item import new_node_id


class GroupNodeItem(QGraphicsItemGroup):
    def __init__(self, node_id: str | None = None) -> None:
        super().__init__()
        self.node_id = node_id or new_node_id()
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges,
        )
