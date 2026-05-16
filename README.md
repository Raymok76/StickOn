# StickOn

**StickOn** is a lightweight, Windows-first **desktop reference board**. Drop images onto a frameless canvas, arrange them like stickers on glass, add handwritten-style notes and quick sketches, then leave the window floating above your work—or let mouse events pass through when you only need the picture as a visual anchor.

Built with **PySide6 (Qt 6)** and installed as a small Python package so you can run it with **`uv`** without touching a global Python install.

---

## Preview

![StickOn canvas with mixed reference images and notes](assets/preview-main.png)

---

## Features

- **Images as first-class citizens** — Drag files from Explorer or paste from the clipboard (`Ctrl+V`: bitmap or a file path). On Windows, grab a screenshot with the built-in tools (**Win+Shift+S**, Snipping Tool, **Print Screen**, or any capture that lands on the clipboard), then switch to StickOn and press **Ctrl+V** to paste the shot directly onto the canvas—no saving a file first. GIFs animate; corner handles resize; crops, rotate, scale, and flip gestures stay on the canvas.
- **Notes** — Add notes with `Ctrl+N`, or double-click canvas to place one under the pointer, or edit inline by double-clicking a note. Resize handles scale text with the card.
- **Layout helpers** — Pack images into the viewport, align selections, and group nodes when you need them to move together.
- **Window that behaves like a tool** — Always on top or bottom, adjustable opacity, optional **click-through** (`Ctrl+T`) so you can see through to apps underneath (Windows uses precise hit-testing so the title bar and resize rim stay clickable).
- **Session memory** — Closing saves layout: window position and size, zoom/view state when configured, and each node’s transforms. Reopening restores your last board (`StickOn — last session`).
- **Command key** — `Ctrl+Shift+P` opens the hotkey settings for discoverable actions and shortcuts.

Drawing mode, scene export, undo/redo, and GIF playback controls round out the workflow for quick visual references—not a full image editor, but a fast overlay for design, 3D, coding, or teaching side-by-side with another window.

## Main Functions and Hotkeys

- `Always on Top` (`Ctrl+Shift+A`): Modifies the z-order to force the application to render above all other active Windows applications.
- `Always on Bottom` (`Ctrl+Shift+B`): Drops the window z-order to the desktop level, functioning as an interactive workspace wallpaper.
- `Click Through` (`Ctrl+T`): Modifies window flags to ignore all mouse-click events, passing them to the application beneath it. Highly utilized for tracing or modeling over references.
- `Overlay Selection` (`Ctrl+O`): Makes the canvas background transparent and opens a borderless, always-on-top window per **selected** image, as large as possible within the current screen’s available (work) area. Focus stays on the StickOn canvas so `Escape` or other shortcuts work immediately. With overlays open, `Escape` or `Ctrl+O` closes them and restores the canvas.
- `Application Opacity` (`Ctrl+Shift++` or `Ctrl+Wheel Up` / `Ctrl+Shift+-` or `Ctrl+Wheel down`): Adjusts the alpha transparency of the entire application window against the Windows desktop.
- `Lock Window` (`Ctrl+W`): Freezes the window coordinates on the monitor to prevent accidental movement.

### Alignments
- `Pack Optimal` (`Ctrl+P`): Algorithmic spatial packing of selected image nodes to eliminate empty space.
- `Align` (`Ctrl+Arrow Keys`): Snaps node bounding boxes to their respective axes.
- `Scale/Resize`: `Left Mouse Drag`　at the borders or corners.
- `Rotate`: `Ctrl + Left Mouse Drag` (Add `Shift` to snap to 45-degree increments).
- `Crop`: `Hold C + Left Mouse Drag` (Draws a non-destructive bounding box mask).

### Annotation & Markup Tools

- `Notes` (`Ctrl+N` or `Double-Click`): Instantiates a text node object. Supports rich text and background color modification.
- `Draw Mode` (`Ctrl+Shift+D`): Converts the cursor into a markup tool to draw lines, shapes (rectangles/circles), and arrows directly onto the canvas or over image nodes.
- `GIF Playback`: Supports loading and continuous looping of `.gif` files, with capabilities to pause, scrub frames, and extract static frames.

### File I/O & Persistence

- `Save Format`: `.sti` (Proprietary binary file). Stores layout coordinates, hierarchy, and embeds or links image binary data.
- `Export Actions`: `Ctrl+E` (Export Scene) or `Ctrl+Shift+I` (Export Selected Images) to rasterize the canvas or extract nodes to standard formats (PNG, BMP, JPG) on the local file system.


---

## Setup

Use [uv](https://docs.astral.sh/uv/) and install dependencies (including dev tools like pytest):

```bash
uv sync --group dev
```

The project is installed in editable mode so imports like `stickon` work under **`uv run`**.

---

## Run

Prefer **`uv run`** so you use this repo’s virtual environment:

```bash
uv run stickon
```

or:

```bash
uv run python -m stickon.main
```

---

## Tests

```bash
uv run pytest
```

---

## Notes

- On first launch, StickOn shows a short click-through safety reminder (`Ctrl+T` / **Escape** to recover pointer hits).
- Clipboard paste: **`Ctrl+V`** (bitmap or image file path).
- Right-click the canvas for a compact menu (pack, alignment submenu, notes, draw mode, export, and more).
