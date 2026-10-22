# -*- coding: utf-8 -*-
"""统一入口示例：一个函数调用所有已封装的网页版模型。"""
import webchat

# ── DeepSeek 网页版（需先 webchat.login('deepseek') 或手动设置 token）──
r = webchat.chat("deepseek", "你好，请用一句话介绍你自己")
print("DeepSeek:", r.text)
print("session_id:", r.conversation_id)

# 深度思考模型
r2 = webchat.chat(
    "deepseek", "9.11 和 9.9 哪个大？",
    model="deepseek-reasoner",
)
print("DeepSeek-R1 思考过程:", r2.reasoning[:120])
print("DeepSeek-R1 回答:", r2.text)

# 多轮续聊（需要 session_id + 上一轮的 message_id）
r3 = webchat.chat(
    "deepseek", "我刚才问的是什么问题？",
    session_id=r2.conversation_id,
    parent_message_id=r2.extras["message_id"],
)
print("续聊:", r3.text)

# 文件理解（多模态）
r4 = webchat.chat("deepseek", "总结一下这个文件", files=["path/to/doc.pdf"])
print("文件回答:", r4.text)
