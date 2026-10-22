"""webchat —— 把 AI 聊天网页版封装成可编程调用。

支持：
  - deepseek：chat.deepseek.com 网页版（纯 HTTP + PoW，含文件/图片多模态）
  - doubao：www.doubao.com 网页版（纯 HTTP + 扫码登录，含图片/文件多模态）
  - api：任意 OpenAI 兼容接口（官方/中转），API Key 直连，无需登录

用法：
    import webchat
    r = webchat.chat("deepseek", "你好，介绍一下自己")
    print(r.text)

    r = webchat.chat("doubao", "用一句话介绍成都", think=1)
    print(r.text)

    webchat.login("doubao")     # 扫码登录（豆包 App）
    webchat.login("deepseek")   # 浏览器登录（自动提取 token）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Union

from .config import load_config, save_config
from .errors import AuthError, ProviderError, WebchatError  # noqa: F401
from .errors import CaptchaError, RateLimitError  # noqa: F401

__version__ = "0.1.0"

PROVIDERS = ("deepseek", "doubao", "api", "dsh")


@dataclass
class Reply:
    """统一返回结构。"""

    text: str = ""
    reasoning: str = ""          # 深度思考/推理过程
    conversation_id: str = ""    # 可传给下一次 chat 以续聊
    provider: str = ""
    model: str = ""
    usage: Dict = field(default_factory=dict)
    extras: Dict = field(default_factory=dict)


def get_client(provider: str):
    """获取 provider 对应的客户端实例（deepseek / doubao）。"""
    provider = provider.lower()
    if provider == "deepseek":
        from .deepseek import DeepSeekClient
        return DeepSeekClient()
    if provider == "doubao":
        from .doubao import DoubaoClient
        return DoubaoClient.from_env()
    if provider == "api":
        from .openai_api import OpenAICompatClient
        return OpenAICompatClient()
    if provider == "dsh":
        from .dsh_headless import DshHeadlessClient
        return DshHeadlessClient()
    raise ProviderError(f"未知 provider: {provider}（可选：{', '.join(PROVIDERS)}）")


def login(provider: str, **kwargs) -> None:
    """交互式登录并持久化会话。

    - doubao：纯 HTTP 扫码（用豆包 App 扫码），自动保存 session 文件
    - deepseek：打开浏览器（复用系统 Chrome）登录后自动提取 userToken
    """
    provider = provider.lower()
    if provider == "deepseek":
        from .deepseek import login_via_browser
        token = login_via_browser()
        print(f"DeepSeek 登录成功，token 已保存（前 8 位：{token[:8]}...）")
        return
    if provider == "doubao":
        from .doubao_login import qr_login
        session_path = _doubao_session_path()
        qr_login(session_path, on_status=lambda s, m: print(m))
        print("豆包登录成功。")
        return
    if provider == "api":
        print("api（API Key 直连）无需登录：在 NVlDlA「设置 → API 接口」填地址与 Key 即可。")
        return
    if provider == "dsh":
        print("dsh（DeepSeek Harness 本体）无需网页登录：装好 Harness、"
              "本机有 DeepSeek 官方 Key 即可（见环境变量 DEEPSEEK_API_KEY）。")
        return
    raise ProviderError(f"未知 provider: {provider}")


def set_token(provider: str, token: str) -> None:
    """手动设置 token（deepseek 的 userToken 或 doubao 的 cookie header）。"""
    provider = provider.lower()
    if provider == "deepseek":
        from .config import set_deepseek_token
        set_deepseek_token(token.strip())
        print("DeepSeek token 已保存。")
        return
    if provider == "doubao":
        cfg = load_config()
        cfg.setdefault("doubao", {})["cookie_header"] = token.strip()
        save_config(cfg)
        print("豆包 Cookie 已保存（格式：name=value; name2=value2）。")
        return
    if provider == "api":
        cfg = load_config()
        cfg.setdefault("api", {})["key"] = token.strip()
        save_config(cfg)
        print("API Key 已保存。")
        return
    if provider == "dsh":
        print("dsh 的 Key 不在此设置：用本机 DeepSeek 官方 Key"
              "（环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY，或 Harness 里保存的）。")
        return
    raise ProviderError(f"未知 provider: {provider}")


def _doubao_session_path():
    from .config import data_dir
    return data_dir() / "webchat" / "sessions" / "doubao.json"


def chat(
    provider: str,
    message: str,
    images: Optional[List[Union[str, Dict]]] = None,
    files: Optional[List[str]] = None,
    *,
    model: Optional[str] = None,
    stream: bool = False,
    on_chunk: Optional[Callable[[str], None]] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    **kwargs,
) -> Reply:
    """统一聊天入口：输入文本/图片/文件，返回模型回复。

    Args:
        provider: "deepseek" / "doubao"（网页版）、"api"（OpenAI 兼容接口）或 "dsh"（Harness headless）
        message: 用户消息
        images: 本地图片路径列表（doubao 支持；deepseek 走文件上传；api 转 base64）
        files: 本地文件路径列表（两个网页版 provider 都支持）
        model: deepseek: deepseek-chat / deepseek-reasoner；api: 任意接口模型名
        stream: True 时通过 on_chunk/on_reasoning 逐段回调（仍返回完整结果）
        kwargs:
          - think (doubao): 0=普通 1=深度思考 2=自动 3=专家
          - conversation_id / session_id: 多轮续聊
          - search_enabled (deepseek): 联网搜索
          - api_key / api_base (api): 覆盖配置里的 Key 与接口地址
    """
    provider = provider.lower()
    if provider == "deepseek":
        from .deepseek import DeepSeekClient, ChatMessage
        client = DeepSeekClient()
        session_id = kwargs.pop("session_id", None) or kwargs.pop("conversation_id", None)
        model = model or "deepseek-chat"
        result = client.chat(
            message,
            model=model,
            session_id=session_id,
            files=files,
            search_enabled=bool(kwargs.pop("search_enabled", False)),
            on_chunk=on_chunk,
            on_reasoning=on_reasoning,
            **{k: v for k, v in kwargs.items() if k in ("parent_message_id", "messages")},
        )
        return Reply(
            text=result.text,
            reasoning=result.reasoning,
            conversation_id=result.session_id,
            provider="deepseek",
            model=model,
            usage={"completion_tokens": result.total_tokens},
            extras={"message_id": result.message_id},
        )

    if provider == "doubao":
        from .doubao import DoubaoClient
        client = DoubaoClient.from_env()
        think = int(kwargs.pop("think", 0))
        conversation_id = kwargs.pop("conversation_id", None) or kwargs.pop("session_id", None)
        result = client.chat(
            message,
            think=think,
            images=images,
            files=files,
            conversation_id=conversation_id,
            on_chunk=on_chunk,
            on_think=on_reasoning,
            **{k: v for k, v in kwargs.items() if k in ("bot_id",)},
        )
        return Reply(
            text=result.text,
            reasoning=result.thinking_text,
            conversation_id=result.conversation_id,
            provider="doubao",
            model=f"doubao-think-{think}" if think else "doubao",
            extras={
                "tool_events": result.tool_events,
                "search_results": result.search_results,
            },
        )

    if provider == "api":
        from .openai_api import OpenAICompatClient
        # CLI/旧调用可能带网页版专用参数，这里全部忽略
        kwargs.pop("think", None)
        kwargs.pop("search_enabled", None)
        kwargs.pop("session_id", None)
        kwargs.pop("conversation_id", None)
        kwargs.pop("files", None)
        client = OpenAICompatClient(
            api_key=kwargs.pop("api_key", ""),
            api_base=kwargs.pop("api_base", ""),
            model=model or "",
        )
        result = client.chat(
            message,
            images=images,
            stream=stream,
            on_chunk=on_chunk,
            on_reasoning=on_reasoning,
        )
        return Reply(
            text=result.text,
            reasoning=result.reasoning,
            conversation_id="",
            provider="api",
            model=result.model or client.model,
            usage=result.usage,
        )

    if provider == "dsh":
        from .dsh_headless import DshHeadlessClient
        # 网页版/API 专用参数一律忽略（CLI 一次一轮，模型跟随 Harness 配置）
        for k in ("think", "search_enabled", "session_id", "conversation_id",
                  "files", "api_key", "api_base"):
            kwargs.pop(k, None)
        client = DshHeadlessClient()
        result = client.chat(
            message,
            images=images,
            stream=stream,
            on_chunk=on_chunk,
            on_reasoning=on_reasoning,
        )
        return Reply(
            text=result.text,
            reasoning=result.reasoning,
            conversation_id="",
            provider="dsh",
            model="harness-headless",
            usage=result.usage,
            extras={"elapsed": round(result.elapsed, 2),
                    "exit_code": result.exit_code},
        )

    raise ProviderError(f"未知 provider: {provider}（可选：{', '.join(PROVIDERS)}）")


__all__ = [
    "chat", "login", "set_token", "get_client",
    "Reply", "AuthError", "WebchatError", "CaptchaError", "RateLimitError",
    "PROVIDERS", "__version__",
]
