# -*- coding: utf-8 -*-
"""从本机 DeepSeek Harness（DSH）读取它已配置的模型通道，供 NVlDlA 的 api 直连复用。

只读 DSH 的两个明文配置（同用户可读，绝不写入）：
  $DSH_HOME/profiles/desktop/cordis.patch.yml   供应商 + 模型（不含密钥值，
      apiKeyEnv 只存环境变量名）
  $DSH_HOME/.credentials.yaml                   refs.<名字> = 密钥值

DSH 自己的凭据优先级是「存储文件 > 启动环境」，这里保持一致；
文件里没有的回退读同名环境变量，最后再回退 OPENAI_API_KEY/OPENAI_BASE_URL。
YAML 只按本项目需要的子集解析（嵌套映射 / `- ` 列表 / 标量），不引第三方依赖。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from config import DEFAULT_API_MODEL


# ---------------------------------------------------------- 极简 YAML 子集
def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


_KEY_RE = re.compile(r"^(?P<key>[^:#][^:]*?):(?:\s+(?P<val>.*))?$")
_ITEM_RE = re.compile(r"^- (?P<rest>.*)$")


def _scalar(text: str):
    v = text.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    low = v.lower()
    if low in ("", "null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _parse_yaml_subset(text: str):
    """解析嵌套映射 + `- ` 列表（含行内首键的映射项）+ 标量。

    不支持锚点/别名/多行块标量——DSH 的 cordis.patch.yml 用不到。
    解析失败抛 ValueError，由调用方兜底。
    """
    lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return None

    def parse_block(buf, i: int, indent: int):
        line = buf[i]
        if _ITEM_RE.match(line.strip()):
            return parse_list(buf, i, indent)
        return parse_map(buf, i, indent)

    def parse_map(buf, i: int, indent: int):
        out: dict = {}
        while i < len(buf):
            line = buf[i]
            ind = _indent(line)
            if ind < indent:
                break
            if ind > indent:
                raise ValueError(f"意外缩进: {line!r}")
            m = _KEY_RE.match(line.strip())
            if not m:
                raise ValueError(f"无法解析: {line!r}")
            key = m.group("key").strip()
            val = (m.group("val") or "").strip()
            i += 1
            if val in ("", "|", ">"):
                if i < len(buf) and _indent(buf[i]) > indent:
                    child, i = parse_block(buf, i, _indent(buf[i]))
                    out[key] = child
                else:
                    out[key] = None
            else:
                out[key] = _scalar(val)
        return out, i

    def parse_list(buf, i: int, indent: int):
        out: list = []
        while i < len(buf):
            line = buf[i]
            ind = _indent(line)
            if ind < indent:
                break
            if ind > indent:
                raise ValueError(f"意外缩进: {line!r}")
            m = _ITEM_RE.match(line.strip())
            if not m:
                break  # 同级不是列表项 → 列表结束
            rest = m.group("rest").strip()
            item_indent = indent + 2  # "- " 占两格，行内首键从这里开始
            if _KEY_RE.match(rest):
                # 行内首键的映射项：造虚拟行接管，后续同级行一并解析
                virtual = " " * item_indent + rest
                item, k = parse_map([virtual] + buf[i + 1:], 0, item_indent)
                if k <= 0:
                    raise ValueError(f"列表项解析无进展: {line!r}")
                out.append(item)
                i += k  # 虚拟行对应 buf[i]，消耗量与真实行一一对应
            else:
                out.append(_scalar(rest))
                i += 1
        return out, i

    value, _ = parse_block(lines, 0, _indent(lines[0]))
    return value


# ---------------------------------------------------------- DSH 数据读取
@dataclass
class DshChannel:
    """一条可直连的模型通道（OpenAI 兼容三要素 + 元信息）。"""

    base_url: str
    api_key: str
    model: str
    provider_id: str = ""
    display: str = ""
    supports_image: bool = False
    source: str = ""          # 人类可读的来源说明

    @property
    def masked_key(self) -> str:
        k = self.api_key or ""
        if len(k) <= 10:
            return k
        return k[:6] + "…" + k[-4:]


def dsh_home() -> Path:
    env = (os.environ.get("DSH_HOME") or "").strip()
    return Path(env) if env else (Path.home() / ".dsh")


def _patch_path(home: Path) -> Path:
    return home / "profiles" / "desktop" / "cordis.patch.yml"


def load_dsh_providers(home: Path | None = None) -> dict:
    """读 profile patch 里所有 `config.providers.<id>` 条目（无密钥值）。"""
    home = home or dsh_home()
    path = _patch_path(home)
    data = _parse_yaml_subset(path.read_text(encoding="utf-8"))
    providers: dict = {}
    entries = data if isinstance(data, list) else [data]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cfg = entry.get("config")
        if isinstance(cfg, dict) and isinstance(cfg.get("providers"), dict):
            for pid, prov in cfg["providers"].items():
                if isinstance(prov, dict):
                    providers[pid] = prov
    return providers


def _credential_ref(home: Path, name: str) -> str:
    """从 .credentials.yaml 的 refs 块按键名取值（整行不回显，按键读取）。"""
    path = home / ".credentials.yaml"
    if not path.is_file():
        return ""
    in_refs = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw and not raw[0].isspace():     # 顶层键
            in_refs = raw.startswith("refs:")
            continue
        if not in_refs or ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        if key.strip() == name:
            return _scalar(val) or ""
    return ""


def _resolve_key(home: Path, env_name: str) -> str:
    """与 DSH 一致：存储文件优先，其次环境变量。"""
    if not env_name:
        return ""
    v = _credential_ref(home, env_name)
    if v:
        return v
    return (os.environ.get(env_name) or "").strip()


def _channel_from_provider(pid: str, prov: dict, key: str) -> list[DshChannel]:
    base = str(prov.get("baseURL") or "").strip()
    display = str(prov.get("displayName") or "").strip()
    out: list[DshChannel] = []
    models = prov.get("models")
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        inputs = [str(x) for x in (m.get("input") or [])]
        out.append(DshChannel(
            base_url=base,
            api_key=key,
            model=str(m["id"]),
            provider_id=pid,
            display=display,
            supports_image="image" in inputs,
            source=f"DeepSeek Harness · {pid}",
        ))
    return out


def import_dsh_channel(home: Path | None = None):
    """挑出 DSH 已配置、可直接给 OpenAI 兼容接口用的通道。

    返回 (channel | None, 提示文案)。挑选顺序：
    支持图片输入的通道 > 其余 DSH 通道 > 环境变量 OPENAI_API_KEY 兜底。
    """
    home = home or dsh_home()
    reasons: list[str] = []

    try:
        providers = load_dsh_providers(home)
    except FileNotFoundError:
        providers = {}
        reasons.append(f"未找到 {_patch_path(home)}")
    except (ValueError, OSError) as exc:
        providers = {}
        reasons.append(f"解析 {_patch_path(home).name} 失败：{exc}")

    candidates: list[DshChannel] = []
    for pid, prov in providers.items():
        base = str(prov.get("baseURL") or "").strip()
        env = str(prov.get("apiKeyEnv") or "").strip()
        if not base or not env:
            continue
        key = _resolve_key(home, env)
        if not key:
            reasons.append(f"供应商 {pid} 的密钥 {env} 未找到")
            continue
        candidates.extend(_channel_from_provider(pid, prov, key))

    if candidates:
        ch = next((c for c in candidates if c.supports_image), candidates[0])
        note = "、".join(reasons) if reasons else "全部通道已枚举"
        return ch, f"已导入（{ch.source}）· {ch.display or '—'} · 模型 {ch.model}" \
                   f"{' · 支持图片' if ch.supports_image else ''} · Key {ch.masked_key}"

    # 兜底：环境变量里现成的官方直连配置（DSH 也会读启动环境）
    env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env_key:
        base = (os.environ.get("OPENAI_BASE_URL")
                or os.environ.get("OPENAI_API_BASE") or "").strip()
        ch = DshChannel(
            base_url=base,
            api_key=env_key,
            model=DEFAULT_API_MODEL,
            provider_id="env",
            display="OPENAI_API_KEY",
            supports_image=True,
            source="环境变量",
        )
        extra = "；" + "、".join(reasons) if reasons else ""
        return ch, f"已导入环境变量通道 · {base or '官方默认'} · 模型 {ch.model}" \
                   f" · Key {ch.masked_key}{extra}"

    detail = "；".join(reasons) if reasons else f"{home} 下无可用通道"
    return None, f"未从 DeepSeek Harness 找到可用通道：{detail}"
