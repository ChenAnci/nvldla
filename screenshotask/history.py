"""NVlDlA 提问历史本地留档 —— JSONL 一问一行，仅存本地，绝不入库。

数据落点（与 nvldla_config.json 同目录，frozen/exe 与源码运行一致）：
  nvldla_history.jsonl   每次问答追加一行 JSON（时间/通道/问题/答案/状态）
  history_shots/         每次提问的截图副本（临时图会被下一次覆盖，这里永久留）

所有写入均为「尽力而为」：任何异常吞掉并返回 False，留档永远不能影响问答主流程。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

HISTORY_NAME = "nvldla_history.jsonl"
SHOTS_DIRNAME = "history_shots"

# 单字段字符上限：防止超长回答（如 dsh 全量推理）把历史文件撑得过大
_MAX_FIELD = 100_000


def history_path(base_dir: Path) -> Path:
    return Path(base_dir) / HISTORY_NAME


def shots_dir(base_dir: Path) -> Path:
    return Path(base_dir) / SHOTS_DIRNAME


def _cap(text: str) -> str:
    if len(text) <= _MAX_FIELD:
        return text
    return text[:_MAX_FIELD] + f"\n…（过长已截断，原长 {len(text)} 字符）"


def append_record(base_dir: Path, record: dict) -> bool:
    """追加一条问答记录。失败静默返回 False。"""
    try:
        path = history_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        slim = dict(record)
        for key in ("prompt", "answer", "full", "error"):
            val = slim.get(key)
            if isinstance(val, str):
                slim[key] = _cap(val)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def load_records(base_dir: Path) -> list[dict]:
    """读取全部记录（时间正序：旧在前）。坏行跳过。"""
    path = history_path(base_dir)
    out: list[dict] = []
    try:
        if not path.exists():
            return out
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        pass
    return out


def archive_shot(base_dir: Path, src) -> str:
    """把会被下一次提问覆盖的临时截图复制进 history_shots/。

    返回相对路径（如 history_shots/20261023-105500_nvldla_shot.png），
    源缺失或复制失败返回 ""。
    """
    if not src:
        return ""
    try:
        src = Path(src)
        if not src.exists():
            return ""
        dst_dir = shots_dir(base_dir)
        dst_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = dst_dir / f"{stamp}_{src.name}"
        n = 1
        while dst.exists():
            dst = dst_dir / f"{stamp}_{n}_{src.name}"
            n += 1
        shutil.copy2(str(src), str(dst))
        return f"{SHOTS_DIRNAME}/{dst.name}"
    except (OSError, ValueError):
        return ""
