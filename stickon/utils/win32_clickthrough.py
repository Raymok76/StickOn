import ctypes
from ctypes import wintypes

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

WM_NCHITTEST = 0x0084
GA_ROOT = 2

HTTRANSPARENT = -1
HTCLIENT = 1

user32 = ctypes.windll.user32


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


def set_clickthrough(hwnd: int, enabled: bool) -> None:
    """Enable/disable click-through mode baseline styles on Windows."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_LAYERED
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def set_clickthrough_passthrough(hwnd: int, enabled: bool) -> None:
    """Toggle WS_EX_TRANSPARENT while preserving other extended styles."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def hwnd_targets_root_window(msg_hwnd: int, root_hwnd: int) -> bool:
    """True if msg_hwnd is the root frame or any child belonging to root_hwnd."""
    if msg_hwnd == root_hwnd:
        return True
    anc = user32.GetAncestor(wintypes.HWND(msg_hwnd), GA_ROOT)
    return int(anc) == int(root_hwnd)


def nc_hit_test_click_through(
    root_hwnd: int,
    msg_lparam: int,
    *,
    title_bar_height_px: int,
    margin_px: int,
) -> int | None:
    """Top bar + resize border remain interactive; other regions pass through."""
    sx = ctypes.c_int16(msg_lparam & 0xFFFF).value
    sy = ctypes.c_int16((msg_lparam >> 16) & 0xFFFF).value
    pt = _POINT(sx, sy)
    if not user32.ScreenToClient(wintypes.HWND(root_hwnd), ctypes.byref(pt)):
        return HTTRANSPARENT
    rc = _RECT()
    if not user32.GetClientRect(wintypes.HWND(root_hwnd), ctypes.byref(rc)):
        return HTTRANSPARENT
    cw = rc.right - rc.left
    ch = rc.bottom - rc.top
    if not (0 <= pt.x < cw and 0 <= pt.y < ch):
        return HTTRANSPARENT
    th = max(1, title_bar_height_px)
    m = max(1, margin_px)
    if pt.y < th:
        return HTCLIENT
    if pt.x <= m or pt.x >= cw - m or pt.y >= ch - m:
        return HTCLIENT
    return HTTRANSPARENT


def parse_windows_msg(message_ptr: object) -> _MSG | None:
    try:
        addr = int(message_ptr)
    except (TypeError, ValueError):
        return None
    if addr == 0:
        return None
    return ctypes.cast(addr, ctypes.POINTER(_MSG)).contents
