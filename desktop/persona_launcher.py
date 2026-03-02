"""
desktop/persona_launcher.py
Floating desktop widget for Home Pal.

Opens a borderless, transparent, always-on-top window that shows the Persona
widget at http://localhost:5000/persona/desktop. Window position is saved to
desktop/window_pos.json and restored on next launch.

Usage (from project root, with Flask server already running):
    python desktop/persona_launcher.py

Install dependency once:
    pip install pywebview>=4.4.1
"""

import ctypes
import ctypes.wintypes
import json
import os
import sys
import time
from pathlib import Path

try:
    import webview
except ImportError:
    print(
        "[launcher] ERROR: pywebview is not installed.\n"
        "           Run:  pip install pywebview>=4.4.1"
    )
    sys.exit(1)

try:
    import requests
except ImportError:
    requests = None

# ─── Config ───────────────────────────────────────────────────────────────────

SERVER_URL      = "http://localhost:5000"
DESKTOP_URL     = f"{SERVER_URL}/persona/desktop"
WINDOW_TITLE    = "Home Pal"
WINDOW_POS_FILE = Path(__file__).parent / "window_pos.json"
ICON_PATH       = Path(__file__).parent / "homepal.ico"

DEFAULT_WIDTH  = 340
DEFAULT_HEIGHT = 680
DEFAULT_X      = 80
DEFAULT_Y      = 80

CORNER_RADIUS  = 8    # must match CSS border-radius on html / #desktop-root


# ─── Win32 helpers ────────────────────────────────────────────────────────────

def _find_hwnd(title: str) -> int | None:
    """Find the HWND of the first top-level window with this title that belongs
    to our process.  No visibility check — called both at load time (window may
    not be visible yet) and from event handlers."""
    if sys.platform != 'win32':
        return None
    our_pid = os.getpid()
    result  = [None]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == our_pid:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == title:
                result[0] = hwnd
                return False   # stop enumeration
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    return result[0]


def _apply_rounded_corners(hwnd: int, width: int, height: int) -> None:
    """Apply OS-level rounded corners.

    Windows 11+: DwmSetWindowAttribute DWMWCP_ROUND — automatic on resize.
    Windows 10:  SetWindowRgn with a rounded rectangle — must be refreshed
                 when the window size changes.
    """
    if sys.platform != 'win32' or not hwnd:
        return
    try:
        build = sys.getwindowsversion().build
        if build >= 22000:                              # Windows 11+
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND                   = 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref), ctypes.sizeof(pref)
            )
        else:                                           # Windows 10
            _update_window_region(hwnd, width, height)
    except Exception as e:
        print(f"[launcher] Rounded corner setup failed: {e}")


def _set_window_icon(hwnd: int) -> None:
    """Load homepal.ico and apply it as the window + taskbar icon."""
    if sys.platform != 'win32' or not hwnd or not ICON_PATH.exists():
        return
    try:
        hicon = ctypes.windll.user32.LoadImageW(
            None, str(ICON_PATH), IMAGE_ICON, 0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if hicon:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG,   hicon)
    except Exception as e:
        print(f"[launcher] Failed to set window icon: {e}")


def _update_window_region(hwnd: int, width: int, height: int) -> None:
    """Refresh the Win32 window region to match the current size (Win10)."""
    if sys.platform != 'win32' or not hwnd:
        return
    try:
        r = CORNER_RADIUS * 2
        hrgn = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, r, r
        )
        ctypes.windll.user32.SetWindowRgn(hwnd, hrgn, True)
    except Exception as e:
        print(f"[launcher] SetWindowRgn update failed: {e}")


# ─── Parent window hook (WM_EXITSIZEMOVE) ─────────────────────────────────────
# Captures the true outer rect at every stable moment (end of resize/move).
# Also seeded on load and in main() so the first session is correct.
_parent_wndproc_ref = None
_parent_old_proc    = None
_last_known_rect: dict = {}    # {"x", "y", "width", "height"}

# ─── Typed Win32 call helpers ─────────────────────────────────────────────────
# ctypes defaults undecorated args to c_int (32-bit).  On 64-bit Windows,
# LPARAM / LRESULT are pointer-sized (8 bytes) — passing large values crashes
# with OverflowError.  Declare argtypes/restype once at module level.
if sys.platform == 'win32':
    _CallWindowProcW = ctypes.windll.user32.CallWindowProcW
    _CallWindowProcW.restype  = ctypes.c_longlong          # LRESULT (64-bit)
    _CallWindowProcW.argtypes = [
        ctypes.c_void_p,         # lpPrevWndFunc
        ctypes.wintypes.HWND,    # hWnd
        ctypes.wintypes.UINT,    # Msg
        ctypes.wintypes.WPARAM,  # wParam
        ctypes.wintypes.LPARAM,  # lParam
    ]
    _SetWindowLongPtrW = ctypes.windll.user32.SetWindowLongPtrW
    _SetWindowLongPtrW.restype  = ctypes.c_void_p          # old proc pointer
    _SetWindowLongPtrW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.c_int,            # nIndex
        ctypes.c_void_p,         # new proc pointer
    ]
else:
    _CallWindowProcW   = None
    _SetWindowLongPtrW = None

# WNDPROCTYPE with correct 64-bit return (LRESULT) — shared by both hooks.
if sys.platform == 'win32':
    WNDPROCTYPE = ctypes.WINFUNCTYPE(
        ctypes.c_longlong,       # LRESULT
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )


# pywebview's create_window(width=W, height=H) creates a WinForms Form whose
# GetWindowRect returns W−16 × H−39.  Compensate so the saved values, when
# passed back to create_window, restore the same visual size.
_FORM_W_OFFSET = 16
_FORM_H_OFFSET = 39

# ─── Win32 constants ──────────────────────────────────────────────────────────
GWL_STYLE          = -16
WS_THICKFRAME      = 0x00040000
GWLP_WNDPROC       = -4
WM_NCCALCSIZE      = 0x0083
WM_EXITSIZEMOVE    = 0x0232
WM_SETICON         = 0x0080
IMAGE_ICON         = 1
LR_LOADFROMFILE    = 0x00000010
LR_DEFAULTSIZE     = 0x00000040
ICON_SMALL         = 0
ICON_BIG           = 1


def _capture_rect(hwnd: int) -> None:
    """Read GetWindowRect into _last_known_rect (called at stable moments only).
    Width/height are inflated by the pywebview create_window offset so the saved
    values can be passed straight back to create_window next session."""
    if sys.platform != 'win32' or not hwnd:
        return
    try:
        rc = ctypes.wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            w = (rc.right  - rc.left) + _FORM_W_OFFSET
            h = (rc.bottom - rc.top)  + _FORM_H_OFFSET
            _last_known_rect.update({"x": rc.left, "y": rc.top, "width": w, "height": h})
    except Exception as e:
        print(f"[launcher] _capture_rect failed: {e}")


def _setup_parent_hooks(hwnd: int) -> None:
    """Subclass the top-level window to intercept WM_EXITSIZEMOVE — the only
    moment after a user resize/move where GetWindowRect is guaranteed stable
    (close animation has not yet started, resize modal loop has just ended)."""
    global _parent_wndproc_ref, _parent_old_proc
    if sys.platform != 'win32' or not hwnd:
        return

    # SM_CYSIZEFRAME (33): height of the visible resize border — kept as the
    # visual NC strip after stripping the title bar in WM_NCCALCSIZE.
    top_frame = ctypes.windll.user32.GetSystemMetrics(33)

    def _wndproc(h, msg, wparam, lparam):
        if msg == WM_NCCALCSIZE and wparam:
            # Let WinForms calculate NC areas normally (keeps thin left/right/bottom
            # resize borders).  Then set client top = window top + top_frame to
            # eliminate only the large title-bar NC area added by WS_THICKFRAME.
            params = ctypes.cast(lparam, ctypes.POINTER(ctypes.wintypes.RECT))
            window_top = params[0].top + top_frame
            result = _CallWindowProcW(_parent_old_proc, h, msg, wparam, lparam)
            params[0].top = window_top
            return result
        if msg == WM_EXITSIZEMOVE:
            _capture_rect(hwnd)
        return _CallWindowProcW(_parent_old_proc, h, msg, wparam, lparam)

    _parent_wndproc_ref = WNDPROCTYPE(_wndproc)
    _parent_old_proc    = _SetWindowLongPtrW(hwnd, GWLP_WNDPROC, _parent_wndproc_ref)

# ─── Position persistence ─────────────────────────────────────────────────────

def _load_prefs() -> dict:
    try:
        if WINDOW_POS_FILE.exists():
            return json.loads(WINDOW_POS_FILE.read_text())
    except Exception as e:
        print(f"[launcher] Failed to load prefs: {e}")
    return {}


def _load_pos() -> dict:
    data = _load_prefs()
    return {
        "x":      int(data.get("x",      DEFAULT_X)),
        "y":      int(data.get("y",      DEFAULT_Y)),
        "width":  int(data.get("width",  DEFAULT_WIDTH)),
        "height": int(data.get("height", DEFAULT_HEIGHT)),
    }


def _save_pos() -> None:
    """Write _last_known_rect to window_pos.json, preserving any other saved keys."""
    try:
        if not _last_known_rect:
            print("[launcher] No rect cached — skipping save")
            return
        data = _load_prefs()   # preserve other keys (e.g. tts_enabled)
        data.update({
            "x":      _last_known_rect["x"],
            "y":      _last_known_rect["y"],
            "width":  _last_known_rect["width"],
            "height": _last_known_rect["height"],
        })
        WINDOW_POS_FILE.write_text(json.dumps(data, indent=2))
        print(f"[launcher] Prefs saved: {_last_known_rect}")
    except Exception as e:
        print(f"[launcher] Failed to save prefs: {e}")


# ─── Server readiness check ───────────────────────────────────────────────────

def _wait_for_server(url: str, timeout: int = 30) -> bool:
    """Poll /persona until the Flask server responds, up to timeout seconds."""
    check_url = f"{url}/persona"
    deadline  = time.time() + timeout
    print(f"[launcher] Waiting for server at {url} ...", end="", flush=True)

    if requests is None:
        # Fallback: stdlib urllib
        import urllib.request
        while time.time() < deadline:
            try:
                urllib.request.urlopen(check_url, timeout=2)
                print(" ready.")
                return True
            except Exception:
                print(".", end="", flush=True)
                time.sleep(1)
        print(" timed out.")
        return False

    while time.time() < deadline:
        try:
            r = requests.get(check_url, timeout=2)
            if r.status_code < 500:
                print(" ready.")
                return True
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(1)

    print(" timed out.")
    return False


# ─── Python API exposed to JS ─────────────────────────────────────────────────

class Api:
    """Methods callable from JS as window.pywebview.api.<method>()."""

    def __init__(self):
        self._window = None   # set after window creation
        self._hwnd   = None   # cached Win32 HWND, set in on_loaded()

    def close(self) -> None:
        if self._window:
            self._window.destroy()

    def minimize(self) -> None:
        if self._window:
            self._window.minimize()

    def setTtsEnabled(self, enabled: bool) -> None:
        """Persist TTS preference to window_pos.json."""
        try:
            data = _load_prefs()
            data["tts_enabled"] = bool(enabled)
            WINDOW_POS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[launcher] Failed to save TTS pref: {e}")

    def setChatOpen(self, open: bool) -> None:
        """Persist chat panel open/closed state to window_pos.json."""
        try:
            data = _load_prefs()
            data["chat_open"] = bool(open)
            WINDOW_POS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[launcher] Failed to save chat_open pref: {e}")

    def setChatSplitRatio(self, ratio: float) -> None:
        """Persist image/chat split ratio (0–1, image fraction) to window_pos.json."""
        try:
            data = _load_prefs()
            data["chat_split_ratio"] = float(ratio)
            WINDOW_POS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[launcher] Failed to save chat_split_ratio: {e}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def _on_shown() -> None:
    hwnd = _find_hwnd(WINDOW_TITLE)
    if hwnd:
        _set_window_icon(hwnd)


def main() -> None:
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HomePal.Persona")

    if not _wait_for_server(SERVER_URL, timeout=30):
        print(
            "\n[launcher] ERROR: Flask server did not respond within 30 seconds.\n"
            "           Start the server first:  python app.py"
        )
        sys.exit(1)

    pos = _load_pos()
    # Seed the rect cache with the persisted position so that if the user closes
    # the window without ever moving or resizing it, _save_pos still has data.
    _last_known_rect.update(pos)
    api = Api()

    window = webview.create_window(
        title=WINDOW_TITLE,
        url=DESKTOP_URL,
        js_api=api,
        width=pos["width"],
        height=pos["height"],
        x=pos["x"],
        y=pos["y"],
        resizable=True,
        frameless=True,            # no OS title bar
        transparent=True,          # layered window + WebView2 DefaultBackgroundColor transparent
        on_top=True,
        easy_drag=False,           # drag handled by pywebview-drag-region CSS class
    )

    api._window = window

    def _on_loaded():
        hwnd = _find_hwnd(WINDOW_TITLE)
        if hwnd:
            api._hwnd = hwnd
            # Add WS_THICKFRAME so DefWindowProc handles NC resize hit-codes.
            # Without it, HTBOTTOM/HTRIGHT/HTBOTTOMRIGHT from the child hook are
            # silently ignored and the resize modal loop never starts.
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
            # Install the hook BEFORE SWP_FRAMECHANGED so the WM_NCCALCSIZE
            # message triggered by SWP_FRAMECHANGED is already intercepted and
            # the border is trimmed from the very first frame.
            _setup_parent_hooks(hwnd)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0020,   # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
            )
            _apply_rounded_corners(hwnd, window.width, window.height)
            _capture_rect(hwnd)
        else:
            print("[launcher] WARNING: could not find HWND")

        # Push persisted state into the page (localStorage is ephemeral in WebView2)
        prefs = _load_prefs()
        window.evaluate_js(
            f"if(typeof _applyPersistedTts==='function') _applyPersistedTts({json.dumps(prefs.get('tts_enabled', True))});"
        )
        window.evaluate_js(
            f"if(typeof _applyPersistedSplitRatio==='function') _applyPersistedSplitRatio({json.dumps(prefs.get('chat_split_ratio', None))});"
        )
        window.evaluate_js(
            f"if(typeof _applyPersistedChatOpen==='function') _applyPersistedChatOpen({json.dumps(prefs.get('chat_open', True))});"
        )

    window.events.shown   += _on_shown
    window.events.loaded  += _on_loaded
    def _on_closing():
        # GetWindowRect at close is reliable for size (hwnd is confirmed set).
        # Use it directly so position is always current (WM_EXITSIZEMOVE never
        # fires for pywebview CSS-drag moves, so the cache position is stale).
        if api._hwnd:
            rc = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(api._hwnd, ctypes.byref(rc))
            _last_known_rect.update({
                "x":      rc.left,
                "y":      rc.top,
                "width":  (rc.right  - rc.left) + _FORM_W_OFFSET,
                "height": (rc.bottom - rc.top)  + _FORM_H_OFFSET,
            })
        _save_pos()

    window.events.closing += _on_closing

    print(f"[launcher] Opening window at {DESKTOP_URL}")
    webview.start(debug=False)


if __name__ == "__main__":
    main()
