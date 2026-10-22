"""NVlDlA —— 账号登录的后台任务与状态查询。

把 webchat 的登录（豆包扫码 / DeepSeek 浏览器）放进 QThread 后台执行，
避免阻塞 GUI；登录过程中通过信号回传状态文字与二维码图片。
"""
from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

import webchat


def faker_dir() -> Path:
    """定位 faker（webchat 包）目录，与 main.py 保持一致。"""
    override = os.environ.get("FAKER_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "faker"


def _doubao_session_path() -> Path:
    """豆包会话文件路径（webchat 数据目录/webchat/sessions/doubao.json）。"""
    from webchat.config import data_dir
    return data_dir() / "webchat" / "sessions" / "doubao.json"


def get_login_status() -> dict:
    """返回各 provider 的本地登录状态 {"doubao": bool, "deepseek": bool}。"""
    doubao_ok = _doubao_session_path().exists()
    from webchat.config import get_deepseek_token
    deepseek_ok = bool(get_deepseek_token().strip())
    return {"doubao": doubao_ok, "deepseek": deepseek_ok}


class DoubaoLoginWorker(QThread):
    """豆包扫码登录（纯 HTTP）。二维码就绪后发出 qr_ready(path)。"""

    status = pyqtSignal(str)       # 状态文字
    qr_ready = pyqtSignal(str)     # 二维码 PNG 路径
    done = pyqtSignal(bool, str)   # (是否成功, 消息)

    def run(self) -> None:
        from webchat.doubao_login import qr_login
        try:
            qr_login(
                _doubao_session_path(),
                on_status=self._on_status,
                auto_open=False,
            )
        except Exception as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, "豆包登录成功，可开始使用")

    def _on_status(self, status_str: str, message: str) -> None:
        if status_str == "qr_ready":
            self.qr_ready.emit(
                str(_doubao_session_path().with_name("doubao_qr.png"))
            )
        self.status.emit(message)


class DeepSeekLoginWorker(QThread):
    """DeepSeek 浏览器登录（打开系统 Chrome，登录后自动提取 token）。"""

    status = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def run(self) -> None:
        from webchat.deepseek import login_via_browser
        self.status.emit(
            "正在打开浏览器（复用系统 Chrome），请在弹出的页面中登录 "
            "chat.deepseek.com，完成后会自动保存登录态…"
        )
        try:
            login_via_browser()
        except Exception as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, "DeepSeek 登录成功，可开始使用")
