from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeModel:
    id: str
    type: str
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    flip_x: bool = False
    flip_y: bool = False
    z_value: float = 0.0
    crop: dict[str, float] | None = None  # x,y,w,h in item coords
    group_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "rotation": self.rotation,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
            "flip_x": self.flip_x,
            "flip_y": self.flip_y,
            "z_value": self.z_value,
            "group_id": self.group_id,
            "payload": self.payload,
        }
        if self.crop:
            d["crop"] = self.crop
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeModel:
        c = d.get("crop")
        return cls(
            id=str(d["id"]),
            type=str(d["type"]),
            x=float(d.get("x", 0)),
            y=float(d.get("y", 0)),
            rotation=float(d.get("rotation", 0)),
            scale_x=float(d.get("scale_x", 1)),
            scale_y=float(d.get("scale_y", 1)),
            flip_x=bool(d.get("flip_x", False)),
            flip_y=bool(d.get("flip_y", False)),
            z_value=float(d.get("z_value", 0)),
            crop=dict(c) if c else None,
            group_id=d.get("group_id"),
            payload=dict(d.get("payload", {})),
        )


NODE_IMAGE = "image"
NODE_NOTE = "note"
NODE_DRAW = "draw"
NODE_GROUP = "group"
