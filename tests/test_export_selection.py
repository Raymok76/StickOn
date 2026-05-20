from __future__ import annotations

import os
import tempfile

from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsScene

from stickon.scene.items.image_item import ImageNodeItem
from stickon.services.export_service import ExportService

_SELECTION_BLUE = QColor(80, 160, 255)


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _contains_color(img: QImage, color: QColor) -> bool:
    for y in range(img.height()):
        for x in range(img.width()):
            if img.pixelColor(x, y) == color:
                return True
    return False


def test_export_selected_image_omits_selection_border() -> None:
    _app()
    scene = QGraphicsScene()
    pm = QPixmap(80, 80)
    pm.fill(QColor(210, 50, 50))
    item = ImageNodeItem(pm)
    scene.addItem(item)
    item.setSelected(True)

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        ExportService.export_item_selection(scene, path, [item])
        exported = QImage(path)
        assert not _contains_color(exported, _SELECTION_BLUE)
        assert item.isSelected()
    finally:
        os.unlink(path)
