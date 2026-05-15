from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from stickon.models.node_models import NodeModel


@dataclass
class ProjectModel:
    version: int = 1
    nodes: list[NodeModel] = field(default_factory=list)
    scene_rect: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "nodes": [n.to_dict() for n in self.nodes],
            "scene_rect": self.scene_rect,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProjectModel:
        nodes = [NodeModel.from_dict(x) for x in d.get("nodes", [])]
        return cls(
            version=int(d.get("version", 1)),
            nodes=nodes,
            scene_rect=d.get("scene_rect"),
        )
