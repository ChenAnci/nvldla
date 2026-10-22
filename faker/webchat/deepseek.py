"""DeepSeek 网页版（chat.deepseek.com）客户端封装。

原理（逆向自公开项目 booleamu/deepseek-mcp-server，Apache-2.0）：
  1. 登录后从 localStorage 取 `userToken` 作为 Bearer 凭证；
  2. 每次对话前先向 /chat/create_pow_challenge 获取 PoW 挑战，
     用官方浏览器同款 WASM（DeepSeekHashV1）在 Node 里求解；
  3. POST /chat/completion（SSE 流式）获得回答；
  4. 多模态：先 /file/upload_file 上传文件，再以 ref_file_ids 引用。

依赖：requests、Node.js（>=18，用于跑 pow_solver.mjs）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import requests

from .config import get_deepseek_token, set_deepseek_token
from .errors import AuthError, PoWError, UploadError, WebchatError
from .sse import iter_sse

BASE_URL = "https://chat.deepseek.com/api/v0"
WEB_URL = "https://chat.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_POW_SOLVER = Path(__file__).resolve().parent / "pow_solver.mjs"
_WASM_PATH = Path(__file__).resolve().parent / "sha3_wasm_bg.wasm"


@dataclass
class ChatResult:
    """Aggregated DeepSeek web response."""

    text: str = ""
    reasoning: str = ""
    message_id: Optional[int] = None
    session_id: str = ""
    total_tokens: int = 0
    raw_events: List[Dict] = field(default_factory=list)


@dataclass
class ChatMessage:
    role: str = "user"
    content: str = ""


def _messages_to_prompt(messages: Iterable[ChatMessage | Dict]) -> str:
    """Serialize a chat history into the web prompt format.

    Port of the reference ``messagesToPrompt``: merge consecutive same-role
    messages, wrap assistant turns in special markers.
    """
    items = []
    for m in messages:
        if isinstance(m, dict):
            items.append(ChatMessage(role=str(m.get("role", "user")), content=str(m.get("content", ""))))
        else:
            items.append(m)
    if not items:
        return ""
    if len(items) == 1:
        return items[0].content

    merged: List[ChatMessage] = []
    for msg in items:
        if merged and merged[-1].role == msg.role:
            merged[-1].content += "\n\n" + msg.content
        else:
            merged.append(ChatMessage(msg.role, msg.content))

    parts: List[str] = []
    for i, msg in enumerate(merged):
        role, text = msg.role, msg.content
        if role == "assistant":
            parts.append(f"<｜Assistant｜>{text}<｜end▁of▁sentence｜>")
        elif role in ("user", "system"):
            if i > 0:
                parts.append(f"<｜User｜>{text}")
            else:
                parts.append(text)
        else:
            parts.append(text)
    return "".join(parts)


class DeepSeekClient:
    """Synchronous client for the DeepSeek web API."""

    def __init__(
        self,
        token: Optional[str] = None,
        cookies: Optional[Dict[str, str]] = None,
        timeout: float = 120,
        node_bin: str = "node",
    ):
        self.token = (token or get_deepseek_token()).strip()
        self.cookies = cookies or {}
        self.timeout = timeout
        self.node_bin = node_bin
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": CHROME_UA,
                "Accept": "application/json, text/event-stream, */*",
                "Content-Type": "application/json",
                "x-client-platform": "web",
                "x-client-locale": "zh_CN",
                "Origin": WEB_URL,
                "Referer": WEB_URL,
            }
        )
        if self.cookies:
            self._session.cookies.update(self.cookies)

    # ------------------------------------------------------------------ auth
    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def check_auth(self) -> bool:
        """Verify the token by creating a chat session (lightweight)."""
        try:
            self.create_session()
            return True
        except Exception:
            return False

    def _auth_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    # ------------------------------------------------------------- pow solve
    def solve_pow(self, target_path: str = "/api/v0/chat/completion") -> str:
        """Fetch a PoW challenge and solve it via the Node WASM solver.

        Returns the base64-encoded ``X-Ds-Pow-Response`` value.
        """
        if not self.is_authenticated:
            raise AuthError("DeepSeek token 未设置，请先 login")

        r = self._session.post(
            f"{BASE_URL}/chat/create_pow_challenge",
            headers=self._auth_headers(),
            json={"target_path": target_path},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise PoWError(f"获取 PoW 挑战失败: {data.get('msg') or data}")

        challenge = data["data"]["biz_data"]["challenge"]
        if challenge.get("algorithm") != "DeepSeekHashV1":
            raise PoWError(f"不支持的 PoW 算法: {challenge.get('algorithm')}")

        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(challenge, f, ensure_ascii=False)
            tmp_path = f.name
        try:
            proc = subprocess.run(
                [self.node_bin, str(_POW_SOLVER), tmp_path, str(_WASM_PATH)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as e:
            raise PoWError("未找到 Node.js，请安装 Node >= 18 并确保 node 在 PATH 中") from e
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if proc.returncode != 0:
            raise PoWError(f"PoW 求解失败: {proc.stderr.strip() or proc.stdout.strip()}")
        try:
            answer = int(proc.stdout.strip())
        except ValueError as e:
            raise PoWError(f"PoW 求解器输出异常: {proc.stdout!r}") from e

        pow_obj = {
            "algorithm": challenge["algorithm"],
            "challenge": challenge["challenge"],
            "salt": challenge["salt"],
            "answer": answer,
            "signature": challenge["signature"],
            "target_path": challenge["target_path"],
        }
        return base64.b64encode(
            json.dumps(pow_obj, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

    # ------------------------------------------------------------- sessions
    def create_session(self, agent: str = "chat") -> str:
        r = self._session.post(
            f"{BASE_URL}/chat_session/create",
            headers=self._auth_headers(),
            json={"agent": agent},
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise WebchatError(f"创建会话失败: {data.get('msg') or data}")
        return str(data["data"]["biz_data"]["id"])

    # --------------------------------------------------------------- upload
    def upload_file(self, file_path: str | Path, file_name: Optional[str] = None) -> str:
        """Upload one file; returns its file id (usable in ref_file_ids).

        注意：requests 生成的 multipart boundary 会被服务端拒绝（实测 400
        Invalid boundary），因此上传走 httpx。
        """
        path = Path(file_path)
        if not path.exists():
            raise UploadError(f"文件不存在: {path}")
        name = file_name or path.name

        pow_response = self.solve_pow("/api/v0/file/upload_file")
        import httpx

        with open(path, "rb") as f:
            data = f.read()
        headers = {
            **self._auth_headers(),
            "x-ds-pow-response": pow_response,
            "User-Agent": CHROME_UA,
            "Origin": WEB_URL,
        }
        try:
            r = httpx.post(
                f"{BASE_URL}/file/upload_file",
                headers=headers,
                files={"file": (name, data, "application/octet-stream")},
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise UploadError(f"文件上传网络错误: {e}") from e

        if r.status_code != 200:
            raise UploadError(f"文件上传失败 ({r.status_code}): {r.text[:300]}")
        data = r.json()
        if data.get("code") != 0 or not data.get("data", {}).get("biz_data", {}).get("id"):
            raise UploadError(f"文件上传失败: {data.get('msg') or data}")
        return str(data["data"]["biz_data"]["id"])

    def wait_for_file_parsed(self, file_id: str, max_wait: float = 30.0) -> None:
        start = time.monotonic()
        while time.monotonic() - start < max_wait:
            r = self._session.get(
                f"{BASE_URL}/file/fetch_files",
                headers=self._auth_headers(),
                params={"file_ids": file_id},
                timeout=self.timeout,
            )
            data = r.json()
            file_info = (
                data.get("data", {}).get("biz_data", {}).get("files") or [{}]
            )[0]
            status = file_info.get("status")
            if status == "SUCCESS":
                return
            if status == "FAILED":
                raise UploadError(f"文件解析失败: {file_info.get('error_code') or 'unknown'}")
            time.sleep(1.0)
        raise UploadError(f"文件解析超时（{max_wait}s）")

    # ----------------------------------------------------------------- chat
    def chat(
        self,
        message: Optional[str] = None,
        messages: Optional[Iterable[ChatMessage | Dict]] = None,
        *,
        model: str = DEFAULT_MODEL,
        session_id: Optional[str] = None,
        parent_message_id: Optional[int] = None,
        files: Optional[List[str | Path]] = None,
        search_enabled: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> ChatResult:
        """Send a chat request and collect the full response.

        Args:
            message: single user message (shortcut for ``messages=[...]``).
            messages: full history; serialized with the web prompt markers.
            model: "deepseek-chat" or "deepseek-reasoner".
            session_id: reuse a previous session for multi-turn context.
            parent_message_id: previous response message id to continue from.
            files: local file paths to upload and attach (multimodal).
            search_enabled: enable web search.
            on_chunk / on_reasoning: streaming callbacks (text / thinking).
        """
        if not self.is_authenticated:
            raise AuthError("DeepSeek token 未设置，请先 webchat.login('deepseek')")

        if message is not None:
            items: List[ChatMessage] = [ChatMessage("user", message)]
        elif messages is not None:
            items = [m if isinstance(m, ChatMessage) else ChatMessage(str(m.get("role", "user")), str(m.get("content", ""))) for m in messages]
        else:
            raise ValueError("需要提供 message 或 messages")

        prompt = _messages_to_prompt(items)
        is_reasoner = model in ("deepseek-reasoner", "deepseek-r1")

        sid = session_id or self.create_session()
        pow_response = self.solve_pow("/api/v0/chat/completion")

        ref_file_ids: List[str] = []
        if files:
            for fp in files:
                fid = self.upload_file(fp)
                self.wait_for_file_parsed(fid)
                ref_file_ids.append(fid)

        body = {
            "chat_session_id": sid,
            "parent_message_id": parent_message_id if parent_message_id is not None else None,
            "prompt": prompt,
            "ref_file_ids": ref_file_ids,
            "thinking_enabled": is_reasoner,
            "search_enabled": search_enabled,
        }
        headers = {
            **self._auth_headers(),
            "x-ds-pow-response": pow_response,
            "Accept": "text/event-stream",
        }
        r = self._session.post(
            f"{BASE_URL}/chat/completion",
            headers=headers,
            json=body,
            stream=True,
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise WebchatError(f"对话请求失败 ({r.status_code}): {r.text[:500]}")

        return self._parse_sse(
            r, on_chunk=on_chunk, on_reasoning=on_reasoning, session_id=sid
        )

    # ----------------------------------------------------------- sse parsing
    def _parse_sse(
        self,
        resp: requests.Response,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
        session_id: str = "",
    ) -> ChatResult:
        """Parse the DeepSeek web SSE stream.

        实测当前站点格式（2026-09）：
          - event=ready -> {"request_message_id", "response_message_id", ...}
          - {"v":{"response":{...,"fragments":[{"type":"RESPONSE|THINKING",...}]}}}  旧格式
          - {"p":"response/thinking_content","v":"..."}    思考增量
          - {"p":"response/content","v":"..."}             正文增量
          - {"o":"APPEND","v":"..."} / 裸 {"v":"..."}      正文增量
          - {"p":"response/accumulated_token_usage","v":N}
        """
        result = ChatResult(session_id=session_id)
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        total_tokens = 0
        message_id: Optional[int] = None
        # 当前输出流类型：裸 v 增量跟随最近一次显式路径
        mode = "content"  # "content" | "thinking"

        for block in iter_sse(resp):
            chunk = block["json"]
            if chunk is None:
                continue
            event_name = block["event"]

            if event_name == "ready":
                if chunk.get("response_message_id") is not None:
                    message_id = int(chunk["response_message_id"])
                continue

            v = chunk.get("v")

            # 旧格式：首个 chunk 带完整 response 对象与 fragments
            if isinstance(v, dict) and "response" in v:
                resp_obj = v["response"]
                if resp_obj.get("message_id") is not None:
                    message_id = int(resp_obj["message_id"])
                for frag in resp_obj.get("fragments") or []:
                    ftype = frag.get("type")
                    fcontent = frag.get("content")
                    if ftype == "RESPONSE" and fcontent:
                        content_parts.append(fcontent)
                        mode = "content"
                    elif ftype == "THINKING" and fcontent:
                        reasoning_parts.append(fcontent)
                        mode = "thinking"
                continue

            # BATCH update
            if chunk.get("p") == "response" and chunk.get("o") == "BATCH" and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and item.get("p") == "accumulated_token_usage":
                        total_tokens = int(item.get("v") or 0)
                continue

            # 增量：显式路径 + 裸 v（跟随当前模式）
            p = chunk.get("p")
            if isinstance(v, str):
                if p == "response/thinking_content":
                    reasoning_parts.append(v)
                    mode = "thinking"
                elif p == "response/content":
                    content_parts.append(v)
                    mode = "content"
                elif p == "response/thinking_elapsed_secs":
                    # 思考结束标记
                    mode = "content"
                elif p is None:
                    if mode == "thinking":
                        reasoning_parts.append(v)
                    else:
                        content_parts.append(v)
                elif p == "response/accumulated_token_usage":
                    try:
                        total_tokens = int(float(v))
                    except (TypeError, ValueError):
                        pass
                continue

            # OpenAI 风格 delta 兼容
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    content_parts.append(str(delta["content"]))
                if delta.get("reasoning_content"):
                    reasoning_parts.append(str(delta["reasoning_content"]))

        result.text = "".join(content_parts)
        result.reasoning = "".join(reasoning_parts)
        result.total_tokens = total_tokens
        result.message_id = message_id

        if on_chunk and result.text:
            on_chunk(result.text)
        if on_reasoning and result.reasoning:
            on_reasoning(result.reasoning)
        return result

    # ---------------------------------------------------------------- login
    @staticmethod
    def login_instructions() -> str:
        return (
            "DeepSeek 网页版登录（二选一）：\n"
            "  A. 自动：运行 python -m webchat login deepseek，会打开浏览器，"
            "登录成功后自动保存 token。\n"
            "  B. 手动：浏览器登录 https://chat.deepseek.com → F12 → "
            "Application → Local Storage → chat.deepseek.com → 复制 userToken 的值，"
            "然后运行 python -m webchat token deepseek <token>。"
        )


def _extract_user_token(raw) -> Optional[str]:
    """新版站点把 token 存在 localStorage 的 JSON 对象里：{"value": "...", ...}。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return None
        val = obj.get("value") if isinstance(obj, dict) else None
        return str(val).strip() if val else None
    return text


def login_via_browser() -> str:
    """Launch a dedicated Chrome profile, let the user log in, extract userToken.

    Requires ``playwright`` and a system Chrome/Edge installation.
    Returns the extracted userToken and persists it to config.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise WebchatError(
            "需要安装 playwright：python -m pip install playwright（会复用系统 Chrome）"
        ) from e

    from .config import data_dir
    profile_dir = data_dir() / "webchat" / ".browser" / "deepseek"
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(WEB_URL, wait_until="domcontentloaded")
        print("请在打开的浏览器中登录 chat.deepseek.com ...")

        token = None
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            try:
                raw = page.evaluate("localStorage.getItem('userToken')")
                parsed = _extract_user_token(raw)
                if parsed:
                    token = parsed
                    break
            except Exception:
                pass
            time.sleep(2)

        if not token:
            context.close()
            raise AuthError("登录超时（15 分钟），未检测到 userToken")

        cookies = {c["name"]: c["value"] for c in context.cookies(WEB_URL)}
        context.close()

    set_deepseek_token(token)
    # persist cookies alongside for endpoints that want them
    from .config import data_dir
    cfg_path = data_dir() / "webchat" / "deepseek_cookies.json"
    cfg_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    return token
