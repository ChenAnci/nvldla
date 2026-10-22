"""豆包网页版（www.doubao.com）客户端封装 —— 纯 HTTP 同步实现。

协议移植自 doubao2api 项目（Apache-2.0）：
  - 统一入口 POST /chat/completion（SSE 流式）
  - 认证：sessionid 等 Cookie（扫码登录获取）+ passport_csrf_token
  - 设备参数：aid/device_id/web_id/fp（登录时生成全新指纹）
  - 多模态：图片走 /samantha/pages/upload_image + /alice/message/get_file_url；
    文件走 prepare_upload → ApplyImageUpload → TOS 上传 → CommitImageUpload

风控说明：msToken 留空即可（伪造值反而触发 710022002 限流）；
遇到 710022002/710022004 验证码挑战会抛出 RateLimitError，重新扫码登录即可恢复。
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests

from .config import get_doubao_cookie_header
from .errors import AuthError, CaptchaError, RateLimitError, UploadError, WebchatError
from .sse import iter_sse

BASE_URL = "https://www.doubao.com"
DEFAULT_BOT_ID = "7234781073513644036"
EXTENSION_BOT_ID = "7338286299411103781"

RISK_ERROR_CODES = {710022002, 710022004}

DEFAULT_FP = "verify_mlcfw5f7_TPq0YmFD_NrsC_4RuQ_BJPg_M5W7i58I7wV0"
DEFAULT_DEVICE_ID = "714003710229497"
DEFAULT_WEB_ID = "7604137868021548590"

UPLOAD_SUPPORTED_EXTENSIONS = {
    "pdf", "txt", "csv", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
    "md", "mobi", "epub", "py", "java", "js", "ts", "c", "cpp", "h",
    "hpp", "html", "css", "php", "rb", "pl", "sh", "bash", "swift",
    "kt", "go", "dart", "scala", "cs", "xaml", "vue", "json", "yaml",
    "yml", "xml", "env", "ini", "toml", "plist", "feature", "bat",
    "cmd", "ps1", "vbs", "lua", "mod", "sum", "png", "jpeg", "jpg", "webp",
    "proto", "dockerfile", "rs", "tsx", "jsx",
}


@dataclass
class UploadedFile:
    uri: str = ""
    name: str = ""
    size: int = 0
    file_type: str = ""


@dataclass
class CompletionChunk:
    text: str = ""
    thinking: str = ""
    block_type: int = 0
    tool_info: str = ""
    search_info: Optional[Dict] = None
    error_code: int = 0
    error_msg: str = ""
    is_done: bool = False
    conversation_id: str = ""


@dataclass
class ChatResult:
    text: str = ""
    thinking_text: str = ""
    tool_events: List[str] = field(default_factory=list)
    search_results: List[Dict] = field(default_factory=list)
    conversation_id: str = ""


# 调试开关：DOUBAO_DEBUG=1 时，把未识别的 SSE/JSON 块写入 CWD/doubao_sse_debug.log
_DEBUG_RAW = os.environ.get("DOUBAO_DEBUG") == "1"


def _debug_raw(obj) -> None:
    if not _DEBUG_RAW:
        return
    try:
        with open(Path.cwd() / "doubao_sse_debug.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _as_dict(node, default: Optional[Dict] = None) -> Dict:
    """把 str/dict 统一成 dict（str 先 JSON 解析）。"""
    if isinstance(node, str):
        try:
            node = json.loads(node)
        except (json.JSONDecodeError, TypeError):
            return dict(default) if default else {}
    return node if isinstance(node, dict) else (dict(default) if default else {})


def _extract_samantha_chunk(obj) -> Optional[CompletionChunk]:
    """兼容新版 /samantha 风格响应：{event_type, event_data:{message:{content_type, content}}}。

    content_type：2001/2003/10000=正文文本；2008=思考块（content.think 思考 / content.text 正文）；
    10040=思考内容块。2003 无文本时视为结束标记，由调用方收尾。
    """
    if "event_type" not in obj and "event_data" not in obj:
        return None
    event_data = _as_dict(obj.get("event_data"))
    msg = _as_dict(event_data.get("message"))
    if not msg:
        return None
    content_type = int(msg.get("content_type", 0) or 0)
    content = _as_dict(msg.get("content"))

    text = content.get("text", "")
    think = content.get("think", "")
    text = text if isinstance(text, str) else ""
    think = think if isinstance(think, str) else ""

    if content_type in (2001, 2003, 10000):
        if text:
            return CompletionChunk(text=text, block_type=10000)
    elif content_type == 2008:
        if think:
            return CompletionChunk(thinking=think, block_type=10040)
        if text:
            return CompletionChunk(text=text, block_type=10000)
    elif content_type == 10040:
        t = think or content.get("finish_title", "") or content.get("title", "") or text
        if isinstance(t, str) and t:
            return CompletionChunk(thinking=t, block_type=10040)
    return None


def _extract_message_chunk(obj) -> Optional[CompletionChunk]:
    """兼容 {message: {content: {content_type, content: {...}}}} 风格。"""
    msg = _as_dict(obj.get("message"))
    content = _as_dict(msg.get("content"))
    if not content:
        return None
    inner = _as_dict(content.get("content"))
    if not inner:
        inner = content
    ct = int(content.get("content_type", inner.get("content_type", 0)) or 0)
    text = inner.get("text", "")
    think = inner.get("think", "")
    text = text if isinstance(text, str) else ""
    think = think if isinstance(think, str) else ""
    if text and ct in (2001, 2003, 10000, 2008):
        return CompletionChunk(text=text, block_type=10000)
    if think and ct in (2008, 10040):
        return CompletionChunk(thinking=think, block_type=10040)
    return None


def _aws_sign_v4(
    method: str,
    url: str,
    body: Union[bytes, str],
    access_key: str,
    secret_key: str,
    session_token: str,
    region: str = "cn-north-1",
    service: str = "imagex",
) -> Dict[str, str]:
    """AWS Signature V4（用于字节 veImageX 文件上传）。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    query_params = parse_qs(parsed.query, keep_blank_values=True)
    sorted_params = sorted((k, v[0] if v else "") for k, v in query_params.items())
    canonical_qs = "&".join(
        f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted_params
    )

    headers_to_sign: Dict[str, str] = {"host": host, "x-amz-date": amz_date}
    if session_token:
        headers_to_sign["x-amz-security-token"] = session_token
    signed_headers = ";".join(sorted(headers_to_sign.keys()))
    canonical_headers = "".join(
        f"{k}:{v}\n" for k, v in sorted(headers_to_sign.items())
    )

    body_bytes = body if isinstance(body, bytes) else body.encode()
    payload_hash = hashlib.sha256(body_bytes).hexdigest()

    canonical_request = (
        f"{method}\n{path}\n{canonical_qs}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    cr_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{cr_hash}"

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac_mod.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(f"AWS4{secret_key}".encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac_mod.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    result = {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if session_token:
        result["x-amz-security-token"] = session_token
    return result


class DoubaoClient:
    """Synchronous Doubao web chat client."""

    def __init__(
        self,
        cookies: Optional[Dict[str, str]] = None,
        device_id: str = DEFAULT_DEVICE_ID,
        web_id: str = DEFAULT_WEB_ID,
        fp: str = DEFAULT_FP,
        bot_id: str = EXTENSION_BOT_ID,
        timeout: float = 180,
    ):
        cookies = cookies or {}
        if not cookies.get("sessionid"):
            raise AuthError("豆包 sessionid 缺失，请先 webchat.login('doubao')")
        self.cookies = cookies
        self.device_id = device_id
        self.web_id = web_id
        self.fp = fp
        self.bot_id = bot_id
        self.timeout = timeout

        self._session = requests.Session()
        self._session.cookies.update(cookies)
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                ),
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/chat",
            }
        )

    # ------------------------------------------------------------- factories
    @classmethod
    def from_session(cls, session_path: str | Path | None = None) -> "DoubaoClient":
        """从 session 文件创建客户端（webchat.login('doubao') 生成）。"""
        if session_path is None:
            from .config import data_dir
            session_path = data_dir() / "webchat" / "sessions" / "doubao.json"
        session_path = Path(session_path)
        if not session_path.exists():
            raise AuthError(f"会话文件不存在：{session_path}，请先运行 python -m webchat login doubao")
        data = json.loads(session_path.read_text(encoding="utf-8"))
        cookies = {str(k): str(v) for k, v in (data.get("cookies") or {}).items()}
        params = data.get("params") or {}
        return cls(
            cookies=cookies,
            device_id=str(params.get("device_id") or DEFAULT_DEVICE_ID),
            web_id=str(params.get("web_id") or DEFAULT_WEB_ID),
            fp=str(params.get("fp") or DEFAULT_FP),
        )

    @classmethod
    def from_cookie_header(cls, cookie_header: str) -> "DoubaoClient":
        cookies = {}
        for item in cookie_header.split(";"):
            item = item.strip()
            if item and "=" in item:
                name, value = item.split("=", 1)
                cookies[name.strip()] = value.strip()
        return cls(cookies=cookies)

    @classmethod
    def from_env(cls) -> "DoubaoClient":
        env_cookie = get_doubao_cookie_header()
        if env_cookie:
            return cls.from_cookie_header(env_cookie)
        return cls.from_session()

    # ------------------------------------------------------------- security
    def _security_params(self) -> Dict[str, str]:
        params = {
            "aid": "582478",
            "real_aid": "582478",
            "device_id": self.device_id,
            "tea_uuid": self.device_id,
            "web_id": self.web_id,
            "device_platform": "web",
            "language": "zh",
            "region": "CN",
            "sys_region": "CN",
            "pkg_type": "release_version",
            "version_code": "20800",
            "pc_version": "2.1.7",
            "chromium_version": "148.0.7816.0",
            "client_platform": "pc_client",
            "runtime": "web",
            "runtime_version": "3.5.4",
            "samantha_web": "1",
            "use-olympus-account": "1",
            "fp": self.fp,
            "web_tab_id": str(uuid.uuid4()),
        }
        # msToken：留空（伪造值会触发 710022002 限流），有真实值才带
        ms = self.cookies.get("msToken", "")
        if ms:
            params["msToken"] = ms
        return params

    def _csrf_header(self) -> Dict[str, str]:
        return {
            "x-tt-passport-csrf-token": (
                self.cookies.get("passport_csrf_token")
                or self.cookies.get("passport_csrf_token_default")
                or ""
            )
        }

    # -------------------------------------------------------------- payload
    def _build_completion_content_blocks(
        self,
        text: str,
        image_attachments: Optional[List[Dict[str, str]]] = None,
        file_attachments: Optional[List[UploadedFile]] = None,
    ) -> List[Dict]:
        blocks: List[Dict] = []

        if file_attachments:
            attachments_list = []
            for f in file_attachments:
                attachments_list.append({
                    "type": 3,
                    "identifier": str(uuid.uuid4()),
                    "file": {
                        "uri": f.uri,
                        "url": "",
                        "file_type": 0,
                        "name": f.name,
                        "size": f.size,
                    },
                    "parse_state": 1,
                    "review_state": 1,
                    "upload_status": 1,
                    "progress": 100,
                    "src": "",
                })
            blocks.append({
                "block_type": 10052,
                "content": {
                    "attachment_block": {"attachments": attachments_list},
                    "pc_event_block": "",
                },
                "block_id": str(uuid.uuid4()),
                "parent_id": "",
                "meta_info": [],
                "append_fields": [],
            })

        for img in image_attachments or []:
            blocks.append({
                "block_type": 10052,
                "content": {
                    "attachment_block": {
                        "attachments": [{
                            "type": 1,
                            "identifier": str(uuid.uuid4()),
                            "image": {
                                "name": img.get("name", "image.png"),
                                "uri": img["uri"],
                                "image_ori": {
                                    "url": img["cdn_url"],
                                    "width": int(img.get("width", 64)),
                                    "height": int(img.get("height", 64)),
                                    "format": img.get("format", "png"),
                                    "url_formats": {},
                                },
                            },
                            "parse_state": 0,
                            "review_state": 0,
                        }],
                    },
                    "pc_event_block": "",
                },
                "block_id": str(uuid.uuid4()),
                "parent_id": "",
                "meta_info": [],
                "append_fields": [],
                "is_finish": True,
                "patch_type": 2,
            })

        blocks.append({
            "block_type": 10000,
            "content": {
                "text_block": {
                    "text": text,
                    "icon_url": "",
                    "icon_url_dark": "",
                    "summary": "",
                },
                "pc_event_block": "",
            },
            "block_id": str(uuid.uuid4()),
            "parent_id": "",
            "meta_info": [],
            "append_fields": [],
            "is_finish": True,
            "patch_type": 2,
        })
        return blocks

    def _build_completion_payload(
        self,
        text: str,
        need_deep_think: int = 0,
        bot_id: Optional[str] = None,
        image_attachments: Optional[List[Dict[str, str]]] = None,
        file_attachments: Optional[List[UploadedFile]] = None,
    ) -> Dict:
        content_blocks = self._build_completion_content_blocks(
            text, image_attachments, file_attachments
        )
        return {
            "client_meta": {
                "local_conversation_id": f"local_{uuid.uuid4().hex[:16]}",
                "conversation_id": "",
                "bot_id": bot_id or self.bot_id,
                "last_section_id": "",
                "last_message_index": 0,
            },
            "messages": [{
                "local_message_id": str(uuid.uuid4()),
                "content_block": content_blocks,
                "message_status": 0,
            }],
            "option": {
                "send_message_scene": "",
                "create_time_ms": 0,
                "collect_id": "",
                "is_audio": False,
                "answer_with_suggest": True,
                "tts_switch": False,
                "need_deep_think": need_deep_think,
                "click_clear_context": False,
                "from_suggest": False,
                "is_regen": False,
                "is_replace": False,
                "disable_sse_cache": False,
                "select_text_action": "",
                "resend_for_regen": False,
                "scene_type": 0,
                "unique_key": str(uuid.uuid4()),
                "start_seq": 0,
                "need_create_conversation": True,
                "conversation_init_option": {"need_ack_conversation": True},
                "regen_query_id": [],
                "edit_query_id": [],
                "regen_instruction": "",
                "no_replace_for_regen": False,
                "message_from": 0,
                "shared_app_name": "",
                "action_bar_skill_id": 0,
                "sse_recv_event_options": {"support_chunk_delta": True},
                "is_ai_playground": False,
            },
            "chat_ability": {},
            "ext": {
                "use_deep_think": str(need_deep_think),
                "fp": self.fp,
                "use_submit_pipeline": "1",
                "commerce_credit_config_enable": "0",
                "sub_conv_firstmet_type": "1",
            },
        }

    # ---------------------------------------------------------------- chat
    def chat(
        self,
        text: str,
        think: int = 0,
        bot_id: Optional[str] = None,
        images: Optional[List[Union[str, Path, Dict[str, str]]]] = None,
        files: Optional[List[Union[str, Path, UploadedFile]]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
        on_think: Optional[Callable[[str], None]] = None,
        conversation_id: Optional[str] = None,
    ) -> ChatResult:
        """发送消息并收集完整回复。

        Args:
            text: 用户消息文本。
            think: 0=普通, 1=深度思考, 2=自动, 3=专家模式。
            bot_id: 覆盖默认 bot。
            images: 本地图片路径或已上传的附件 dict（uri/cdn_url/...）。
            files: 本地文件路径或 UploadedFile。
            on_chunk / on_think: 流式回调（正文 / 思考过程）。
            conversation_id: 上一轮返回的会话 ID（多轮续聊）。
        """
        image_attachments: List[Dict[str, str]] = []
        if images:
            for img in images:
                if isinstance(img, dict):
                    image_attachments.append(img)
                else:
                    image_attachments.append(self.upload_image(Path(img).read_bytes(), Path(img).name))

        file_attachments: List[UploadedFile] = []
        if files:
            for f in files:
                if isinstance(f, UploadedFile):
                    file_attachments.append(f)
                else:
                    path = Path(f)
                    file_attachments.append(self.upload_file(path.read_bytes(), path.name))

        result = ChatResult()
        for chunk in self.chat_stream(
            text=text,
            think=think,
            bot_id=bot_id,
            image_attachments=image_attachments,
            file_attachments=file_attachments,
            conversation_id=conversation_id,
        ):
            if chunk.is_done:
                break
            if chunk.conversation_id:
                result.conversation_id = chunk.conversation_id
            if chunk.text:
                result.text += chunk.text
                if on_chunk:
                    on_chunk(chunk.text)
            if chunk.thinking:
                result.thinking_text += chunk.thinking
                if on_think:
                    on_think(chunk.thinking)
            if chunk.tool_info:
                result.tool_events.append(chunk.tool_info)
            if chunk.search_info:
                result.search_results.append(chunk.search_info)
        return result

    def chat_stream(
        self,
        text: str,
        think: int = 0,
        bot_id: Optional[str] = None,
        image_attachments: Optional[List[Dict[str, str]]] = None,
        file_attachments: Optional[List[UploadedFile]] = None,
        conversation_id: Optional[str] = None,
    ):
        """流式迭代 CompletionChunk。"""
        payload = self._build_completion_payload(
            text, think, bot_id, image_attachments, file_attachments
        )
        if conversation_id:
            payload["client_meta"]["conversation_id"] = conversation_id
            payload["option"]["need_create_conversation"] = False

        url = f"{BASE_URL}/chat/completion?{urlencode(self._security_params())}"
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            **self._csrf_header(),
        }

        with self._session.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                raise WebchatError(f"/chat/completion 失败 ({resp.status_code}): {resp.text[:500]}")

            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                body = resp.text
                try:
                    err_obj = json.loads(body)
                except json.JSONDecodeError:
                    err_obj = None
                if isinstance(err_obj, dict) and "code" in err_obj:
                    raise AuthError(
                        f"/chat/completion 鉴权错误: code={err_obj.get('code')} "
                        f"msg={err_obj.get('msg') or err_obj.get('message', body[:200])}"
                    )
                # 兼容新版接口直接返回 JSON 明文（非 SSE 流）的情况
                if isinstance(err_obj, dict):
                    chunk = _extract_samantha_chunk(err_obj) or _extract_message_chunk(err_obj)
                    if chunk is not None:
                        yield chunk
                        yield CompletionChunk(is_done=True)
                        return
                raise WebchatError(
                    f"/chat/completion 非预期响应 (content-type={content_type}): {body[:300]}"
                )

            thinking_count = 0
            in_thinking = False

            for block in iter_sse(resp):
                obj = block.get("json")
                if obj is None:
                    continue
                sse_event_name = block.get("event", "")

                if sse_event_name == "gateway-error":
                    raise WebchatError(
                        f"gateway-error: code={obj.get('code')} message={obj.get('message', str(obj))}"
                    )

                if sse_event_name == "SSE_ACK":
                    ack = obj.get("ack_client_meta", {})
                    if isinstance(ack, dict):
                        cid = str(ack.get("conversation_id", "")).strip()
                        if cid and cid != "0":
                            yield CompletionChunk(conversation_id=cid)
                    continue

                if sse_event_name == "STREAM_ERROR" or "error_code" in obj:
                    code = int(obj.get("error_code", 0))
                    msg = str(obj.get("error_msg", ""))
                    if code in RISK_ERROR_CODES:
                        extra = obj.get("extra") or {}
                        decision_str = extra.get("decision", "") if isinstance(extra, dict) else ""
                        if decision_str and "verify" in decision_str:
                            raise CaptchaError(
                                f"豆包触发验证码风控 ({code})：{msg}。请重新扫码登录或到网页版完成验证。",
                                code=code, extra=extra,
                            )
                        raise RateLimitError(
                            f"豆包风控/限流 ({code})：{msg}", code=code, extra=extra
                        )
                    if code:
                        yield CompletionChunk(error_code=code, error_msg=msg)
                        continue

                # chunk_delta 紧凑格式: {"text": "..."}
                if isinstance(obj.get("text"), str) and "error_code" not in obj:
                    t = obj["text"]
                    if in_thinking:
                        yield CompletionChunk(thinking=t, block_type=10040)
                    else:
                        yield CompletionChunk(text=t, block_type=10000)
                    continue

                # content_block 数组（patch_op / content）
                def _iter_blocks(data):
                    for patch in data.get("patch_op", []):
                        pv = patch.get("patch_value", {})
                        if isinstance(pv, dict):
                            yield from pv.get("content_block", [])
                    dc = data.get("content", {})
                    if isinstance(dc, dict):
                        yield from dc.get("content_block", [])

                for cb in _iter_blocks(obj):
                    bt = cb.get("block_type", 0)
                    content = cb.get("content", {}) or {}

                    if bt == 10040:
                        thinking_count += 1
                        in_thinking = thinking_count == 1
                        yield CompletionChunk(block_type=bt)
                    elif bt == 10000:
                        tb = content.get("text_block", {})
                        if isinstance(tb, dict) and tb.get("text"):
                            t = tb["text"]
                            if in_thinking:
                                yield CompletionChunk(thinking=t, block_type=bt)
                            else:
                                yield CompletionChunk(text=t, block_type=bt)
                    elif bt == 10101:
                        lb = content.get("loading_block", {})
                        tl = lb.get("text_loading", {})
                        if tl.get("text"):
                            yield CompletionChunk(tool_info=tl["text"], block_type=bt)
                    elif bt == 10024:
                        title = content.get("generic_tool_block", {}).get("title", "")
                        if title:
                            yield CompletionChunk(tool_info=f"[tool] {title}", block_type=bt)
                    elif bt == 10025:
                        sqrb = content.get("search_query_result_block", {})
                        if sqrb:
                            summary = sqrb.get("summary", "")
                            parsed = [
                                {
                                    "title": r.get("text_card", {}).get("title", ""),
                                    "url": r.get("text_card", {}).get("url", ""),
                                    "summary": r.get("text_card", {}).get("summary", ""),
                                    "source": r.get("text_card", {}).get("source_name", ""),
                                }
                                for r in sqrb.get("results", []) or []
                                if r.get("text_card")
                            ]
                            if parsed:
                                yield CompletionChunk(
                                    tool_info=f"[search] {summary}",
                                    search_info={
                                        "type": "search",
                                        "summary": summary,
                                        "queries": sqrb.get("queries", []),
                                        "is_finish": cb.get("is_finish", False),
                                        "block_id": cb.get("block_id", ""),
                                        "results": parsed,
                                    },
                                    block_type=bt,
                                )
                            else:
                                yield CompletionChunk(tool_info=f"[search] {summary}", block_type=bt)

                # patch_op 里的 content 字符串（新 STREAM_CHUNK 格式）
                for patch in obj.get("patch_op", []):
                    pv = patch.get("patch_value", {})
                    if isinstance(pv, dict) and isinstance(pv.get("content"), str):
                        content_str = pv["content"]
                        if content_str:
                            try:
                                content_obj = json.loads(content_str)
                                t = content_obj.get("text", "")
                                if t:
                                    if in_thinking:
                                        yield CompletionChunk(thinking=t, block_type=10000)
                                    else:
                                        yield CompletionChunk(text=t, block_type=10000)
                            except (json.JSONDecodeError, TypeError):
                                pass

                # ---- 兼容新版 /samantha 风格与 message.content 风格块 ----
                # （新版网页端可能返回 event_type/event_data/message 结构，旧解析覆盖不到）
                chunk = _extract_samantha_chunk(obj) or _extract_message_chunk(obj)
                if chunk is not None:
                    yield chunk
                elif any(k in obj for k in ("event_type", "event_data", "message", "content_type")):
                    _debug_raw(obj)

                if obj.get("verbose_type"):
                    yield CompletionChunk(
                        tool_info=f"[{obj['verbose_type']}] {str(obj.get('verbose_payload', ''))[:200]}"
                    )

            yield CompletionChunk(is_done=True)

    # --------------------------------------------------------------- upload
    def upload_file(self, file_data: bytes, filename: str) -> UploadedFile:
        """上传任意支持格式的文件（TOS 存储），返回 UploadedFile。"""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        file_size = len(file_data)
        crc32 = format(zlib.crc32(file_data) & 0xFFFFFFFF, "08x")
        params = self._security_params()

        # Step 1: prepare_upload -> STS 凭证
        r = self._session.post(
            f"{BASE_URL}/alice/resource/prepare_upload?{urlencode(params)}",
            json={"tenant_id": "5", "scene_id": "5", "resource_type": 1},
            timeout=self.timeout,
        )
        body = r.json()
        if body.get("code") != 0:
            raise UploadError(f"prepare_upload 失败: code={body.get('code')} msg={body.get('msg')}")
        data = body["data"]
        service_id = data["service_id"]
        auth_token = data["upload_auth_token"]
        ak, sk, st = auth_token["access_key"], auth_token["secret_key"], auth_token["session_token"]

        # Step 2: ApplyImageUpload
        file_ext = f".{ext}" if ext else ""
        apply_url = (
            f"{BASE_URL}/top/v1?"
            f"Action=ApplyImageUpload&Version=2018-08-01"
            f"&ServiceId={service_id}&NeedFallback=true"
            f"&FileSize={file_size}&FileExtension={file_ext}"
            f"&s=jdnfglwfkl"
        )
        sign_headers = _aws_sign_v4("GET", apply_url, "", ak, sk, st)
        r = self._session.get(apply_url, headers=sign_headers, timeout=self.timeout)
        body = r.json()
        result_data = body.get("Result")
        if not result_data:
            err = body.get("ResponseMetadata", {}).get("Error", {})
            raise UploadError(f"ApplyImageUpload 失败: {err.get('Code')} {err.get('Message')}")
        upload_addr = result_data["UploadAddress"]
        store_info = upload_addr["StoreInfos"][0]
        store_uri = store_info["StoreUri"]
        tos_auth = store_info["Auth"]
        session_key = upload_addr["SessionKey"]
        upload_hosts = upload_addr.get("UploadHosts", [])

        # Step 3: 上传到 TOS
        tos_host = upload_hosts[0] if upload_hosts else "tos-mya2lf.vodupload.com"
        upload_url = f"https://{tos_host}/upload/v1/{store_uri}"
        r = requests.post(
            upload_url,
            data=file_data,
            headers={"Authorization": tos_auth, "Content-CRC32": crc32},
            timeout=self.timeout,
        )
        tos_resp = r.json()
        if tos_resp.get("code") != 2000:
            raise UploadError(f"TOS 上传失败: {tos_resp.get('message', tos_resp)}")

        # Step 4: CommitImageUpload
        commit_url = (
            f"{BASE_URL}/top/v1?Action=CommitImageUpload&Version=2018-08-01"
            f"&ServiceId={service_id}"
        )
        commit_body = json.dumps({"SessionKey": session_key})
        sign_headers2 = _aws_sign_v4("POST", commit_url, commit_body, ak, sk, st)
        sign_headers2["Content-Type"] = "application/json"
        r = self._session.post(commit_url, data=commit_body, headers=sign_headers2, timeout=self.timeout)
        body = r.json()
        results = body.get("Result", {}).get("Results", [])
        if not results or results[0].get("UriStatus") != 2000:
            raise UploadError(f"CommitImageUpload 失败: {body}")

        return UploadedFile(uri=store_uri, name=filename, size=file_size, file_type=ext)

    def upload_image(self, image_bytes: bytes, filename: str = "image.png") -> Dict[str, str]:
        """上传图片，返回附件 dict（uri/cdn_url/name/format/width/height）。"""
        params = self._security_params()
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "png"

        upload_url = f"{BASE_URL}/samantha/pages/upload_image?{urlencode(params)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/chat",
            **self._csrf_header(),
        }
        files = {"data": (filename, image_bytes, f"image/{ext}")}
        r = requests.post(
            upload_url,
            files=files,
            data={"file_type": ext},
            cookies=self.cookies,
            headers=headers,
            timeout=60,
        )
        if r.status_code != 200:
            raise UploadError(f"图片上传失败 ({r.status_code}): {r.text[:500]}")
        body = r.json()
        if body.get("code") != 0:
            raise UploadError(f"图片上传错误: {body.get('msg', body)}")
        uri_short = body["data"]["uri"]

        file_url_endpoint = f"{BASE_URL}/alice/message/get_file_url?{urlencode(params)}"
        r = self._session.post(
            file_url_endpoint,
            json={
                "uris": [uri_short],
                "type": "image",
                "format": ext,
                "expire_second": 3600,
            },
            timeout=self.timeout,
        )
        body = r.json()
        if body.get("code") != 0:
            raise UploadError(f"get_file_url 错误: {body.get('msg', body)}")
        data = body.get("data", {})
        file_urls = data.get("file_urls", []) if isinstance(data, dict) else []
        if not file_urls:
            raise UploadError("get_file_url 返回空 file_urls")
        file_info = file_urls[0]

        return {
            "uri": file_info["uri"],
            "cdn_url": file_info["main_url"],
            "name": filename,
            "format": ext,
            "width": "64",
            "height": "64",
        }

    def get_file_download_url(self, uri: str, expire_seconds: int = 3600) -> str:
        """获取已上传文件的临时 CDN 下载链接。"""
        params = self._security_params()
        ext = uri.rsplit(".", 1)[-1] if "." in uri else ""
        r = self._session.post(
            f"{BASE_URL}/alice/message/get_file_url?{urlencode(params)}",
            json={
                "uris": [uri],
                "type": "file",
                "format": ext,
                "expire_second": expire_seconds,
            },
            timeout=self.timeout,
        )
        body = r.json()
        if body.get("code") != 0:
            raise UploadError(f"get_file_url 错误: {body.get('msg', body)}")
        file_urls = body.get("data", {}).get("file_urls", []) if isinstance(body.get("data"), dict) else []
        if not file_urls:
            raise UploadError("get_file_url 返回空 file_urls")
        return file_urls[0].get("main_url", "")
