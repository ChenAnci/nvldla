# NVlDlA —— 悬浮截图问答助手（screenshotask + faker/webchat 单仓库）

> 按 **F8**（可改）截取整个屏幕 → 发给豆包 / DeepSeek 网页版 → 完整回答后自动提炼出最终答案，流式显示在最前端悬浮窗。
> 悬浮窗通过 `SetWindowDisplayAffinity` 从系统截图中排除，Ctrl+Win+S / PrtSc 截不到。
>
> ⚠️ 仅供学习与个人研究使用，请勿用于商业用途或高频自动化。请自行承担账号风控风险。

## 📦 仓库结构

```
nvldla/
├── faker/            # webchat 包：DeepSeek / 豆包网页版封装（纯 HTTP，免费复用登录态）
│   ├── webchat/      #   主包：chat() / login() 统一入口，支持多模态、流式、多轮
│   ├── examples/     #   使用示例
│   ├── config.example.json
│   └── README.md     #   webchat 独立说明
├── screenshotask/    # NVlDlA GUI：托盘常驻 + 全局热键截图问答 + 防截图悬浮窗
│   ├── main.py       #   主程序（入口 nvidia.py）
│   ├── nvldla_config.example.json
│   └── requirements.txt
├── requirements.txt  # 统一依赖
├── start.cmd         # 一键初始化 + 启动（Windows）
└── README.md
```

## 🚀 快速开始（Windows）

### 免安装：直接下载 exe（不想装 Python 就用这个）

- **国内用户（推荐）：**[Gitee 发行版](https://gitee.com/orangeflavour/nvldla/releases) 下载 `NVlDlA.exe`，双击即可运行
- 海外用户：**[GitHub Releases](https://github.com/orangefplus/nvldla/releases)** 同样可下载

- 无需安装 Python / Node.js / 任何依赖，**单文件即用**
- 配置与登录会话保存在 **exe 所在目录**（`nvldla_config.json`、`webchat\sessions\`），整个文件夹拷走即随身携带
- 登录：托盘图标右键 →「设置」→「账号登录」→ 豆包扫码
- ⚠️ **exe 版仅支持豆包**；DeepSeek 需要 Node.js + playwright，请用下方源码方式运行
- 个别杀毒软件可能对 PyInstaller 单文件误报，添加信任即可（或改用源码方式运行）

### 环境要求

- **Python 3.10+**（已加入 PATH；或使用 `py` 启动器）
- **Node.js ≥ 18**（仅 DeepSeek 需要；只用豆包可不装）
- **Google Chrome**（仅 `login deepseek` 自动登录需要）

### 1. 下载

```bash
# 国内用户（推荐，更快）
git clone https://gitee.com/orangeflavour/nvldla.git
# 海外用户
git clone https://github.com/orangefplus/nvldla.git
cd nvldla
```

### 2. 启动

**方式一（推荐）：一键脚本** —— 自动定位 Python、创建虚拟环境、安装依赖并启动：

```bat
start.cmd
```

首次运行 `start.cmd` 会自动完成：① 创建 `faker\.venv` 虚拟环境 → ② 用 `pip` 安装 `requirements.txt` 里的全部依赖 → ③ 启动程序。已初始化过则直接启动。

**方式二：手动创建虚拟环境并启动**（想完全掌控每一步时用，以下命令在仓库根目录执行）：

```bat
:: 1. 创建虚拟环境（只做一次）
python -m venv faker\.venv

:: 2. 安装依赖（只做一次）
faker\.venv\Scripts\pip install -r requirements.txt

:: 3. 启动 NVlDlA（托盘常驻，后台运行不弹黑窗）
faker\.venv\Scripts\pythonw.exe screenshotask\nvidia.py
```

如果系统只装了 `py` 启动器，把上面的 `python` 换成 `py -3` 即可。

**依赖清单**（`requirements.txt`，全部为 pip 包）：

| 依赖 | 用途 |
|---|---|
| `requests` ≥ 2.31 | 豆包/DeepSeek 网页接口 HTTP 请求 |
| `httpx` ≥ 0.27 | webchat 流式/上传内部依赖 |
| `PyQt6` ≥ 6.6 | 悬浮窗、托盘、设置界面（GUI） |
| `playwright` ≥ 1.40 | 仅 DeepSeek 浏览器登录需要；只用豆包可不装 |

**非 pip 的外部依赖**：Node.js ≥ 18（仅 DeepSeek 的 PoW 求解需要，只用豆包可跳过）；Google Chrome（仅 DeepSeek 自动登录需要）。

### 3. 登录（只做一次）

启动后如未登录，在程序里直接登录即可：**托盘图标右键 →「设置」→「账号登录」**：

- **登录豆包（扫码）**：对话框内直接显示二维码，用手机豆包 App「扫一扫」
- **登录 DeepSeek（浏览器）**：自动打开系统 Chrome，在网页中登录后自动保存登录态

也可以在命令行登录（两种方式等价）：

```bat
faker\.venv\Scripts\python.exe -m webchat login doubao    :: 豆包：手机 App 扫码
faker\.venv\Scripts\python.exe -m webchat login deepseek  :: DeepSeek：浏览器登录
```

### 4. 使用

- 默认全局热键 **F8**：截图 → 提问 → 完整回答 → 自动提炼最终答案，流式显示在悬浮窗（可在设置里关闭二次提取）
- 托盘图标右键：设置 / 显示隐藏悬浮窗 / 退出
- 设置里可改热键、提供商（豆包/DeepSeek）、模型、透明度等；小键盘 1~9 控制悬浮窗移动 / 滚动 / 透明度

## 🔧 常见问题

| 现象 | 处理 |
|---|---|
| 提示登录过期 / 会话失效（401 / sessionid） | 重新执行上面的 `login` 命令 |
| `start.cmd` 首次安装依赖很慢 | 网络问题，重试即可 |
| DeepSeek 报错缺少 Node | 安装 Node.js ≥ 18（`pow_solver.mjs` 依赖） |
| 想用别的热键 / 提供商 | 托盘菜单「设置」中修改，或编辑 `screenshotask/nvldla_config.json`（首次运行自动生成） |

## 🔒 隐私说明

- 登录凭证（`config.json`、`webchat/sessions/`、cookies）只保存在本地，已被 `.gitignore` 排除，不会提交到仓库。
- 本项目直连豆包 / DeepSeek 官网网页接口，不上传任何数据到第三方服务器。

## 📄 开源说明

`faker/webchat` 的协议参考移植自开源项目（Apache-2.0），详见 `faker/NOTICE.md`。
