"""NVlDlA 悬浮问答助手 —— 配置持久化。

配置文件存放于项目目录下的 nvldla_config.json，
保存快捷键 / 透明度 / 提示词 / 提供商与模型 / 悬浮窗几何信息。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_NAME = "nvldla_config.json"

# doubao 可选的 think 级别：0=普通 1=深度思考 2=自动 3=专家
DOUBAO_MODELS = ["普通回复", "深度思考", "自动思考", "专家模式"]
DEEPSEEK_MODELS = ["deepseek-chat", "deepseek-reasoner"]

# api（OpenAI 兼容接口）默认值：地址留空 = 用官方默认；
# 模型默认 deepseek-flash = DeepSeek 官方 V4.1 Flash（原生多模态，可接截图）
API_PROVIDER = "api"
DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_API_MODEL = "deepseek-flash"

# dsh（DeepSeek Harness 本体 headless 模式）提供商 id
DSH_PROVIDER = "dsh"

# 默认第一段提示词：截图后发给 AI 的初始提问（设置里可改，可留空=直接问）
DEFAULT_PROMPT = "根据图片，分析图片中的问题，然后详细解析推理给出答案。"

# 二次提取答案的默认提示词模板：{answer} 会被替换为第一次的完整回答。
# 代码题输出简洁无注释的完整 Python 代码；普通题只输出最终答案。
DEFAULT_EXTRACT_PROMPT = (
    "【任务】下面是一段 AI 回复的完整内容。请把其中的“最终答案”提取出来。\n"
    "如果答案是代码：请提取为简洁、可直接运行的完整 Python 代码，"
    "去掉所有注释和多余空行。\n"
    "如果是普通问题：只输出最终答案本身（可以是数字、选项字母、关键词或一句结论）。\n"
    "不要输出思考过程、不要解释、不要加任何前缀。\n"
    "如果找不到明确答案，请回复“未找到明确答案”。\n\n"
    "【完整回答】\n{answer}"
)

# 旧版提取提示词（仅用于识别旧配置并自动升级，不要修改）
_LEGACY_EXTRACT_PROMPT = (
    "【任务】下面是一段 AI 回复的完整内容。请把其中的“最终答案”单独提取出来。\n"
    "要求：只输出最终答案本身（可以是数字、选项字母、关键词或一句结论），\n"
    "不要输出思考过程、不要解释、不要加任何前缀。\n"
    "如果找不到明确答案，请回复“未找到明确答案”。\n\n"
    "【完整回答】\n{answer}"
)


def _think_of(model: str) -> int:
    return DOUBAO_MODELS.index(model) if model in DOUBAO_MODELS else 0


# 控制热键默认布局（动作名 -> 快捷键）。用普通工厂函数而不是 lambda，
# 便于 load() 时用 __dataclass_fields__ 的默认值做合并补齐。
def _default_control_hotkeys() -> dict:
    return {
        "scroll_up": "NumPad8",
        "scroll_down": "NumPad2",
        "move_left": "NumPad4",
        "move_right": "NumPad6",
        "move_down": "NumPad1",
        "move_up": "NumPad3",
        "opacity_down": "NumPad7",
        "opacity_up": "NumPad9",
        "font_size_down": "Minus",   # 小键盘 -：字号 -1pt
        "font_size_up": "Plus",      # 小键盘 +：字号 +1pt
        "shot": "NumPad5",   # 截图提问（替代原"居中"）
    }


@dataclass
class AppConfig:
    hotkey: str = "F8"
    answer_opacity: int = 100            # 文本透明度 0-100（0=不可见 100=全显灰字）
    prompt: str = DEFAULT_PROMPT         # 第一段提示词（截图提问，设置里可改/可留空）
    placeholder_text: str = "A"          # 悬浮窗初始显示文本（尚无回答时）
    provider: str = "doubao"             # doubao / deepseek / api / dsh
    model: str = "普通回复"               # doubao: 普通/深度/自动/专家；deepseek/api: 模型名
    api_base: str = ""                    # api: 接口地址（留空 = https://api.deepseek.com）
    api_key: str = ""                     # api: API Key（仅存本地 nvldla_config.json）
    extract_answer: bool = True          # 二次提取答案：AI 回复后再次提炼最终答案
    extract_prompt: str = DEFAULT_EXTRACT_PROMPT   # 提取提示词模板（含 {answer}）
    window_x: int = 60
    window_y: int = 60
    window_w: int = 480
    window_h: int = 320
    font_size: int = 11                # 回答/占位文本字号（像素点大小）
    control_hotkeys: dict = field(default_factory=_default_control_hotkeys)

    @property
    def model_kwargs(self) -> dict:
        """转换为 webchat.chat 需要的模型相关参数。"""
        if self.provider == API_PROVIDER:
            # 旧配置里 model 可能还停在豆包的档位名，切到 api 时兜底成默认模型
            model = self.model if self.model not in DOUBAO_MODELS else DEFAULT_API_MODEL
            return {
                "model": model or DEFAULT_API_MODEL,
                "api_key": self.api_key,
                "api_base": self.api_base,
            }
        if self.provider == DSH_PROVIDER:
            # dsh（Harness 本体 headless）模型跟随 Harness 自身配置，不传模型参数
            return {}
        if self.provider == "doubao":
            return {"think": _think_of(self.model)}
        return {"model": self.model}

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}
        merged = {k: v for k, v in data.items() if k in known}
        # 旧配置第一段提示词为空 → 升级为默认（分析图片问题）
        if not merged.get("prompt"):
            merged["prompt"] = DEFAULT_PROMPT
        # 旧版提取提示词自动升级为新模板（用户自定义的模板不受影响）
        if merged.get("extract_prompt") == _LEGACY_EXTRACT_PROMPT:
            merged["extract_prompt"] = DEFAULT_EXTRACT_PROMPT
        # control_hotkeys 可能是部分 dict（或旧配置缺失），用默认布局补齐缺失键，
        # 保证 9 个动作始终齐全（如去掉某个键/新增动作时旧配置也有兜底）。
        if "control_hotkeys" in merged and isinstance(merged["control_hotkeys"], dict):
            merged["control_hotkeys"] = {
                **_default_control_hotkeys(),
                **merged["control_hotkeys"],
            }
        return cls(**merged)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
