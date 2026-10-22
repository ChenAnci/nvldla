"""豆包（www.doubao.com）扫码登录 —— 纯 HTTP，无需浏览器。

流程（移植自 doubao2api 项目的 QRLogin，Apache-2.0）：
  1. 访问 doubao.com 主页收集基础 Cookie（ttwid 等）
  2. 取 passport_csrf_token
  3. 取二维码（PNG），用户用豆包 App 扫码
  4. 轮询 check_qrconnect：new → scanned → confirmed
  5. 跟随 redirect_url 收集 sessionid 等完整 Cookie
  6. 生成全新设备指纹参数（fp / device_id / web_id）并保存 session 文件
"""
from __future__ import annotations

import base64
import json
import random
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote, urljoin

import requests

from .errors import AuthError

BASE_URL = "https://www.doubao.com"
AID = 497858
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{BASE_URL}/chat/login",
    "Origin": BASE_URL,
}
POLL_INTERVAL = 1.5
TIMEOUT_SEC = 120


def generate_device_params() -> Dict[str, str]:
    """生成全新的设备指纹参数，避免沿用旧会话的风控分数。"""
    chars = string.ascii_letters + string.digits
    fp = "verify_" + "_".join(
        "".join(random.choices(chars, k=k))
        for k in (8, 4, 4, 4, 12)
    )
    device_id = str(random.randint(7000000000000000, 7999999999999999))
    web_id = str(random.randint(7600000000000000000, 7699999999999999999))
    return {"fp": fp, "device_id": device_id, "web_id": web_id}


def _collect_cookies(sess: requests.Session, resp: requests.Response) -> None:
    for c in resp.cookies:
        sess.cookies.set(c.name, c.value, domain=c.domain or "doubao.com", path=c.path or "/")


def _get(sess: requests.Session, url: str, headers: Optional[Dict[str, str]] = None,
         timeout: int = 10, follow: bool = False) -> requests.Response:
    """GET with manual redirect handling so every hop's Set-Cookie is kept."""
    h = dict(BASE_HEADERS)
    if headers:
        h.update(headers)
    resp = sess.get(url, headers=h, timeout=timeout, allow_redirects=False)
    _collect_cookies(sess, resp)
    hops = 0
    while resp.status_code in (301, 302, 303, 307, 308) and follow and hops < 10:
        loc = resp.headers.get("Location", "")
        if not loc:
            break
        if not loc.startswith("http"):
            loc = urljoin(url, loc)
        resp = sess.get(loc, headers=h, timeout=timeout, allow_redirects=False)
        _collect_cookies(sess, resp)
        hops += 1
    return resp


def qr_login(session_path: str | Path, timeout_sec: int = TIMEOUT_SEC,
             on_status=None, save_qr: bool = True,
             auto_open: bool = True) -> Dict:
    """执行豆包扫码登录并保存 session 文件。

    Returns session dict: {"cookies": {...}, "params": {...}}.
    ``on_status`` callback receives (status_str, message).
    ``auto_open`` 控制是否自动用系统看图器弹出二维码；
     GUI 调用方传 False，自行在界面上展示二维码图片。
    """
    session_path = Path(session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    status_cb = on_status or (lambda s, m: print(f"[{datetime.now():%H:%M:%S}] {m}"))

    sess = requests.Session()
    status_cb("fetching", "正在获取二维码...")

    # 1) homepage -> base cookies (ttwid)
    _get(sess, BASE_URL + "/")
    csrf = sess.cookies.get("passport_csrf_token") or sess.cookies.get("passport_csrf_token_default")
    if not csrf:
        r = _get(sess, f"{BASE_URL}/passport/safe/csrf_token/?aid={AID}")
        try:
            data = r.json()
            if data.get("error_code") == 0:
                csrf = data.get("data", {}).get("passport_csrf_token", "")
        except (ValueError, AttributeError):
            pass
        csrf = csrf or sess.cookies.get("passport_csrf_token", "")

    # 2) qrcode
    next_url = quote(BASE_URL, safe="")
    r = _get(sess, f"{BASE_URL}/passport/web/get_qrcode/?next={next_url}&aid={AID}",
             headers={"x-tt-passport-csrf-token": csrf})
    try:
        data = r.json()
    except ValueError as e:
        raise AuthError(f"获取二维码失败：响应不是 JSON（{r.status_code}）") from e
    qr_data = data.get("data", {})
    if qr_data.get("error_code", data.get("error_code", -1)) != 0 or data.get("message") != "success":
        raise AuthError(f"获取二维码失败: error_code={qr_data.get('error_code', data.get('error_code'))}")

    token = qr_data.get("token", "")
    if not token:
        raise AuthError("二维码响应中无 token")

    qr_raw = qr_data.get("qrcode", "") or qr_data.get("qrcode_url", "")
    qr_bytes = b""
    if qr_raw:
        if qr_raw.startswith("data:"):
            qr_bytes = base64.b64decode(qr_raw.partition(",")[2])
        elif qr_raw.startswith("http"):
            qr_bytes = requests.get(qr_raw, timeout=10).content
        else:
            qr_bytes = base64.b64decode(qr_raw)

    qr_path = session_path.with_name("doubao_qr.png")
    if qr_bytes and save_qr:
        qr_path.write_bytes(qr_bytes)
        status_cb("qr_ready", f"二维码已保存：{qr_path} —— 请用豆包 App「扫一扫」登录")
        # 终端无法显示图片，自动用系统默认看图器弹出二维码；GUI 调用方传 auto_open=False
        if auto_open:
            try:
                import os, sys
                if sys.platform.startswith("win"):
                    os.startfile(str(qr_path))  # noqa: SLF001
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", str(qr_path)])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", str(qr_path)])
            except Exception:
                status_cb("qr_manual", f"（自动打开失败，请手动双击打开）{qr_path}")

    # 3) poll
    start = time.monotonic()
    last_status = ""
    while time.monotonic() - start < timeout_sec:
        r = _get(sess,
                 f"{BASE_URL}/passport/web/check_qrconnect/?next={next_url}&token={token}&aid={AID}",
                 headers={"x-tt-passport-csrf-token": csrf})
        try:
            data = r.json()
        except ValueError:
            time.sleep(2.0)
            continue
        qr_data = data.get("data", {})
        if qr_data.get("error_code", data.get("error_code", -1)) != 0 or data.get("message") != "success":
            desc = str(qr_data.get("description", "")).lower()
            if "expired" in desc or "过期" in desc:
                raise AuthError("二维码已过期，请重试")
            time.sleep(2.0)
            continue

        qr_status = qr_data.get("status", "")
        if qr_status != last_status:
            last_status = qr_status
            if qr_status == "new":
                status_cb("waiting_scan", "等待扫码...")
            elif qr_status == "scanned":
                status_cb("scanned", "已扫码，请在手机上确认")
            elif qr_status == "confirmed":
                status_cb("confirmed", "登录成功，正在获取会话...")
            elif qr_status == "expired":
                raise AuthError("二维码已过期，请重试")

        if qr_status == "confirmed":
            redirect_url = qr_data.get("redirect_url", "")
            if redirect_url:
                _get(sess, redirect_url, follow=True, timeout=15)
            cookies = {c.name: c.value for c in sess.cookies}
            if not cookies.get("sessionid"):
                raise AuthError("登录成功但未获得 sessionid Cookie")
            params = generate_device_params()
            session = {"cookies": cookies, "params": params}
            session_path.write_text(
                json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            status_cb("done", f"会话已保存：{session_path}")
            return session

        time.sleep(POLL_INTERVAL)

    raise AuthError("扫码超时（120 秒），请重试")
