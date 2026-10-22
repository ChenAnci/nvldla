# -*- coding: utf-8 -*-
"""流式输出示例。"""
import webchat

print("=== DeepSeek 流式 ===")
webchat.chat(
    "deepseek",
    "写一首关于秋天的短诗",
    stream=True,
    on_chunk=lambda t: print(t, end="", flush=True),
)

print("\n\n=== 豆包流式（深度思考）===")
webchat.chat(
    "doubao",
    "解释一下什么是量子纠缠，用大白话",
    think=1,
    stream=True,
    on_chunk=lambda t: print(t, end="", flush=True),
    on_reasoning=lambda t: print(f"\n[思考] {t}", end="", flush=True),
)
print()
