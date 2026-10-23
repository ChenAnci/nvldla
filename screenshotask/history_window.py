"""NVlDlA 提问历史浏览窗口 —— 搜索 + 列表 + 详情。

只读本地 JSONL（history.py），每次打开/点刷新都从磁盘重新加载，
不缓存、不与问答线程共享状态。
"""
from __future__ import annotations

import html
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
)

from history import SHOTS_DIRNAME, load_records


class HistoryWindow(QDialog):
    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("提问历史 — NVlDlA")
        self.resize(1000, 660)
        self._base = Path(base_dir)
        self._records: list[dict] = []

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索问题 / 回答（不区分大小写）…")
        self.search.textChanged.connect(self._rebuild)

        self.listw = QListWidget()
        self.listw.setMinimumWidth(300)
        self.listw.currentRowChanged.connect(self._show_detail)

        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(self._open_anchor)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.listw)
        split.addWidget(self.detail)
        split.setStretchFactor(1, 1)

        self.count_label = QLabel("")
        btn_folder = QPushButton("打开截图文件夹")
        btn_folder.clicked.connect(self._open_shots_folder)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count_label, 1)
        bottom.addWidget(btn_folder)
        bottom.addWidget(btn_refresh)
        bottom.addWidget(btn_close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(split, 1)
        layout.addLayout(bottom)
        self.refresh()

    # ---------------------------------------------------------- 数据
    def refresh(self) -> None:
        self._records = load_records(self._base)
        self._rebuild()

    def _filtered(self) -> list[int]:
        """返回命中的记录下标，新的在前。"""
        idx = list(range(len(self._records) - 1, -1, -1))
        q = self.search.text().strip().lower()
        if not q:
            return idx
        hit = []
        for i in idx:
            r = self._records[i]
            blob = " ".join(
                str(r.get(k, "")) for k in ("prompt", "answer", "full", "error")
            )
            if q in blob.lower():
                hit.append(i)
        return hit

    def _rebuild(self) -> None:
        rows = self._filtered()
        self.listw.blockSignals(True)
        self.listw.clear()
        for i in rows:
            r = self._records[i]
            ts = str(r.get("ts", ""))[:16]
            prov = str(r.get("provider", "?"))
            mark = "" if r.get("status") == "ok" else "⚠ "
            prompt = " ".join(str(r.get("prompt", "")).split())[:34] or "（无提示词）"
            it = QListWidgetItem(f"{mark}{ts}  [{prov}]  {prompt}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.listw.addItem(it)
        self.listw.blockSignals(False)
        self.count_label.setText(f"共 {len(self._records)} 条，显示 {len(rows)} 条")
        if self.listw.count() > 0 and self.listw.currentRow() < 0:
            self.listw.setCurrentRow(0)

    # ---------------------------------------------------------- 详情
    def _show_detail(self, row: int) -> None:
        if row < 0:
            self.detail.setHtml("<i>左侧选择一条记录查看详情</i>")
            return
        it = self.listw.item(row)
        if it is None:
            return
        idx = int(it.data(Qt.ItemDataRole.UserRole))
        if not 0 <= idx < len(self._records):
            return
        r = self._records[idx]
        esc = html.escape

        status = str(r.get("status", "ok"))
        head_rows = [
            ("时间", str(r.get("ts", ""))),
            ("通道", str(r.get("provider", ""))),
            ("模型", str(r.get("model", ""))),
            ("状态", "✅ 完成" if status == "ok" else f"❌ 失败：{esc(str(r.get('error', '')))}"),
            ("二次提取", "开" if r.get("extract") else "关"),
        ]
        head = "".join(
            f'<tr><td style="color:#888;padding-right:8px;white-space:nowrap">'
            f'{esc(k)}</td><td><b>{v}</b></td></tr>'
            for k, v in head_rows
        )

        def section(title: str, body: str) -> str:
            if not body.strip():
                return ""
            return (
                f'<p style="color:#888;margin-bottom:2px">{esc(title)}</p>'
                f'<pre style="white-space:pre-wrap;background:#161616;'
                f'border-radius:6px;padding:8px">{esc(body)}</pre>'
            )

        answer = str(r.get("answer", ""))
        full = str(r.get("full", ""))
        parts = [
            "<style>body{font-family:'Segoe UI',sans-serif;font-size:13px}"
            "a{color:#4da3ff}</style>",
            f"<table>{head}</table>",
            section("提示词（提问）", str(r.get("prompt", ""))),
            section("最终答案", answer),
        ]
        if full and full != answer:
            parts.append(section("完整回答（提取前全文）", full))
        elif not answer and full:
            parts.append(section("完整回答", full))

        shot = str(r.get("shot", ""))
        if shot:
            abs_shot = (self._base / shot).resolve()
            url = html.escape(QUrl.fromLocalFile(str(abs_shot)).toString(), quote=True)
            parts.append(
                f'<p>截图：<a href="{url}">{esc(shot)}</a>'
                f' <span style="color:#666">（点击打开）</span></p>'
            )
        self.detail.setHtml("".join(parts))

    def _open_anchor(self, url: QUrl) -> None:
        QDesktopServices.openUrl(url)

    def _open_shots_folder(self) -> None:
        d = self._base / SHOTS_DIRNAME
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self.refresh()
