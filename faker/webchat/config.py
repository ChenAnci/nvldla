"""Configuration management.

Stores provider credentials in a single ``config.json`` next to the package
(overridable via the ``WEBChat_CONFIG`` environment variable).

PyInstaller 打包（frozen）后，可写数据统一放到 exe 所在目录：
源码运行时保持包同级目录，两种模式路径行为一致。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG_NAME = "config.json"

# Environment variable overrides (kept compatible with the reference projects)
ENV_DEEPSEEK_TOKEN = "DEEPSEEK_USER_TOKEN"
ENV_DOUBAO_COOKIE = "DOUBAO_COOKIE"


def data_dir() -> Path:
    """webchat 可写数据目录（登录会话、config.json 等都放这里）。

    优先级：
      1. WEBCHAT_DATA_DIR 环境变量显式指定；
      2. PyInstaller 打包（sys.frozen）→ exe 所在目录（可写）；
      3. 源码运行 → faker 包根目录（webchat 同级上一级）。
    """
    env = os.environ.get("WEBCHAT_DATA_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    env = os.environ.get("WEBChat_CONFIG")
    if env:
        return Path(env)
    return data_dir() / DEFAULT_CONFIG_NAME


def load_config() -> Dict[str, Any]:
    path = default_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: Dict[str, Any]) -> Path:
    path = default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def get_deepseek_token() -> str:
    """Token resolution order: env var -> config file -> empty."""
    env = os.environ.get(ENV_DEEPSEEK_TOKEN, "").strip()
    if env:
        return env
    cfg = load_config()
    return str(cfg.get("deepseek", {}).get("token", "")).strip()


def set_deepseek_token(token: str) -> None:
    cfg = load_config()
    cfg.setdefault("deepseek", {})["token"] = token
    save_config(cfg)


def get_doubao_cookie_header() -> str:
    return os.environ.get(ENV_DOUBAO_COOKIE, "").strip()
