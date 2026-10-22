"""dsh —— 用本机 DeepSeek Harness（DSH）的 headless 模式当答题大脑。

DSH 桌面版没有 OpenAI 兼容接口（私有 RPC + WebSocket，不适合直连），
但它自带无头模式：任务文本从 stdin 读入（`-` 表示读 stdin），
最终答案写 stdout，推理/诊断写 stderr，退出码 0 = 成功。

实现要点：
  - 用 ELECTRON_RUN_AS_NODE=1 让 Harness.exe 以 Node 身份执行 asar 里的 bin.js；
  - headless 首次运行会在 $DSH_HOME/profiles/headless 建自己的 profile；
  - 默认模型路由 deepseek-official 需要 DEEPSEEK_API_KEY：启动前注入环境
    （取值顺序：进程 env DEEPSEEK_API_KEY → env OPENAI_API_KEY →
      注册表用户级同名变量），机器上没配时原样透传 Harness 自己的报错；
  - 没有图片参数：把截图文件路径写进任务文本，让 Harness 自己读文件看图。
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

from .errors import ProviderError

DEFAULT_TIMEOUT = 240.0

# 手动指定安装位置的环境变量（默认走常见安装目录）
EXE_ENV = "NVLDLA_DSH_EXE"
EXE_NAME = "DeepSeek Harness.exe"
# bin.js 在 asar 虚拟文件系统里，普通 stat 探不到，按安装结构拼相对路径
_BIN_SUFFIX = Path("resources") / "app.asar" / "dsh" / "node_modules" / \
    "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def _exe_candidates() -> List[Path]:
    out: List[Path] = []
    env = (os.environ.get(EXE_ENV) or "").strip()
    if env:
        out.append(Path(env))
    out.append(Path(r"D:\DSH") / EXE_NAME)
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        out.append(Path(local) / "Programs" / "DeepSeek Harness" / EXE_NAME)
    pf = (os.environ.get("ProgramFiles") or "").strip() or r"C:\Program Files"
    out.append(Path(pf) / "DeepSeek Harness" / EXE_NAME)
    return out


def find_install() -> Optional[Path]:
    """返回 DeepSeek Harness.exe 路径；找不到返回 None。"""
    for p in _exe_candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _bin_js_for(exe: Path) -> str:
    return str(exe.parent / _BIN_SUFFIX)


def _registry_value(name: str) -> str:
    """读 HKCU\\Environment 里的用户级环境变量（进程环境被裁剪时的兜底）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            val, _ = winreg.QueryValueEx(key, name)
            return (val or "").strip()
    except Exception:
        return ""


def deepseek_key() -> str:
    """headless 官方路由需要的 Key（注入启动环境，避免 MISSING_CREDENTIAL）。"""
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        v = _registry_value(name)
        if v:
            return v
    return ""


def _image_paths(images: Optional[List[Union[str, Dict]]]) -> List[str]:
    paths: List[str] = []
    for item in images or []:
        if isinstance(item, str):
            if item:
                paths.append(item)
        elif isinstance(item, dict):
            p = item.get("path") or item.get("file") or item.get("local_path") or ""
            if p:
                paths.append(str(p))
    return paths


@dataclass
class DshResult:
    """一轮 headless 问答的结果。"""

    text: str = ""
    reasoning: str = ""          # stderr（推理/启动日志）
    exit_code: int = 0
    elapsed: float = 0.0
    usage: Dict = field(default_factory=dict)


class DshHeadlessClient:
    """调一次 `dsh headless` 完成一轮问答（非流式：一个进程一次回答）。"""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, exe: str = ""):
        self.timeout = timeout
        self._exe_override = exe.strip()

    @property
    def exe(self) -> Optional[Path]:
        if self._exe_override:
            p = Path(self._exe_override)
            return p if p.is_file() else None
        return find_install()

    def chat(
        self,
        message: str,
        images: Optional[List[Union[str, Dict]]] = None,
        *,
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        **_ignored,
    ) -> DshResult:
        exe = self.exe
        if exe is None:
            raise ProviderError(
                "未找到 DeepSeek Harness（DeepSeek Harness.exe）。"
                "请安装 DeepSeek Harness，或设置环境变量 "
                f"{EXE_ENV} 指定其安装路径。"
            )
        paths = _image_paths(images)
        task = message
        if paths:
            task = (
                "【截图问答】图片已保存为本地文件，请先用你的读图能力打开"
                "下面的图片文件、看清其中内容，再回答问题。\n"
                "图片路径：" + "、".join(paths) + "\n"
                "【问题】\n" + message
            )
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        key = deepseek_key()
        if key:
            env["DEEPSEEK_API_KEY"] = key
        # 工作目录：有图取图片所在目录（读文件权限通常按目录范围），
        # 纯文本（如二次提取）退到系统临时目录
        cwd = str(Path(paths[0]).parent) if paths else (
            os.environ.get("TEMP") or os.environ.get("TMP") or "."
        )
        cmd = [str(exe), _bin_js_for(exe), "headless", "-"]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                input=task.encode("utf-8"),
                capture_output=True,
                timeout=self.timeout,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(
                f"dsh headless 超时（{int(self.timeout)} 秒无响应）。"
                "可稍后重试，或在 Harness 里确认模型通道可用。"
            ) from None
        except OSError as exc:
            raise ProviderError(f"dsh headless 启动失败：{exc}") from None
        elapsed = time.monotonic() - started
        text = (proc.stdout or b"").decode("utf-8", "replace").strip()
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            tail = (err or text or "").strip()[-500:]
            if "MISSING_CREDENTIAL" in tail:
                tail += (
                    "\n（提示：在系统环境变量里配 DEEPSEEK_API_KEY，"
                    "或在 Harness 网页 Models 页保存官方 Key）"
                )
            raise ProviderError(
                f"dsh headless 退出码 {proc.returncode}：{tail or '无错误输出'}"
            )
        if on_chunk and text:
            on_chunk(text)
        if on_reasoning and err:
            on_reasoning(err)
        return DshResult(
            text=text,
            reasoning=err,
            exit_code=proc.returncode,
            elapsed=elapsed,
        )
