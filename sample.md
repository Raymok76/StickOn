# sample program

**Target OS:** Windows
**Format:** Structured Agent Manifest

## 1. System Overview

* **Application Name:** StickOn
* **Core Function:** A standalone, infinite-canvas image viewer and mood board organizer designed for visual artists, designers, and 3D modelers.
* **UI/UX Paradigm:** Hyper-minimalist. By default, there are **no visible toolbars, menus, or window borders**. The application is driven almost entirely by keyboard shortcuts, mouse chords (combinations of buttons + modifiers), a Command Palette, and a Right-Click Context Menu.

## 2. Windows-Specific Implementations & OS Interactions

* **Executable:** Runs as a standard Windows executable (`.exe`). Can be run as a portable application or installed locally.
* **DWM Integration:** Deeply utilizes the Windows Desktop Window Manager (DWM) for advanced state manipulations (e.g., frameless windows, background transparency, overlay modes).
* **Taskbar Hover Context (CRITICAL RECOVERY):** On Windows, if the application is set to "Transparent to Mouse" (which disables hit-testing and makes the window unclickable), the user or automation agent can recover control by hovering over the StickOn icon in the Windows Taskbar and clicking the small context button that appears to toggle off the state.
* **Data Ingestion:** Deep integration with Windows Explorer for Drag & Drop support and the Windows Clipboard (`Ctrl+V` to paste image data or file paths directly from memory).

## 3. Core Feature Schema (State Modifiers & Capabilities)

### A. Window State Attributes

These commands alter how the application interacts with the Windows OS environment:

* `Always on Top` (`Ctrl+Shift+A`): Modifies the z-order to force the application to render above all other active Windows applications.
* `Always on Bottom` (`Ctrl+Shift+B`): Drops the window z-order to the desktop level, functioning as an interactive workspace wallpaper.
* `Transparent to Mouse` (`Ctrl+T`): Modifies window flags to ignore all mouse-click events, passing them to the application beneath it. Highly utilized for tracing or modeling over references.
* `Overlay Selection` (`Ctrl+Y`): Removes the main canvas background and spawns floating, borderless sub-windows for each individual image node.
* `Application Opacity` (`Ctrl+Shift++` / `Ctrl+Shift+-`): Adjusts the alpha transparency of the entire application window against the Windows desktop.
* `Lock Window` (`Ctrl+W`): Freezes the window coordinates on the monitor to prevent accidental movement.

### B. Canvas & Node Data Management

* **Packing & Alignment:** * `Pack Optimal` (`Ctrl+P`): Algorithmic spatial packing of selected image nodes to eliminate empty space.
* `Align` (`Ctrl+Arrow Keys`): Snaps node bounding boxes to their respective axes.


* **Node Transformation (Non-Destructive):**
* *Scale/Resize*: `Ctrl+Alt + Left Mouse Drag`
* *Rotate*: `Ctrl + Left Mouse Drag` (Add `Shift` to snap to 45-degree increments).
* *Flip*: `Alt+Shift + Left Mouse Drag` (Directional based on mouse movement).
* *Crop*: `C + Left Mouse Drag` (Draws a non-destructive bounding box mask).


* **Hierarchy & Grouping:** Nodes can be grouped (`Ctrl+G`) and nested.

### C. Annotation & Markup Tools

* **Notes** (`Ctrl+N`): Instantiates a text node object. Supports rich text and background color modification.
* **Draw Mode** (`Ctrl+Shift+D`): Converts the cursor into a markup tool to draw lines, shapes (rectangles/circles), and arrows directly onto the canvas or over image nodes.
* **GIF Playback:** Supports loading and continuous looping of `.gif` files, with capabilities to pause, scrub frames, and extract static frames.

### D. File I/O & Persistence

* **Save Format:** `.pur` (Proprietary binary file). Stores layout coordinates, hierarchy, and embeds or links image binary data.
* **Export Actions:** `Ctrl+E` (Export Scene) or `Ctrl+Shift+I` (Export Selected Images) to rasterize the canvas or extract nodes to standard formats (PNG, BMP, JPG) on the local file system.

## 4. Input Matrix (Windows Keyboard & Mouse Event Listeners)

| Action / Function | Windows Event Trigger |
| --- | --- |
| **Command Palette** | `Ctrl+Shift+P` |
| **Context Menu** | `Right Mouse Button Click` |
| **Move Window** | `Right Mouse Button Drag` (Anywhere on canvas) |
| **Pan Canvas** | `Middle Mouse Button Drag` OR `Left Mouse Drag + Alt` |
| **Zoom Canvas** | `Mouse Scroll Wheel` OR `Z + Left Mouse Drag` |
| **Pick Color (Eyedropper)** | `S + Left Mouse Hold` (Can drag out of app to sample OS colors) |
| **Show Image Coordinates** | `D + Left Mouse Hold` |
| **Grayscale Canvas** | `Ctrl+Alt+G` |
| **Undo / Redo** | `Ctrl+Z` / `Ctrl+Shift+Z` |

## 5. Automation & Integration Notes for AI Agents

1. **No Native API:**: StickOn does not feature a REST API, CLI toolset, or COM interface for headless manipulation.
2. **GUI Automation Constraint:** Because the interface lacks standard Windows UI elements (buttons, standard file ribbons), traditional Accessibility Tree / DOM parsing (like WinAppDriver) will fail to see internal tools.
3. **Agent Action Path:** To automate StickOn, an AI agent must rely on OS-level coordinate-based GUI automation (e.g., PyAutoGUI) and inject the exact keyboard/mouse events listed above.
4. **Optimal Entry Point:** The Command Palette (`Ctrl+Shift+P`) is the most reliable text-based entry point for an agent to programmatically execute specific application commands without relying on complex mouse-drag coordinates.