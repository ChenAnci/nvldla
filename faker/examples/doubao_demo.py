# -*- coding: utf-8 -*-
"""豆包网页版示例（需先 webchat.login('doubao') 扫码登录）。"""
import webchat

# 普通对话
r = webchat.chat("doubao", "用一句话介绍成都")
print("豆包:", r.text)

# 深度思考
r2 = webchat.chat("doubao", "分析一下新能源汽车行业格局", think=1)
print("思考过程:", r2.reasoning)
print("回答:", r2.text)

# 多轮续聊
r3 = webchat.chat("doubao", "再详细一点", conversation_id=r.conversation_id)
print("续聊:", r3.text)

# 图片理解（多模态）
r4 = webchat.chat("doubao", "这张图里有什么？", images=["path/to/your/image.jpg"])
print("图片回答:", r4.text)

# 文件理解
r5 = webchat.chat("doubao", "总结一下这个 PDF", files=["path/to/your/doc.pdf"])
print("文件回答:", r5.text)
