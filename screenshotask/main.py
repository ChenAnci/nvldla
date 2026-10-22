"""NVlDlA —— 悬浮截图问答助手主程序。

托盘常驻（图标为 NVIDIA 风格），后台监听全局快捷键（默认 F8）：
按下即截取整个屏幕 → 发送给豆包/DeepSeek → 流式答案显示在
最前端悬浮窗中。悬浮窗通过 SetWindowDisplayAffinity 从系统截图
中排除（Ctrl+Win+S / PrtSc 等无法截取）。
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _ensure_faker_path() -> None:
    """把 faker 包（webchat）挂到 sys.path。"""
    # PyInstaller 打包（frozen）后 webchat 已打进 exe，无需挂载
    if getattr(sys, "frozen", False):
        return
    override = os.environ.get("FAKER_PATH")
    if override:
        faker_dir = Path(override)
    else:
        faker_dir = Path(__file__).resolve().parent.parent / "faker"
    p = str(faker_dir)
    if p not in sys.path:
        sys.path.insert(0, p)


_ensure_faker_path()

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QStyle, QSystemTrayIcon,
)

from chat_worker import ChatWorker
from config import AppConfig
from hotkey import GlobalHotkey, HotkeyError
from overlay import OverlayWindow
from settings import SettingsDialog, shutdown_login_workers

APP_NAME = "NVlDlA"

# 打包（frozen）后：BASE_DIR = exe 所在目录（写配置用）；
# 源码运行：BASE_DIR = screenshotask 目录。
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

# 图标：frozen 时从解压目录 _MEIPASS 读取；源码运行取 BASE_DIR/icon.png。
ICON_PATH = (
    Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "icon.png"
    if getattr(sys, "frozen", False)
    else BASE_DIR / "icon.png"
)
CONFIG_PATH = BASE_DIR / "nvldla_config.json"


def load_app_icon() -> QIcon:
    """加载托盘/窗口图标；文件缺失时回退系统默认图标，保证托盘始终可见。"""
    if ICON_PATH.exists():
        return QIcon(str(ICON_PATH))
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

COLOR_BUSY = "#f5a623"
COLOR_WORKING = "#4da3ff"
COLOR_DONE = "#2fb344"
COLOR_ERROR = "#e5484d"

# ---------------------------------------------------------- 单实例
# 启动时用命名互斥体保证同一时刻只有一个 NVlDlA 实例：
# 多个实例会互相抢占 F8 全局热键（RegisterHotKey 同一组合只能注册一次，
# 后启动的实例注册失败导致热键失效）。句柄保存在模块级，进程存活期间
# 不会释放；进程退出/崩溃时系统自动回收句柄，不会残留"假锁"。
_MUTEX_NAME = r"Local\NVlDlA_SingleInstance"
ERROR_ALREADY_EXISTS = 183
MB_ICONINFORMATION = 0x00000040
_INSTANCE_MUTEX: int | None = None

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int

_user32 = ctypes.windll.user32
_user32.MessageBoxW.argtypes = [
    ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
]
_user32.MessageBoxW.restype = ctypes.c_int


def acquire_single_instance() -> bool:
    """尝试成为唯一实例。成功返回 True（已持有互斥体）；已有实例返回 False。"""
    global _INSTANCE_MUTEX
    if _INSTANCE_MUTEX is not None:
        return True
    handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return False
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def notify_already_running() -> None:
    """已有实例运行时提示用户，然后本实例退出。"""
    msg = "NVlDlA 已在运行，本实例即将退出。"
    # pythonw 下 sys.stderr 为 None，print(..., file=sys.stderr) 会崩溃
    if sys.stderr is not None:
        print(msg, file=sys.stderr)
    _user32.MessageBoxW(None, msg, APP_NAME, MB_ICONINFORMATION)


def _debug_log(msg: str) -> None:
    """NVLDLA_DEBUG=1 时把运行状态写入项目目录 nvldla_debug.log。"""
    if not os.environ.get("NVLDLA_DEBUG"):
        return
    try:
        with open(BASE_DIR / "nvldla_debug.log", "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except OSError:
        pass


class NvldlaApp:
    def __init__(self, app: QApplication, cfg_path: Path):
        self.app = app
        self.cfg_path = cfg_path
        self.cfg = AppConfig.load(cfg_path)

        icon = load_app_icon()
        self.overlay = OverlayWindow(self.cfg, CONFIG_PATH)
        self.overlay.setWindowIcon(icon)
        self.worker: ChatWorker | None = None

        self.hotkey = GlobalHotkey()
        self.hotkey.activated.connect(self.on_hotkey)
        self.hotkey.error.connect(self._on_hotkey_error)
        # 全局控制热键：截图用 Ctrl+F8（配置里指定），其余固定全局热键
        self.control_hotkeys: dict[str, GlobalHotkey] = {}

        self._build_tray(icon)
        self._register_hotkey()
        self._register_control_hotkeys()
        self.overlay.show()
        _debug_log(f"app ready: overlay_visible={self.overlay.isVisible()} "
                   f"winid={int(self.overlay.winId()):#x} hotkey={self.cfg.hotkey}")
        if os.environ.get("NVLDLA_DEBUG"):
            from PyQt6.QtCore import QTimer
            self._hb_timer = QTimer()
            self._hb_timer.timeout.connect(lambda: _debug_log("heartbeat"))
            self._hb_timer.start(5000)

    # ---------------------------------------------------------- 托盘
    def _build_tray(self, icon: QIcon) -> None:
        self.tray = QSystemTrayIcon(icon, self.app)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()

        toggle_action = QAction("显示/隐藏悬浮窗", menu)
        toggle_action.triggered.connect(self.toggle_overlay)

        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(self.open_settings)

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.quit)

        menu.addAction(toggle_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_overlay()

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self._save_geometry()
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()

    # ---------------------------------------------------------- 快捷键
    def _register_hotkey(self) -> None:
        try:
            self.hotkey.register(self.cfg.hotkey)
        except HotkeyError as exc:
            self._on_hotkey_error(str(exc))

    # 控制动作表：动作名 -> overlay 动作。shot 特殊（截图提问，走 on_hotkey）。
    # 其余动作通过 o.<动作> 绑定到 overlay 实例。
    _CONTROL_ACTIONS = {
        "scroll_down": lambda o: o._scroll_by(30),
        "scroll_up": lambda o: o._scroll_by(-30),
        "move_left": lambda o: o._move_by(-1, 0),
        "move_right": lambda o: o._move_by(1, 0),
        "move_down": lambda o: o._move_by(0, 1),
        "move_up": lambda o: o._move_by(0, -1),
        "opacity_down": lambda o: o._adjust_opacity(-1),
        "opacity_up": lambda o: o._adjust_opacity(1),
        "font_size_down": lambda o: o._adjust_font_size(-1),
        "font_size_up": lambda o: o._adjust_font_size(1),
    }

    def _register_control_hotkeys(self) -> None:
        """按配置注册所有控制热键。

        每个动作可从 cfg.control_hotkeys[动作名] 自由改键（设置里可改）。
        默认是小键盘数字键（NumPad 0..9 = VK 0x60..0x69），**不加修饰键**即可触发；
        上面那排数字键不在注册之列，照常输入。shot（截图提问）不参与按住连发，
        其它动作按住时由系统按键重复速率连续触发（repeat=True）。
        """
        base = 0x6000
        idx = 0
        for name, seq in self.cfg.control_hotkeys.items():
            if name == "shot":
                action = self.on_hotkey
                repeat = False          # 截图提问：按住不连发
            else:
                make = self._CONTROL_ACTIONS.get(name)
                if make is None:
                    self._on_hotkey_error(f"未知控制动作: {name}")
                    continue
                action = lambda o=self.overlay, m=make: m(o)
                repeat = True           # 移动/透明度：按住连续触发
            gh = GlobalHotkey()
            gh.activated.connect(action)
            try:
                gh.register(seq, hotkey_id=base + idx, repeat=repeat)
            except HotkeyError as exc:
                self._on_hotkey_error(f"{name}({seq}): {exc}")
                continue
            self.control_hotkeys[seq] = gh
            idx += 1

    def _unregister_control_hotkeys(self) -> None:
        for gh in self.control_hotkeys.values():
            gh.unregister()
        self.control_hotkeys.clear()

    def _on_hotkey_error(self, message: str) -> None:
        self.overlay.set_status("快捷键不可用", COLOR_ERROR)
        self.tray.showMessage(
            APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning, 5000
        )

    def on_hotkey(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.overlay.show()
            self.overlay.set_status("上一轮处理中…", COLOR_BUSY)
            return
        self.overlay.show()
        self.overlay.raise_()
        # 按下热键立即清空回复框（不等截图完成）
        self.overlay.set_answer("")
        self.overlay.set_status("截图中…", COLOR_WORKING)
        try:
            shot_path = self.capture_screenshot()
        except Exception as exc:
            self.overlay.set_answer(f"截图失败：{exc}")
            self.overlay.set_status("失败", COLOR_ERROR)
            return
        self.overlay.set_status("上传并提问中…", COLOR_WORKING)

        self.worker = ChatWorker(
            self.cfg.provider, self.cfg.prompt, str(shot_path), self.cfg.model_kwargs,
            extract_answer=self.cfg.extract_answer,
            extract_prompt=self.cfg.extract_prompt,
        )
        self.worker.answer_updated.connect(self.overlay.set_answer)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.phase_changed.connect(self._on_phase)
        self.worker.start()

    def capture_screenshot(self) -> Path:
        """截取整个虚拟桌面（多显示器），保存为临时 PNG。"""
        screen = QApplication.primaryScreen()
        pixmap = screen.grabWindow(0)
        tmp_dir = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        ))
        path = tmp_dir / "nvldla_shot.png"
        pixmap.save(str(path), "PNG")
        return path

    def _on_finished(self, text: str) -> None:
        self.overlay.set_answer(text)
        self.overlay.set_status("完成", COLOR_DONE)

    def _on_phase(self, message: str) -> None:
        """阶段切换提示（如进入"提取答案中…"）。"""
        self.overlay.set_status(message, COLOR_WORKING)

    def _on_failed(self, message: str) -> None:
        self.overlay.set_status("失败", COLOR_ERROR)
        hint = ""
        if self.cfg.provider == "api":
            if any(k in message for k in ("API Key", "接口", "模型", "图片")):
                hint = "（提示：请在托盘菜单「设置」中核对 API 地址 / API Key / 模型名）"
        elif self.cfg.provider == "dsh":
            hint = ("（提示：dsh 需要本机已安装 DeepSeek Harness，"
                    "且环境里有 DeepSeek 官方 Key）")
        elif "登录" in message or "会话" in message or "sessionid" in message:
            hint = "（提示：若为登录过期，请在托盘菜单「设置」→「账号登录」重新登录）"
        self.overlay.set_answer(f"出错了：{message}\n{hint}")

    # ---------------------------------------------------------- 设置
    def open_settings(self) -> None:
        # 打开设置前先显示悬浮窗，便于滑块实时预览透明度效果
        if not self.overlay.isVisible():
            self.overlay.show()
        dlg = SettingsDialog(
            self.cfg,
            preview=self.overlay.apply_settings,
            notify=lambda msg: self.tray.showMessage(
                APP_NAME, msg, QSystemTrayIcon.MessageIcon.Information, 5000
            ),
        )
        dlg.setWindowIcon(self.tray.icon())
        self.hotkey.unregister()  # 设置期间不响应截图快捷键
        try:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                # 取消：还原拖动滑块时实时预览的效果
                self.overlay.apply_settings(self.cfg)
                return
            new_cfg = dlg.collect()
            gx, gy, gw, gh = self.overlay.current_geometry()
            new_cfg.window_x, new_cfg.window_y = gx, gy
            new_cfg.window_w, new_cfg.window_h = gw, gh
            self.cfg = new_cfg
            self.cfg.save(self.cfg_path)
            self.overlay.apply_settings(self.cfg)
            # 改键生效：先释放再按新配置重注册控制热键
            self._unregister_control_hotkeys()
            self._register_control_hotkeys()
        finally:
            self._register_hotkey()

    # ---------------------------------------------------------- 退出
    def _save_geometry(self) -> None:
        gx, gy, gw, gh = self.overlay.current_geometry()
        self.cfg.window_x, self.cfg.window_y = gx, gy
        self.cfg.window_w, self.cfg.window_h = gw, gh

    def quit(self) -> None:
        self._save_geometry()
        self.cfg.save(self.cfg_path)
        if self.worker is not None:
            self.worker.wait(3000)
        shutdown_login_workers()
        self._unregister_control_hotkeys()
        self.hotkey.unregister()
        self.tray.hide()
        self.overlay.hide()
        self.app.quit()


def main() -> int:
    # 单实例检查：已有实例在运行则提示并退出，避免抢占 F8 全局热键
    if not acquire_single_instance():
        notify_already_running()
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(load_app_icon())
    # 必须持有 controller 引用，否则 Python GC 会销毁悬浮窗/热键对象
    controller = NvldlaApp(app, CONFIG_PATH)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
