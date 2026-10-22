"""NVlDlA —— 后台问答线程。

在 QThread 里调用 webchat.chat（阻塞式），流式回填答案，
避免阻塞 GUI 主线程（截图上传 + 模型回答可能耗时数十秒）。

两阶段回答（config.extract_answer=True 时）：
  阶段 1：把提示词 + 截图发给 AI，收集完整回答（流式上屏）；
  阶段 2：把完整回答再次发给 AI，让它只提炼出最终答案，
          完成后一次性显示「提取的答案 + 完整回答」。
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

import webchat

from config import DEFAULT_EXTRACT_PROMPT


class ChatWorker(QThread):
    """单次截图问答任务。用完即弃，isRunning() 表示忙碌中。"""

    answer_updated = pyqtSignal(str)   # 流式增量，携带完整累计文本
    finished_ok = pyqtSignal(str)      # 完成，携带最终文本
    failed = pyqtSignal(str)           # 失败，携带错误信息
    phase_changed = pyqtSignal(str)    # 阶段切换提示（如"提取答案中…"）

    def __init__(
        self,
        provider: str,
        prompt: str,
        image_path: str,
        model_kwargs: dict,
        extract_answer: bool = True,
        extract_prompt: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._provider = provider
        self._prompt = prompt
        self._image_path = image_path
        self._model_kwargs = model_kwargs
        self._extract_answer = extract_answer
        self._extract_prompt = extract_prompt or DEFAULT_EXTRACT_PROMPT
        self._buf = ""
        self._extract_buf = ""

    def run(self) -> None:
        try:
            # ---------- 阶段 1：提示词 + 截图 → 完整回答（静默，不上屏） ----------
            r1 = webchat.chat(
                self._provider,
                self._prompt,
                images=[self._image_path],
                stream=False,
                **self._model_kwargs,
            )
            full = (r1.text or "").strip()
            if not full:
                self.finished_ok.emit("（AI 没有返回内容）")
                return

            # ---------- 关闭二次提取：单阶段，流式显示完整回答 ----------
            if not self._extract_answer:
                # dsh（Harness headless）没有流式：整轮已经答完，直接上屏，
                # 不再为流式效果重复冷启动一个 headless 进程
                if self._provider == "dsh":
                    self.finished_ok.emit(full)
                    return
                self._buf = ""
                webchat.chat(
                    self._provider,
                    self._prompt,
                    images=[self._image_path],
                    stream=True,
                    on_chunk=self._on_chunk,
                    **self._model_kwargs,
                )
                self.finished_ok.emit(self._buf.strip() or "（AI 没有返回内容）")
                return

            # ---------- 阶段 2：完整回答 → 提取最终答案（流式上屏） ----------
            self.phase_changed.emit("提取答案中…")
            self._extract_buf = ""
            extract_text = self._extract_prompt.replace("{answer}", full)
            try:
                r2 = webchat.chat(
                    self._provider,
                    extract_text,
                    stream=True,
                    on_chunk=self._on_extract_chunk,
                    **self._model_kwargs,
                )
            except Exception as exc:
                # 提取失败不整体报错：回退显示完整回答
                self.finished_ok.emit(f"（二次提取失败：{exc}）\n\n{full}")
                return

            answer = (r2.text or "").strip() or self._extract_buf.strip()
            self.finished_ok.emit(answer or "（未提取到答案）")
        except Exception as exc:  # webchat 抛出的各类 AuthError/RateLimitError/...
            self.failed.emit(str(exc))

    def _on_chunk(self, text: str) -> None:
        self._buf += text
        self.answer_updated.emit(self._buf)

    def _on_extract_chunk(self, text: str) -> None:
        self._extract_buf += text
        self.answer_updated.emit(self._extract_buf)
