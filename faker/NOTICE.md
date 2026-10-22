# NOTICE —— 代码来源与许可

本项目的协议实现参考并移植了以下开源项目（均为 Apache-2.0 许可），
在此说明归属：

1. **DeepSeek 网页版协议**
   - 来源：booleamu/deepseek-mcp-server
     （https://github.com/booleamu/deepseek-mcp-server）
   - 移植内容：`webchat/deepseek.py` 的接口流程、请求头、SSE 解析、
     消息序列化、PoW 应答格式；
     `webchat/pow_solver.mjs`（Node WASM glue）与
     `webchat/sha3_wasm_bg.wasm`（DeepSeek 官方 PoW 求解 WASM，
     源码 crate: sha3-wasm，作者 rz.pan，随上游项目分发）。

2. **豆包网页版协议**
   - 来源：wangchuxiaoji-oss/doubao2api
     （https://github.com/wangchuxiaoji-oss/doubao2api）
   - 移植内容：`webchat/doubao.py` 的 /chat/completion 请求负载、
     流式解析、图片/文件上传流程（含 AWS SigV4 签名）；
     `webchat/doubao_login.py` 的扫码登录流程。

Apache-2.0 许可要求保留版权声明；两项目均为公开开源项目，
本仓库不包含其完整源码副本（仅移植协议逻辑并显著改写为同步
requests 实现）。

本项目 webchat 自身采用 Apache-2.0 许可发布。
