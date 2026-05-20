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

---

## Supported image formats

| Group | Extensions |
|-------|------------|
| **Common** | `.png`, `.jpg`, `.jpeg`, `.jfif`, `.bmp`, `.dib`, `.gif`, `.webp`, `.tif`, `.tiff` |
| **Icons & portable** | `.ico`, `.cur`, `.xbm`, `.xpm` |
| **Modern still** | `.avif`, `.avifs`, `.heic`, `.heif`, `.hif`, `.jxl`, `.jp2`, `.j2k`, `.jpc`, `.jpx`, `.qoi` |
| **Netpbm / similar** | `.pbm`, `.pgm`, `.ppm`, `.pnm`, `.pam`, `.pfm` |
| **Design / 3D** | `.psd`, `.psb`, `.svg`, `.svgz`, `.tga`, `.dds`, `.exr` |
| **Documents** | `.pdf` (each page becomes its own reference image) |
| **Camera RAW** | `.arw`, `.cr2`, `.cr3`, `.dng`, `.nef`, `.nrw`, `.orf`, `.raf`, `.rw2`, `.raw`, `.srw` |

**Animated GIF** (`.gif`) is supported with playback controls (pause, frame step, and related shortcuts)—not treated as a single still frame unless you extract one.

**PDF** (`.pdf`) — drag or paste a file path to import **all pages** as separate images (rasterized via Qt PDF). **Password-protected** PDFs cannot be imported; StickOn shows a message and skips the file. If the estimated memory use is high (about **200 MB** of image data or more), StickOn asks **Yes / No** before importing (**No** is the default).

If an extension is not listed but Qt or Pillow can still decode the file, import may work anyway.

**HEIC / HEIF** (`.heic`, `.heif`, `.hif`) — StickOn bundles **pillow-heif** and **libheif** with the app (no separate Windows codec install). Phone and camera exports should open after drag-and-drop or paste of a file path. Very old or unusual HEIF variants may still fail.

### Formats that may fail or look wrong on import

| Format | What to expect |
|--------|----------------|
| **AVIF** (`.avif`, `.avifs`) | Usually works on recent Pillow builds; older installs or unusual encodes may fail. |
| **JPEG 2000** (`.jp2`, `.j2k`, `.jpc`, `.jpx`) | Pillow-only path; depends on optional codecs being available. |
| **JPEG XL** (`.jxl`) | Pillow-only; support varies by Pillow version and build. |
| **PSD / PSB** (`.psd`, `.psb`) | Typically opens a **flattened** composite, not full layer editing. Large or complex files can be slow or fail. |
| **SVG / SVGZ** (`.svg`, `.svgz`) | Loaded as a **raster** preview (Qt), not as editable vectors. Dense or unusual SVGs may fail or look soft when scaled. |
| **EXR** (`.exr`) | Limited decoder support; HDR tone may look wrong compared to a dedicated EXR viewer. |
| **DDS** (`.dds`) | Some compression types work; others are rejected by the decoder. |
| **Camera RAW** (`.cr2`, `.nef`, `.dng`, etc.) | Highly **camera- and codec-dependent**. Many RAW files will not open on Windows unless the right codecs or libraries are installed; convert to TIFF/PNG for reliable refs. |
| **ICO / CUR** (`.ico`, `.cur`) | Multi-resolution icons use one decoded size; very large icon files are uncommon edge cases. |
| **Large GIFs** | May load but can be **slow** or memory-heavy during playback. |
| **PDF** (`.pdf`) | Large or scanned PDFs can be **slow** and memory-heavy (every page is rasterized). Encrypted/password PDFs fail. Very wide pages are capped at 4096 px on the long edge. |

### Not supported as reference media

- **Video** — `.mp4`, `.mov`, `.webm`, `.mkv`, and similar are **not** imported (use animated GIF for motion on the board).
- **YouTube / streaming URLs** — not supported.

### Export (save out from StickOn)

Export scene or selection (`Ctrl+E`, `Ctrl+Shift+I`) writes **PNG, JPEG, or BMP** only. Importing a PSD, AVIF, or RAW does not mean you can export back to that same format from StickOn.

---

## Main Functions and Hotkeys

- `Always on Top` (`Ctrl+Shift+A`): Modifies the z-order to force the application to render above all other active Windows applications.
- `Always on Bottom` (`Ctrl+Shift+B`): Drops the window z-order to the desktop level, functioning as an interactive workspace wallpaper.
- `Click Through` (`Ctrl+T`): Modifies window flags to ignore all mouse-click events, passing them to the application beneath it. Highly utilized for tracing or modeling over references.
- `Overlay Selection` (`Ctrl+O`): Makes the canvas background transparent and opens a borderless, always-on-top window per **selected** image, as large as possible within the current screen’s available (work) area. Focus stays on the StickOn canvas so `Escape` or other shortcuts work immediately. With overlays open, `Escape` or `Ctrl+O` closes them and restores the canvas.
- `Application Opacity` (`Ctrl+Shift++` or `Ctrl+Wheel Up` / `Ctrl+Shift+-` or `Ctrl+Wheel down`): Adjusts the alpha transparency of the entire application window against the Windows desktop.
- `Lock Window` (`Ctrl+W`): Freezes the window coordinates on the monitor to prevent accidental movement.
- `Minimize Window` (`Ctrl+M`): Sends StickOn to the taskbar. Use the title bar **−** button (left of maximize) or rebind via **Command Key** (`Ctrl+Shift+P`).

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
