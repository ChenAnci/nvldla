"""NVlDlA —— Windows 全局快捷键。

方案：专用 QThread 里跑 Win32 消息泵（RegisterHotKey + GetMessageW）。
优点：
  - 不依赖 QAbstractNativeEventFilter（PyQt6 6.10/6.11 + Py3.14 下
    installNativeEventFilter 会导致事件循环崩溃，已实测）
  - RegisterHotKey 与消息循环同线程，WM_HOTKEY 直接进入该线程队列，
    Qt 信号线程安全，跨线程 emit 自动排队到主线程
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PyQt6.QtCore import QObject, QThread, pyqtSignal

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# 常见键名 -> 虚拟键码（QKeySequence 的 PortableText 命名）
_VK_MAP = {
    **{f"F{i}": 0x70 + i - 1 for i in range(1, 25)},
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
    # 小键盘数字键（用户用小键盘控制）：NumLock 开启时按 numpad 数字会发
    # VK_NUMPAD0..9（0x60..0x69），与上面那排数字键（VK_0..9=0x30..0x39）不同，
    # 必须分别注册，否则"Ctrl+小键盘2"等无效。
    **{f"NumPad{d}": 0x60 + d for d in range(10)},
    "Space": 0x20, "Return": 0x0D, "Enter": 0x0D, "Esc": 0x1B, "Escape": 0x1B,
    "Tab": 0x09, "Backspace": 0x08, "Delete": 0x2E, "Del": 0x2E, "Insert": 0x2D,
    "Ins": 0x2D, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PgUp": 0x21,
    "PageDown": 0x22, "PgDown": 0x22, "Up": 0x26, "Down": 0x28, "Left": 0x25,
    "Right": 0x27, "PrintScreen": 0x2C, "PrtSc": 0x2C, "Pause": 0x13,
    "CapsLock": 0x14, "NumLock": 0x90, "ScrollLock": 0x91,
    "Plus": 0x6B, "Minus": 0x6D, "Comma": 0xBC, "Period": 0xBE,
    "Slash": 0xBF, "Backslash": 0xDC, "Quote": 0xDE, "Semicolon": 0xBA,
    "BracketLeft": 0xDB, "BracketRight": 0xDD, "Backtick": 0xC0,
    "Equal": 0xBB, "OEMPlus": 0xBB, "OEMMinus": 0xBD,
    "Multiply": 0x6A, "Divide": 0x6F, "Subtract": 0x6D,
}

_MOD_MAP = {
    "Ctrl": MOD_CONTROL, "Control": MOD_CONTROL,
    "Shift": MOD_SHIFT,
    "Alt": MOD_ALT,
    "Win": MOD_WIN, "Meta": MOD_WIN, "Super": MOD_WIN,
}


class HotkeyError(ValueError):
    """快捷键无法解析或注册失败。"""


def parse_hotkey(text: str) -> tuple[int, int]:
    """解析 'Ctrl+F8' 这类字符串，返回 (modifiers, vk)。"""
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("快捷键不能为空")
    mods = 0
    for part in parts[:-1]:
        m = _MOD_MAP.get(part)
        if m is None:
            raise HotkeyError(f"不支持的修饰键: {part}")
        mods |= m
    key = parts[-1]
    if key in _MOD_MAP:
        raise HotkeyError("快捷键必须包含一个功能键（不能只有修饰键）")
    vk = _VK_MAP.get(key)
    if vk is None:
        raise HotkeyError(f"不支持的按键: {key}")
    return mods, vk


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class _HotkeyThread(QThread):
    """注册热键并跑消息泵的线程。注册结果同步暴露（threading.Event）。"""

    activated = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, hotkey_id: int, mods: int, vk: int, repeat: bool, parent=None):
        super().__init__(parent)
        self._hotkey_id = hotkey_id
        # MOD_NOREPEAT(0x4000)：「按下」触发一次、按住不自动重复。
        # 需要"一直按住就一直变"时去掉该标志，由系统按键重复速率连续触发。
        self._mods = mods if repeat else (mods | MOD_NOREPEAT)
        self._vk = vk
        self._thread_id = 0
        self._stopping = False
        self._reg_event = threading.Event()
        self._reg_error = ""

    def request_stop(self) -> None:
        self._stopping = True
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def wait_registered(self, timeout: float = 2.0) -> None:
        """阻塞直到线程完成 RegisterHotKey 调用。失败抛 HotkeyError。"""
        self._reg_event.wait(timeout)
        if self._reg_error:
            raise HotkeyError(self._reg_error)

    def run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        if not user32.RegisterHotKey(None, self._hotkey_id, self._mods, self._vk):
            self._reg_error = "快捷键注册失败（可能被其他程序占用）"
            self._reg_event.set()
            self.failed.emit(self._reg_error)
            self._thread_id = 0
            return
        self._reg_event.set()
        try:
            msg = _MSG()
            while not self._stopping:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:  # 0 = WM_QUIT，-1 = 出错
                    break
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    self.activated.emit()
        finally:
            user32.UnregisterHotKey(None, self._hotkey_id)


class GlobalHotkey(QObject):
    """全局快捷键：register(text) 后按下即触发 activated。"""

    activated = pyqtSignal()
    error = pyqtSignal(str)

    _HOTKEY_ID = 0x4E4E  # 'NN'

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: _HotkeyThread | None = None
        self._text: str = ""

    def register(self, text: str, hotkey_id: int | None = None, repeat: bool = False) -> None:
        """注册快捷键。文本解析失败或注册失败均抛 HotkeyError。

        hotkey_id 用于同时注册多个全局热键时区分（默认 ._HOTKEY_ID）。
        repeat=True 时按住按键由系统自动重复触发（用于移动/透明度等连续操作）；
        默认 False（MOD_NOREPEAT），保证截图等一次性操作不因按住而反复触发。
        """
        self.unregister()
        mods, vk = parse_hotkey(text)
        self._text = text
        hid = hotkey_id if hotkey_id is not None else self._HOTKEY_ID
        thread = _HotkeyThread(hid, mods, vk, repeat)
        self._thread = thread
        thread.activated.connect(self.activated)
        thread.failed.connect(self.error)
        thread.start()
        thread.wait_registered(2.0)  # 同步等待注册结果，失败抛 HotkeyError

    def unregister(self) -> None:
        if self._thread is not None:
            thread = self._thread
            self._thread = None
            thread.request_stop()
            thread.wait(2000)
            thread.deleteLater()
