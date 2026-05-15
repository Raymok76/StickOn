from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Command:
    id: str
    title: str
    handler: Callable[[dict[str, Any]], None]
    shortcut: str | None = None
    enabled_when: str | None = None
    palette: bool = True
    """When set, palette shows a leading tick when this returns True."""
    is_checked: Callable[[], bool] | None = None


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.id] = cmd

    def get(self, command_id: str) -> Command | None:
        return self._commands.get(command_id)

    def all(self) -> list[Command]:
        return list(self._commands.values())

    def execute(self, command_id: str, ctx: dict[str, Any]) -> None:
        cmd = self._commands.get(command_id)
        if not cmd:
            raise KeyError(f"Unknown command: {command_id}")
        cmd.handler(ctx)


def load_commands_from_json(path: str) -> list[dict[str, Any]]:
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("commands", []))


@dataclass
class CommandMetadataStore:
    """Static metadata from JSON; handlers are bound in app setup."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> CommandMetadataStore:
        return cls(entries=load_commands_from_json(path))

    def by_id(self) -> dict[str, dict[str, Any]]:
        return {e["id"]: e for e in self.entries if "id" in e}
