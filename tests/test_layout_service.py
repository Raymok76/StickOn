from __future__ import annotations

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from stickon.scene.items.image_item import ImageNodeItem
from stickon.services.layout_service import LayoutService


def test_pack_positions_items() -> None:
    if QApplication.instance() is None:
        QApplication([])
    a = ImageNodeItem(QPixmap(10, 20))
    b = ImageNodeItem(QPixmap(30, 10))
    LayoutService(spacing=0).pack_optimal([a, b])
    assert abs(a.pos().x()) < 2.0
    assert b.pos().x() >= 8.0


def test_pack_keeps_left_to_right_order() -> None:
    """Taller-right image must not be shelved before shorter-left (no horizontal swaps)."""
    if QApplication.instance() is None:
        QApplication([])
    left = ImageNodeItem(QPixmap(20, 10))
    right = ImageNodeItem(QPixmap(10, 40))
    left.setPos(0, 0)
    right.setPos(100, 0)
    svc = LayoutService(spacing=4)
    svc.pack_optimal([left, right])
    assert svc._pixmap_scene_rect(left).center().x() < svc._pixmap_scene_rect(right).center().x()


def test_align_left() -> None:
    if QApplication.instance() is None:
        QApplication([])
    a = ImageNodeItem(QPixmap(10, 10))
    b = ImageNodeItem(QPixmap(10, 10))
    b.setPos(50, 3)
    LayoutService().align([a, b], "left")
    assert abs(a.pos().x() - b.pos().x()) < 1e-6
