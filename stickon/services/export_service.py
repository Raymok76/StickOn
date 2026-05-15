from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QGraphicsScene


class ExportService:
    @staticmethod
    def export_scene(scene: QGraphicsScene, path: str | Path, rect: QRectF | None = None) -> None:
        path = Path(path)
        suffix = path.suffix.lower()
        as_jpeg = suffix in (".jpg", ".jpeg")
        target = rect or scene.itemsBoundingRect().adjusted(-10, -10, 10, 10)
        w = max(1, int(target.width()))
        h = max(1, int(target.height()))
        if as_jpeg:
            image = QImage(w, h, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
        else:
            image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(p, target=QRectF(0, 0, w, h), source=target)
        p.end()
        path.parent.mkdir(parents=True, exist_ok=True)
        if as_jpeg:
            image.save(str(path), "JPEG", 92)
        else:
            image.save(str(path), "PNG")
