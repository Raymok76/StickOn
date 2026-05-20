from __future__ import annotations

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from stickon.scene.items.draw_item import DrawNodeItem
from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteNodeItem
from stickon.ui.canvas_view import CanvasView


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_new_image_stacks_above_existing_images() -> None:
    _app()
    canvas = CanvasView()
    scene = canvas.graphics_scene()

    first = ImageNodeItem(QPixmap(10, 10))
    first.setZValue(5.0)
    scene.addItem(first)

    second = ImageNodeItem(QPixmap(10, 10))
    scene.addItem(second)
    canvas._stack_new_image(second)

    assert second.zValue() > first.zValue()


def test_new_image_stays_below_notes_and_draws() -> None:
    _app()
    canvas = CanvasView()
    scene = canvas.graphics_scene()

    existing = ImageNodeItem(QPixmap(10, 10))
    existing.setZValue(2.0)
    scene.addItem(existing)

    note = NoteNodeItem("note")
    note.setZValue(8.0)
    scene.addItem(note)

    draw = DrawNodeItem()
    draw.setZValue(12.0)
    scene.addItem(draw)

    new_img = ImageNodeItem(QPixmap(10, 10))
    scene.addItem(new_img)
    canvas._stack_new_image(new_img)

    assert new_img.zValue() > existing.zValue()
    assert new_img.zValue() < note.zValue()
    assert new_img.zValue() < draw.zValue()
