"""NVlDlA —— 防截图（SetWindowDisplayAffinity）。

Windows 10 2004+ 支持 WDA_EXCLUDEFROMCAPTURE(0x11)：
把窗口从 DWM 合成中排除，Ctrl+Win+S / PrtSc 等系统截图方式
无法截取到该窗口内容（截出来是透明/空白）。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowDisplayAffinity.restype = wintypes.BOOL

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_from_capture(hwnd: int) -> bool:
    """让窗口从系统截图中排除。hwnd 可通过 int(window.winId()) 获取。"""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def restore_capture(hwnd: int) -> bool:
    """恢复窗口可被截图。"""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_NONE))


def get_affinity(hwnd: int) -> int:
    """读取当前 affinity 值（用于验证）。0=未设置，0x11=已防截图。"""
    aff = wintypes.DWORD(0)
    if not user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(aff)):
        return 0
    return int(aff.value)
