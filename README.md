# NVlDlA —— 悬浮截图问答助手

> 按 **F8**（可在设置里改）截取整个屏幕 → 把图交给任意一个「回答通道」→ 完整回答后自动提炼最终答案，流式显示在最前端悬浮窗。
> 悬浮窗用 `SetWindowDisplayAffinity` 把自己从系统截屏里排除，Ctrl+Win+S / PrtSc 截不到它。
>
> ⚠️ 仅供学习与个人研究使用，请勿用于商业用途或高频自动化，风险自担。

## 🧠 四个回答通道

设置 →「提供商」四选一：

| 提供商 | 说明 | 前置条件 |
|---|---|---|
| `doubao` | 豆包网页版，免费复用登录态 | 扫码登录一次 |
| `deepseek` | DeepSeek 网页版 | 浏览器登录一次；需要 Node.js |
| `api` | **任意 OpenAI 兼容 API**（官方 / 中转 / 自建服务） | 填 API Base、API Key、模型名 |
| `dsh` | **本机 DeepSeek Harness** 引擎作答 | 装好 Harness 且配好频道 |

### `api` 通道

- 「API Base」填 OpenAI 兼容端点（如 `https://api.deepseek.com`，带不带 `/v1` 按服务商要求），「模型」默认 `deepseek-flash`
- 「**测试连接**」：一键验证 Key 与端点是否可用
- 「**从 Harness 导入**」：把本机 DeepSeek Harness 里已配好的频道（Base / Key / 模型）一键搬进 NVlDlA，免手抄
- 截图作为图片随文本一起发给模型（多模态）

### `dsh` 通道

- 截图先存成临时图片，把「图片路径 + 问题」交给本机 Harness 的 `headless` 引擎作答，答案流式回悬浮窗
- 这类会话记录在 Harness 的独立 headless profile 里：**桌面端聊天列表看不到它**，属正常现象
- 整条链路不出本机（Harness 只连它自己配置的服务）

## 📦 仓库结构

```text
nvldla/
├── faker/                # webchat 包：DeepSeek / 豆包网页版封装（纯 HTTP，复用登录态）
│   ├── webchat/          #   主包：chat() / login() 统一入口，api、dsh 通道实现也在这里
│   ├── examples/         #   使用示例
│   └── NOTICE.md         #   上游开源项目署名（Apache-2.0）
├── screenshotask/        # NVlDlA GUI：托盘常驻 + 全局热键截图问答 + 防截图悬浮窗
│   ├── main.py           #   主程序（入口 nvidia.py）
│   ├── nvldla_config.example.json
│   └── requirements.txt
├── requirements.txt      # 统一依赖
├── start.cmd             # 一键初始化 + 启动（Windows）
└── README.md
```

## 🚀 快速开始（Windows，源码运行）

### 环境要求

- **Python 3.10+**
- **Node.js ≥ 18**（仅 DeepSeek 网页通道需要）
- **Google Chrome**（仅 DeepSeek 自动登录需要）
- **DeepSeek Harness**（可选，只有 `dsh` 通道用得到）

### 1. 下载

```bat
git clone https://github.com/ChenAnci/nvldla.git
cd nvldla
```

### 2. 启动

**方式一（推荐）：一键脚本**

```bat
start.cmd
```

首次运行自动完成：建 `faker\.venv` 虚拟环境 → 装 `requirements.txt` 全部依赖 → 启动程序；之后直接进程序。

**方式二：手动**（仓库根目录）

```bat
python -m venv faker\.venv
faker\.venv\Scripts\pip install -r requirements.txt
faker\.venv\Scripts\pythonw.exe screenshotask\nvidia.py
```

系统只有 `py` 启动器的话，把 `python` 换成 `py -3`。

**依赖一览**：

| 依赖 | 用途 |
|---|---|
| `requests` ≥ 2.31 | 网页接口 HTTP 请求 |
| `httpx` ≥ 0.27 | 流式 / 上传内部依赖 |
| `PyQt6` ≥ 6.6 | 悬浮窗、托盘、设置界面 |
| `playwright` ≥ 1.40 | 仅 DeepSeek 浏览器登录需要 |

### 3. 登录（只做一次，仅网页通道需要）

托盘图标右键 →「设置」→「账号登录」：

- **豆包**：对话框里出二维码，手机豆包 App 扫一扫
- **DeepSeek**：自动打开 Chrome，网页登录后保存登录态

命令行等价：

```bat
faker\.venv\Scripts\python.exe -m webchat login doubao
faker\.venv\Scripts\python.exe -m webchat login deepseek
```

用 `api` / `dsh` 通道可以跳过登录。

### 4. 使用

- 默认热键 **F8**：截图 → 提问 → 完整回答 → 提炼最终答案，悬浮窗流式显示（设置里可改热键、关闭二次提取）
- 托盘图标右键：设置 / 提问历史 / 显示隐藏悬浮窗 / 退出
- 小键盘 1~9：悬浮窗移动 / 滚动 / 透明度
- 托盘「提问历史」：每次提问自动留档（时间 / 通道 / 问题 / 最终答案 / 提取前完整回答 / 截图副本），可搜索、点开详情、点击截图直接打开
- 提供商、模型、透明度都在「设置」里改；`api` 通道的「测试连接」「从 Harness 导入」按钮也在设置页

## 🔧 常见问题

| 现象 | 处理 |
|---|---|
| 提示登录过期 / 会话失效（401 / sessionid） | 重跑上面的 `login` 命令（网页通道） |
| `api` 测试连接报 401 / 403 | Key 或 API Base 填错、Key 过期 / 权限不足，去服务商控制台核对 |
| `dsh` 桌面端看不到截图会话 | 正常：headless 独立 profile，见上文 `dsh` 通道说明 |
| DeepSeek 报错缺少 Node | 安装 Node.js ≥ 18（`pow_solver.mjs` 依赖） |
| `start.cmd` 首次装依赖很慢 | 网络问题，重试即可 |
| 想换热键 / 提供商 | 设置里改，或编辑 `screenshotask/nvldla_config.json`（首次运行自动生成） |

## 🔒 隐私说明

- 登录态与 Key 全部只存本地，并已被 `.gitignore` 排除：`nvldla_config.json`（含 api Key）、`nvldla_history.jsonl` 与 `history_shots/`（提问历史与截图副本）、`webchat/sessions/`、cookies、浏览器缓存 —— 不会进仓库
- 网页通道直连豆包 / DeepSeek 官网接口；`api` 通道只发给你自己填的服务商；`dsh` 通道留在本机 Harness —— 项目本身不设任何第三方服务器
- 悬浮窗默认把自己排除在系统截屏之外（依赖系统 API；基于传统抓屏方式的老工具可能仍能截到）

## 🙏 致谢

- 原版 **NVlDlA（screenshotask + faker/webchat）** 出自 **orangeflavour**：[Gitee - orangeflavour/nvldla](https://gitee.com/orangeflavour/nvldla) ｜ [GitHub - orangefplus/nvldla](https://github.com/orangefplus/nvldla)。原版 exe 发行版在作者的 [Gitee Releases](https://gitee.com/orangeflavour/nvldla/releases) / [GitHub Releases](https://github.com/orangefplus/nvldla/releases)（仅含原版豆包 / DeepSeek 功能，无 `api` / `dsh` 通道）
- 本仓库是基于原版的私人改动版，新增：`api` / `dsh` 通道、测试连接、Harness 频道导入等
- `faker/webchat` 的协议参考移植自开源项目（Apache-2.0），详见 [`faker/NOTICE.md`](faker/NOTICE.md)
