from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMovie, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from stickon.models.node_models import NODE_DRAW, NODE_GROUP, NODE_IMAGE, NODE_NOTE
from stickon.scene.items.draw_item import DrawNodeItem
from stickon.scene.items.group_item import GroupNodeItem
from stickon.scene.items.image_item import ImageNodeItem
from stickon.scene.items.note_item import NoteNodeItem
from stickon.services.io_service import load_pur, save_pur


def _path_to_payload(path: QPainterPath, pen: QPen | None = None) -> dict[str, Any]:
    """Serialize path elements (move/line only). Uses elementAt — ElementIterator is not in all PySide6 builds."""
    els: list[dict[str, Any]] = []
    for i in range(path.elementCount()):
        e = path.elementAt(i)
        els.append(
            {
                "x": e.x,
                "y": e.y,
                "is_move": bool(e.isMoveTo()),
                "is_line": bool(e.isLineTo()),
            }
        )
    out: dict[str, Any] = {"elements": els}
    if pen is not None:
        c = pen.color()
        out["stroke"] = {
            "rgba": [c.red(), c.green(), c.blue(), c.alpha()],
            "width": float(pen.widthF()),
        }
    return out


def _path_from_payload(data: dict[str, Any]) -> QPainterPath:
    p = QPainterPath()
    for el in data.get("elements", []):
        x, y = float(el["x"]), float(el["y"])
        if el.get("is_move"):
            p.moveTo(x, y)
        elif el.get("is_line"):
            p.lineTo(x, y)
        else:
            if p.elementCount() == 0:
                p.moveTo(x, y)
            else:
                p.lineTo(x, y)
    return p


def _item_scale_xy(it) -> tuple[float, float]:
    t = it.transform()
    s = float(it.scale())
    return float(t.m11()) * s, float(t.m22()) * s


def _transform_payload(it: QGraphicsItem) -> dict[str, float]:
    """Persist uniform scale(), transform matrix, and origin — matches Qt's sceneTransform composition."""
    t = it.transform()
    op = it.transformOriginPoint()
    return {
        "item_scale": float(it.scale()),
        "tm11": float(t.m11()),
        "tm12": float(t.m12()),
        "tm21": float(t.m21()),
        "tm22": float(t.m22()),
        "origin_x": float(op.x()),
        "origin_y": float(op.y()),
    }


def _apply_transform_payload(it: QGraphicsItem, n: dict[str, Any]) -> None:
    """Restore scale/transform/origin; falls back to legacy scale_x/y + flip flags."""
    if "item_scale" in n and "tm11" in n:
        it.setTransformOriginPoint(float(n["origin_x"]), float(n["origin_y"]))
        it.setScale(max(1e-6, float(n.get("item_scale", 1) or 1)))
        it.setTransform(
            QTransform(
                float(n["tm11"]),
                float(n["tm12"]),
                float(n["tm21"]),
                float(n["tm22"]),
                0.0,
                0.0,
            )
        )
        return
    it.setTransformOriginPoint(0.0, 0.0)
    sx = float(n.get("scale_x", 1) or 1)
    sy = float(n.get("scale_y", 1) or 1)
    tr = QTransform()
    if n.get("flip_x"):
        sx = -sx
    if n.get("flip_y"):
        sy = -sy
    tr.scale(sx, sy)
    it.setTransform(tr)


def _persist_xy(it: QGraphicsItem, gid: str | None) -> tuple[float, float]:
    """Top-level items use pos() so transforms + origin round-trip; grouped items use scenePos() before addToGroup."""
    if gid is None:
        p = it.pos()
    else:
        p = it.scenePos()
    return float(p.x()), float(p.y())


def scene_to_pur_data(scene: QGraphicsScene) -> tuple[dict[str, Any], dict[str, bytes]]:
    nodes: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}

    persist_types = (ImageNodeItem, NoteNodeItem, DrawNodeItem, GroupNodeItem)
    stack_order = [
        it
        for it in scene.items(Qt.SortOrder.AscendingOrder)
        if isinstance(it, persist_types)
    ]
    z_top_level: dict[int, float] = {}
    for i, it in enumerate(
        x for x in stack_order if x.parentItem() is None
    ):
        z_top_level[id(it)] = float(i)

    for it in stack_order:
        gid = None
        par = it.parentItem()
        if par is not None and isinstance(par, GroupNodeItem):
            gid = par.node_id
        z_stored = z_top_level[id(it)] if it.parentItem() is None else float(it.zValue())
        if isinstance(it, ImageNodeItem):
            crop = None
            if it.crop_rect and it.crop_rect.isValid():
                cr = it.crop_rect
                crop = {"x": cr.x(), "y": cr.y(), "w": cr.width(), "h": cr.height()}
            px, py = _persist_xy(it, gid)
            payload: dict[str, Any] = {
                "source_path": it.source_path or "",
                "has_gif": it._movie is not None,
            }
            img = it.pixmap().toImage()
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            buf.close()
            key = f"img:{it.node_id}"
            blobs[key] = bytes(ba)
            node: dict[str, Any] = {
                "id": it.node_id,
                "type": NODE_IMAGE,
                "x": px,
                "y": py,
                "rotation": float(it.rotation()),
                "z_value": z_stored,
                "group_id": gid,
                "payload": payload,
            }
            node.update(_transform_payload(it))
            nodes.append(node)
        elif isinstance(it, NoteNodeItem):
            px, py = _persist_xy(it, gid)
            node_n: dict[str, Any] = {
                "id": it.node_id,
                "type": NODE_NOTE,
                "x": px,
                "y": py,
                "rotation": float(it.rotation()),
                "z_value": z_stored,
                "group_id": gid,
                "payload": {
                    "html": it.toHtml(),
                    "bg": [
                        it.bg_color.red(),
                        it.bg_color.green(),
                        it.bg_color.blue(),
                        it.bg_color.alpha(),
                    ],
                    "border_w": float(it.border_width),
                    "border": [
                        it.border_color.red(),
                        it.border_color.green(),
                        it.border_color.blue(),
                        it.border_color.alpha(),
                    ],
                },
            }
            node_n.update(_transform_payload(it))
            nodes.append(node_n)
        elif isinstance(it, DrawNodeItem):
            px, py = _persist_xy(it, gid)
            payload = _path_to_payload(it.path(), it.pen())
            if it.draw_layer_id:
                payload["draw_layer_id"] = str(it.draw_layer_id)
            if it.draw_layer_name:
                payload["draw_layer_name"] = str(it.draw_layer_name)
            nodes.append(
                {
                    "id": it.node_id,
                    "type": NODE_DRAW,
                    "x": px,
                    "y": py,
                    "rotation": float(it.rotation()),
                    "scale_x": 1.0,
                    "scale_y": 1.0,
                    "flip_x": False,
                    "flip_y": False,
                    "z_value": z_stored,
                    "group_id": gid,
                    "payload": payload,
                }
            )
        elif isinstance(it, GroupNodeItem):
            px, py = _persist_xy(it, gid)
            gn: dict[str, Any] = {
                "id": it.node_id,
                "type": NODE_GROUP,
                "x": px,
                "y": py,
                "rotation": float(it.rotation()),
                "z_value": z_stored,
                "group_id": gid,
                "payload": {},
            }
            gn.update(_transform_payload(it))
            nodes.append(gn)

    manifest = {"version": 1, "nodes": nodes, "grayscale": False}
    return manifest, blobs


def save_scene_to_path(
    scene: QGraphicsScene,
    file_path: str | Path,
    *,
    window_geometry: tuple[int, int, int, int] | None = None,
    view_state: dict[str, float] | None = None,
) -> None:
    manifest, blobs = scene_to_pur_data(scene)
    if window_geometry is not None:
        x, y, w, h = window_geometry
        manifest["window"] = {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
    if view_state is not None:
        manifest["view"] = {
            "m11": float(view_state["m11"]),
            "m12": float(view_state["m12"]),
            "m21": float(view_state["m21"]),
            "m22": float(view_state["m22"]),
            "center_x": float(view_state["center_x"]),
            "center_y": float(view_state["center_y"]),
        }
    save_pur(file_path, manifest, blobs)


def load_scene_from_path(scene: QGraphicsScene, file_path: str | Path) -> dict[str, Any]:
    manifest, blobs = load_pur(file_path)
    scene.clear()
    node_map: dict[str, Any] = {}

    groups_first = [n for n in manifest.get("nodes", []) if n.get("type") == NODE_GROUP]
    rest = [n for n in manifest.get("nodes", []) if n.get("type") != NODE_GROUP]
    ordered = groups_first + rest

    for n in ordered:
        nid = str(n["id"])
        t = n["type"]
        pos = QPointF(float(n.get("x", 0)), float(n.get("y", 0)))
        rot = float(n.get("rotation", 0))
        z = float(n.get("z_value", 0))

        item = None
        if t == NODE_IMAGE:
            key = f"img:{nid}"
            data = blobs.get(key, b"")
            pm = QPixmap()
            if data:
                pm.loadFromData(data, "PNG")
            if pm.isNull():
                pm = QPixmap(32, 32)
                pm.fill(Qt.GlobalColor.darkGray)
            it = ImageNodeItem(pm, nid)
            it.setRotation(rot)
            _apply_transform_payload(it, n)
            it.setPos(pos)
            crop = n.get("crop")
            if crop:
                it.set_crop_rect(
                    QRectF(float(crop["x"]), float(crop["y"]), float(crop["w"]), float(crop["h"]))
                )
            pl = n.get("payload") or {}
            it.source_path = pl.get("source_path") or None
            if pl.get("has_gif") and it.source_path:
                it.set_gif_movie(QMovie(it.source_path, parent=scene))
            item = it
        elif t == NODE_NOTE:
            pl = n.get("payload") or {}
            it = NoteNodeItem("", nid)
            it.setHtml(pl.get("html", "<p>Note</p>"))
            bg = pl.get("bg")
            if bg and len(bg) >= 3:
                it.set_background_color(
                    QColor(int(bg[0]), int(bg[1]), int(bg[2]), int(bg[3]) if len(bg) > 3 else 255)
                )
            bw = pl.get("border_w")
            if bw is not None:
                try:
                    it.border_width = max(0.0, float(bw))
                except (TypeError, ValueError):
                    pass
            bcol = pl.get("border")
            if isinstance(bcol, list) and len(bcol) >= 3:
                it.border_color = QColor(
                    int(bcol[0]),
                    int(bcol[1]),
                    int(bcol[2]),
                    int(bcol[3]) if len(bcol) > 3 else 255,
                )
            it.setRotation(rot)
            _apply_transform_payload(it, n)
            it.setPos(pos)
            item = it
        elif t == NODE_DRAW:
            pl = n.get("payload") or {}
            pit = DrawNodeItem(_path_from_payload(pl), nid)
            lid = pl.get("draw_layer_id")
            pit.draw_layer_id = str(lid) if isinstance(lid, str) and lid.strip() else None
            lname = pl.get("draw_layer_name")
            pit.draw_layer_name = str(lname) if isinstance(lname, str) and lname.strip() else None
            pen = pit.pen()
            st = pl.get("stroke")
            if isinstance(st, dict):
                rgba = st.get("rgba")
                if isinstance(rgba, list) and len(rgba) >= 3:
                    pen.setColor(
                        QColor(
                            int(rgba[0]),
                            int(rgba[1]),
                            int(rgba[2]),
                            int(rgba[3]) if len(rgba) > 3 else 255,
                        )
                    )
                w = st.get("width")
                if w is not None:
                    try:
                        pen.setWidthF(max(0.25, float(w)))
                    except (TypeError, ValueError):
                        pass
            pit.setPen(pen)
            pit.setPos(pos)
            pit.setRotation(rot)
            item = pit
        elif t == NODE_GROUP:
            git = GroupNodeItem(nid)
            git.setRotation(rot)
            _apply_transform_payload(git, n)
            git.setPos(pos)
            item = git

        if item is not None:
            item.setZValue(z)
            scene.addItem(item)
            node_map[nid] = item

    for n in manifest.get("nodes", []):
        gid = n.get("group_id")
        nid = str(n["id"])
        if not gid or nid not in node_map or gid not in node_map:
            continue
        child = node_map[nid]
        parent = node_map[gid]
        if isinstance(parent, GroupNodeItem) and child is not None and child is not parent:
            parent.addToGroup(child)

    return manifest


def sample_color_at_global(x: int, y: int) -> str:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return "#000000"
    pix = screen.grabWindow(0, x, y, 1, 1)
    c = pix.toImage().pixelColor(0, 0)
    return c.name()
