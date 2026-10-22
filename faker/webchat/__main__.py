"""命令行入口：python -m webchat <command>

命令：
  login doubao           豆包扫码登录（手机豆包 App 扫终端提示的二维码图片）
  login deepseek         打开浏览器登录 DeepSeek 并自动保存 token
  token deepseek <token> 手动设置 DeepSeek userToken（F12 → Local Storage）
  token doubao <cookies> 手动设置豆包 Cookie（F12 → Application → Cookies）
  chat <provider> <msg>  发一条消息并打印回复
      --think 1          豆包深度思考
      --model deepseek-reasoner   DeepSeek 推理模型
      --image <path>     附带图片（可多次）
      --file <path>      附带文件（可多次）
      --stream           边生成边打印
  status                 查看登录状态
"""
from __future__ import annotations

import argparse
import sys

from . import PROVIDERS, chat, get_client, login, set_token


def _cmd_login(args) -> None:
    login(args.provider)


def _cmd_token(args) -> None:
    set_token(args.provider, args.value)


def _cmd_chat(args) -> None:
    images = args.image or None
    files = args.file or None
    on_chunk = (lambda t: print(t, end="", flush=True)) if args.stream else None
    on_reason = None
    if args.stream and args.think:
        on_reason = (lambda t: print(f"\n[思考] {t}", end="", flush=True))
    r = chat(
        args.provider,
        args.message,
        images=images,
        files=files,
        model=args.model,
        stream=args.stream,
        on_chunk=on_chunk,
        on_reasoning=on_reason,
        think=args.think,
    )
    if not args.stream:
        if r.reasoning:
            print(f"—— 思考过程 ——\n{r.reasoning}\n")
        print(r.text)
    print()


def _cmd_status(args) -> None:
    for p in PROVIDERS:
        try:
            if p == "api":
                from .openai_api import resolve_api_base, resolve_api_key
                key = resolve_api_key()
                if key:
                    mask = key[:6] + "…" + key[-4:] if len(key) > 12 else "已保存"
                    print(f"{p}: 已配置 Key（{mask}）· {resolve_api_base()}")
                else:
                    print(f"{p}: 未配置 Key（设置环境变量或 `token api <key>`）")
                continue
            client = get_client(p)
            if p == "deepseek":
                ok = client.is_authenticated and client.check_auth()
                print(f"{p}: {'已登录' if ok else '未登录/无效'}")
            else:
                print(f"{p}: 已配置 sessionid（{client.cookies.get('sessionid', '')[:8]}...）")
        except Exception as e:
            print(f"{p}: 未配置（{type(e).__name__}: {e}）")


def main() -> None:
    parser = argparse.ArgumentParser(prog="webchat", description="AI 网页版封装 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="登录")
    p_login.add_argument("provider", choices=PROVIDERS)
    p_login.set_defaults(func=_cmd_login)

    p_token = sub.add_parser("token", help="手动设置 token/cookie")
    p_token.add_argument("provider", choices=PROVIDERS)
    p_token.add_argument("value")
    p_token.set_defaults(func=_cmd_token)

    p_chat = sub.add_parser("chat", help="发消息")
    p_chat.add_argument("provider", choices=PROVIDERS)
    p_chat.add_argument("message")
    p_chat.add_argument("--think", type=int, default=0, help="豆包：0/1/2/3")
    p_chat.add_argument("--model", default=None, help="deepseek-chat / deepseek-reasoner")
    p_chat.add_argument("--image", action="append", default=None)
    p_chat.add_argument("--file", action="append", default=None)
    p_chat.add_argument("--stream", action="store_true")
    p_chat.set_defaults(func=_cmd_chat)

    p_status = sub.add_parser("status", help="登录状态")
    p_status.set_defaults(func=_cmd_status)

    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
