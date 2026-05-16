from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QGraphicsScene

from stickon.scene.items.image_item import ImageNodeItem
from stickon.services.project_service import _item_scale_xy, load_scene_from_path, save_scene_to_path


def test_sti_image_roundtrip() -> None:
    if QApplication.instance() is None:
        QApplication([])
    scene = QGraphicsScene()
    pm = QPixmap(8, 8)
    pm.fill(QColor("red"))
    it = ImageNodeItem(pm)
    it.setPos(12.0, 34.0)
    it.setScale(2.0)
    scene.addItem(it)
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "test.sti"
        save_scene_to_path(scene, fp)
        out = QGraphicsScene()
        load_scene_from_path(out, fp)
        imgs = [x for x in out.items() if isinstance(x, ImageNodeItem)]
        assert len(imgs) == 1
        assert abs(imgs[0].scenePos().x() - 12.0) < 0.01
        assert abs(imgs[0].scenePos().y() - 34.0) < 0.01
        sx, sy = _item_scale_xy(imgs[0])
        assert abs(sx - 2.0) < 0.02 and abs(sy - 2.0) < 0.02


def test_sti_window_geometry_roundtrip() -> None:
    if QApplication.instance() is None:
        QApplication([])
    scene = QGraphicsScene()
    pm = QPixmap(4, 4)
    pm.fill(QColor("blue"))
    it = ImageNodeItem(pm)
    scene.addItem(it)
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "w.sti"
        save_scene_to_path(scene, fp, window_geometry=(11, 22, 900, 700))
        out = QGraphicsScene()
        manifest = load_scene_from_path(out, fp)
        assert manifest.get("window") == {"x": 11, "y": 22, "w": 900, "h": 700}


def test_sti_canvas_view_state_roundtrip() -> None:
    if QApplication.instance() is None:
        QApplication([])
    scene = QGraphicsScene()
    pm = QPixmap(4, 4)
    pm.fill(QColor("yellow"))
    it = ImageNodeItem(pm)
    scene.addItem(it)
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "view.sti"
        view = {"m11": 1.5, "m12": 0.0, "m21": 0.0, "m22": 1.5, "center_x": 55.0, "center_y": 77.0}
        save_scene_to_path(scene, fp, view_state=view)
        out = QGraphicsScene()
        manifest = load_scene_from_path(out, fp)
        assert manifest.get("view") == view


def test_sti_image_scale_origin_roundtrip() -> None:
    if QApplication.instance() is None:
        QApplication([])
    scene = QGraphicsScene()
    pm = QPixmap(10, 10)
    pm.fill(QColor("green"))
    it = ImageNodeItem(pm)
    br = it.pixmapBoundingRect()
    oc = br.center()
    it.setTransformOriginPoint(oc.x(), oc.y())
    it.setScale(2.0)
    it.setPos(33.0, 44.0)
    scene.addItem(it)
    w_expect = it.mapRectToScene(it.pixmapBoundingRect()).width()
    pos_expect = it.scenePos()
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "scale.sti"
        save_scene_to_path(scene, fp)
        out = QGraphicsScene()
        load_scene_from_path(out, fp)
        imgs = [x for x in out.items() if isinstance(x, ImageNodeItem)]
        assert len(imgs) == 1
        ld = imgs[0]
        sx, sy = _item_scale_xy(ld)
        assert abs(sx - 2.0) < 0.02 and abs(sy - 2.0) < 0.02
        scene_w = ld.mapRectToScene(ld.pixmapBoundingRect()).width()
        assert abs(scene_w - w_expect) < 0.5
        assert (ld.scenePos() - pos_expect).manhattanLength() < 0.5


def test_sti_image_crop_roundtrip() -> None:
    if QApplication.instance() is None:
        QApplication([])
    scene = QGraphicsScene()
    pm = QPixmap(20, 20)
    pm.fill(QColor("magenta"))
    it = ImageNodeItem(pm)
    it.set_crop_rect(QRectF(2.0, 3.0, 7.0, 8.0))
    scene.addItem(it)
    with tempfile.TemporaryDirectory() as d:
        fp = Path(d) / "crop.sti"
        save_scene_to_path(scene, fp)
        out = QGraphicsScene()
        load_scene_from_path(out, fp)
        imgs = [x for x in out.items() if isinstance(x, ImageNodeItem)]
        assert len(imgs) == 1
        loaded_crop = imgs[0].crop_rect
        assert loaded_crop is not None
        assert abs(loaded_crop.x() - 2.0) < 0.01
        assert abs(loaded_crop.y() - 3.0) < 0.01
        assert abs(loaded_crop.width() - 7.0) < 0.01
        assert abs(loaded_crop.height() - 8.0) < 0.01
