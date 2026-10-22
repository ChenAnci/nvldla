"""NVlDlA —— 入口脚本。

用法：
    python nvidia.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
