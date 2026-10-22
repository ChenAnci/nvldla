"""OpenAI 兼容接口客户端 —— 用 API Key 直连官方 / 中转接口。

provider == "api" 的调用路径：
  - 地址：api_base + /chat/completions（已含该后缀的地址原样使用）
  - 鉴权：Authorization: Bearer <api_key>
  - 图片：本地路径 → base64 data URI（content 数组里的 image_url 段）
  - 流式：requests stream + webchat.sse.iter_sse 解析

Key / 地址解析优先级：显式参数 → 环境变量（OPENAI_API_KEY 等）→ webchat config.json。
"""
from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

import requests

from .errors import AuthError, ProviderError, RateLimitError, WebchatError
from .sse import iter_sse

DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"  # DeepSeek 官方 V4.1 Flash（原生多模态；1017 快照同名）

ENV_API_KEY_NAMES = (
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY", "MOONSHOT_API_KEY",
)
ENV_API_BASE_NAMES = (
    "OPENAI_BASE_URL", "OPENAI_API_BASE", "API_BASE_URL",
)

# 请求超时：(连接, 读取)。深度思考模型出首字前可能要等较久，读取放宽到 5 分钟。
TIMEOUT = (15, 300)


def resolve_api_key(explicit: str = "") -> str:
    """Key 来源优先级：显式参数 → 环境变量 → webchat config.json。"""
    key = (explicit or "").strip()
    if key:
        return key
    for name in ENV_API_KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        from .config import load_config
        value = str(load_config().get("api", {}).get("key", "")).strip()
        if value:
            return value
    except Exception:
        pass
    return ""


def resolve_api_base(explicit: str = "") -> str:
    """地址来源优先级：显式参数 → 环境变量 → webchat config.json → 官方默认。"""
    base = (explicit or "").strip()
    if base:
        return base
    for name in ENV_API_BASE_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        from .config import load_config
        value = str(load_config().get("api", {}).get("base", "")).strip()
        if value:
            return value
    except Exception:
        pass
    return DEFAULT_API_BASE


def endpoint_of(api_base: str = "") -> str:
    """拼出 chat/completions 端点；用户填的地址可能已含 /v1 或完整路径。"""
    base = resolve_api_base(api_base).rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _image_uri(image: Union[str, Dict]) -> str:
    """图片 → data URI：http(s)/data 原样透传，本地路径读出来转 base64。"""
    if isinstance(image, dict):
        if image.get("url"):
            return str(image["url"])
        if image.get("data"):
            return str(image["data"])
        if image.get("b64"):
            return "data:image/png;base64," + str(image["b64"])
        image = image.get("path") or image.get("file") or ""
    text = str(image)
    if text.startswith(("http://", "https://", "data:")):
        return text
    path = Path(text)
    if not path.is_file():
        raise ProviderError(f"图片不存在：{text}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_messages(message: str, images=None) -> list:
    """纯文本 → 单段 content；带图 → text + image_url 数组（OpenAI 多模态格式）。"""
    if not images:
        return [{"role": "user", "content": message}]
    content = [{"type": "text", "text": message}]
    for img in images:
        content.append({"type": "image_url", "image_url": {"url": _image_uri(img)}})
    return [{"role": "user", "content": content}]


@dataclass
class ApiResult:
    """统一返回结构（对齐其它 provider 的 ChatResult 字段子集）。"""

    text: str = ""
    reasoning: str = ""
    model: str = ""
    usage: Dict = field(default_factory=dict)


def _join_content(content) -> str:
    """content 可能是 str，也可能是分段 list（少数网关返回）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or ""))
            else:
                parts.append(str(p))
        return "".join(parts)
    return "" if content is None else str(content)


def _raise_for_status(resp) -> None:
    """非 2xx → 抛出带上游说明的分类异常（尽力解析 error.message）。"""
    status = resp.status_code
    if 200 <= status < 300:
        return
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or "")
        elif isinstance(err, str):
            detail = err
        elif isinstance(body, dict):
            detail = str(body.get("message") or "")
    except Exception:
        detail = ""
    if not detail:
        detail = (getattr(resp, "text", "") or "").strip()[:300]
    detail = detail or f"HTTP {status}"

    if status in (401, 403):
        raise AuthError(f"API Key 无效或无权限（{status}）：{detail}")
    if status == 429:
        raise RateLimitError(f"API 限流或余额不足（429）：{detail}", code=429)
    lowered = detail.lower()
    if status == 400 and any(
        k in lowered for k in ("image", "vision", "图片", "视觉", "multimodal")
    ):
        raise ProviderError(
            f"该模型不支持图片输入，请在设置里换支持视觉的模型"
            f"（或换支持截图的接口地址）：{detail}"
        )
    if status == 404 and "model" in lowered:
        raise ProviderError(f"模型名不存在（请在设置里核对模型）：{detail}")
    if status in (400, 404, 422) and "model" in lowered and any(
        k in lowered for k in (
            "not found", "not exist", "not available", "unknown", "invalid",
            "unrecognized", "unsupported", "不存在", "无法找到", "不支持",
        )
    ):
        raise ProviderError(
            f"模型名不存在或该接口未提供此模型（{status}）：{detail}。"
            "请在设置里核对模型名（DeepSeek 官方 V4.1 Flash，含 1017 快照，"
            "模型名是 deepseek-flash）"
        )
    raise ProviderError(f"接口返回错误（{status}）：{detail}")


class OpenAICompatClient:
    """OpenAI 兼容 chat/completions 客户端（同步、支持流式）。"""

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        model: str = DEFAULT_MODEL,
        timeout=TIMEOUT,
    ):
        self.api_key = resolve_api_key(api_key)
        self.api_base = api_base
        self.model = (model or "").strip() or DEFAULT_MODEL
        self.timeout = timeout

    # ---------------------------------------------------------- 请求构造
    def _headers(self) -> dict:
        if not self.api_key:
            raise AuthError(
                "未配置 API Key：请在「设置 → API 接口」填写，"
                "或设置 OPENAI_API_KEY 环境变量"
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict, stream: bool) -> requests.Response:
        try:
            resp = requests.post(
                endpoint_of(self.api_base),
                headers=self._headers(),
                json=payload,
                stream=stream,
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise WebchatError(f"请求接口超时：{exc}") from exc
        except requests.RequestException as exc:
            raise WebchatError(f"请求接口失败（检查 API 地址/网络）：{exc}") from exc
        _raise_for_status(resp)
        return resp

    # ---------------------------------------------------------- 对外入口
    def chat(
        self,
        message: str,
        images=None,
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_reasoning: Optional[Callable[[str], None]] = None,
    ) -> ApiResult:
        payload = {
            "model": self.model,
            "messages": build_messages(message, images),
            "stream": bool(stream),
        }
        resp = self._post(payload, stream=stream)
        if stream:
            return self._read_stream(resp, on_chunk, on_reasoning)
        return self._read_json(resp)

    # ---------------------------------------------------------- 响应解析
    def _read_json(self, resp: requests.Response) -> ApiResult:
        try:
            body = resp.json()
        except Exception as exc:
            raise ProviderError(f"接口返回了非 JSON 内容：{exc}") from exc
        finally:
            resp.close()
        if isinstance(body, dict) and body.get("error"):
            _raise_for_status(_FakeErrResponse(body))
        choices = (body.get("choices") or [{}]) if isinstance(body, dict) else [{}]
        message = (choices[0] or {}).get("message") or {}
        return ApiResult(
            text=_join_content(message.get("content")),
            reasoning=_join_content(
                message.get("reasoning_content") or message.get("reasoning")
            ),
            model=str(body.get("model") or self.model) if isinstance(body, dict) else self.model,
            usage=body.get("usage") or {} if isinstance(body, dict) else {},
        )

    def _read_stream(
        self,
        resp: requests.Response,
        on_chunk: Optional[Callable[[str], None]],
        on_reasoning: Optional[Callable[[str], None]],
    ) -> ApiResult:
        text_parts: List[str] = []
        reason_parts: List[str] = []
        usage: Dict = {}
        try:
            for block in iter_sse(resp):
                data = (block.get("data") or "").strip()
                if data == "[DONE]":
                    break
                obj = block.get("json")
                if not isinstance(obj, dict):
                    continue
                if obj.get("error"):
                    _raise_for_status(_FakeErrResponse(obj))
                if obj.get("usage"):
                    usage = obj["usage"]
                choices = obj.get("choices") or []
                if not choices:
                    continue
                choice = choices[0] or {}
                delta = choice.get("delta") or choice.get("message") or {}
                reasoning = (
                    delta.get("reasoning_content") or delta.get("reasoning") or ""
                )
                if reasoning:
                    reason_parts.append(str(reasoning))
                    if on_reasoning is not None:
                        on_reasoning(str(reasoning))
                content = delta.get("content")
                if content:
                    text = _join_content(content)
                    text_parts.append(text)
                    if on_chunk is not None:
                        on_chunk(text)
        except requests.RequestException as exc:
            raise WebchatError(f"读取流式响应中断：{exc}") from exc
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return ApiResult(
            text="".join(text_parts),
            reasoning="".join(reason_parts),
            model=self.model,
            usage=usage,
        )


class _FakeErrResponse:
    """把 2xx 响应体里的 error 包装成 _raise_for_status 认识的形状。"""

    status_code = 400

    def __init__(self, body: dict):
        self._body = body

    @property
    def text(self) -> str:
        import json
        return json.dumps(self._body, ensure_ascii=False)

    def json(self) -> dict:
        return self._body
