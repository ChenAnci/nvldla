"""NVlDlA —— 设置窗口。

托盘菜单点「设置」打开：可改快捷键、全局透明度、回答透明度、
提示词、提供商（豆包/DeepSeek 网页版，或 api + API Key 直连）与模型。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QKeyEvent
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from login_worker import DoubaoLoginWorker, DeepSeekLoginWorker, get_login_status
from dsh_import import import_dsh_channel

from config import (
    AppConfig, DEFAULT_EXTRACT_PROMPT, DEEPSEEK_MODELS, DOUBAO_MODELS,
    API_PROVIDER, DEFAULT_API_BASE, DEFAULT_API_MODEL, DSH_PROVIDER,
    _default_control_hotkeys,
)

_MODIFIER_KEYS = {
    Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
    Qt.Key.Key_Meta, Qt.Key.Key_AltGr, Qt.Key.Key_CapsLock,
}

# 提供商下拉：显示名 -> 存进配置的 id（用 itemData，存取都走 currentData）
PROVIDER_CHOICES = [
    ("doubao", "doubao（网页版·免费）"),
    ("deepseek", "deepseek（网页版·免费）"),
    ("api", "api（API Key 官方/中转接口）"),
    ("dsh", "dsh（DeepSeek Harness 本体·headless）"),
]

# api 模型候选：可编辑下拉，列表外的模型名可直接手输。
# deepseek-flash = DeepSeek 官方 V4.1 Flash（1017 快照也走这个名字，原生多模态）
API_MODEL_SUGGESTIONS = [
    "deepseek-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "doubao-1-5-vision-pro-32k-250115",
    "qwen-vl-max",
    "gpt-4o",
]

# api 接口区域的说明（地址示例 + 模型名口径 + 截图必须用视觉模型）
API_HINT = (
    "常用地址：DeepSeek https://api.deepseek.com ｜ 火山方舟 https://ark.cn-beijing.volces.com/api/v3 "
    "｜ OpenAI https://api.openai.com/v1 ｜ 中转站填其 …/v1 地址（Key 仅保存在本地）。\n"
    "模型名以接口方文档为准：DeepSeek 官方 V4.1 Flash（含 1017 快照）的模型名是 deepseek-flash，"
    "不要填开源权重名（如 deepseek-v4.1-flash1017）。"
    "截图问答需支持图片输入的模型，纯文本模型会提示「不支持图片输入」；"
    "已装 DeepSeek Harness 可点「从 Harness 导入」一键填好，填完点「测试连接」验证。"
)

# dsh（Harness 本体 headless）的说明：答题方式 / 延迟 / 图片 / Key 来源
DSH_HINT = (
    "dsh = 调本机 DeepSeek Harness 的 headless 模式答题：无需登录，"
    "模型跟随 Harness 自身配置；每轮冷启动一个进程（约 10~30 秒起步）。"
    "截图按文件路径交给它自己读图。Key 用本机 DeepSeek 官方 Key"
    "（环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY 或 Harness 里保存的）。"
)


class HotkeyEdit(QLineEdit):
    """点击后按下组合键即记录（如 Ctrl+F8）。只读、捕获按键。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("点击后按下组合键…")
        self.setMinimumWidth(160)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in _MODIFIER_KEYS or event.key() == Qt.Key.Key_unknown:
            event.accept()
            return
        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")
        name = QKeySequence(event.key()).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        if not name:
            event.accept()
            return
        # 小键盘键：Qt 的 key 与主键盘相同（Key_0..9 / Key_Plus / Key_Minus），
        # 靠 KeypadModifier 区分；不处理会被记成主键盘键（如 "5"），
        # 注册后反而拦截正常数字输入。这里映射回 hotkey.py 认识的小键盘键名。
        if mods & Qt.KeyboardModifier.KeypadModifier:
            name = {
                "0": "NumPad0", "1": "NumPad1", "2": "NumPad2", "3": "NumPad3",
                "4": "NumPad4", "5": "NumPad5", "6": "NumPad6", "7": "NumPad7",
                "8": "NumPad8", "9": "NumPad9",
                "+": "Plus", "-": "Minus", "*": "Multiply", "/": "Divide",
            }.get(name, name)
        parts.append(name)
        self.setText("+".join(parts))
        event.accept()


def collect_hotkey_text(text: str) -> str:
    """把 HotkeyEdit 记录的文本规范化成 parse_hotkey 可接受的拼写。

    关键：Qt 对小键盘数字键的 PortableText 常写作 "Numpad5"（P 小写），
    而 hotkey.py 的解析键名是 "NumPad5"（P 大写）；漏改会导致保存后
    重注册时解析失败或误判为普通数字键。这里统一成 "NumPad"。
    """
    if not text:
        return text
    tokens = [t.strip() for t in text.split("+") if t.strip()]
    out = []
    for t in tokens:
        # 统一的修饰键名原样保留；按键名统一 P 大写
        t = t.replace("Numpad", "NumPad").replace("numpad", "NumPad")
        out.append(t)
    return "+".join(out)


# 控制动作名 -> 设置里显示的中文标签
CONTROL_LABELS = [
    ("scroll_up", "内容上滚"),
    ("scroll_down", "内容下滚"),
    ("move_left", "窗口左移"),
    ("move_right", "窗口右移"),
    ("move_down", "窗口下移"),
    ("move_up", "窗口上移"),
    ("opacity_down", "透明度 -1%"),
    ("opacity_up", "透明度 +1%"),
    ("font_size_down", "字号 -1pt"),
    ("font_size_up", "字号 +1pt"),
    ("shot", "截图提问"),
]


class SettingsDialog(QDialog):
    def __init__(self, cfg: AppConfig, preview=None, notify=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._preview = preview          # 可选回调：滑块拖动时实时预览（不保存）
        self._notify = notify            # 可选回调(str)：登录完成时托盘提示
        self._login_worker = None        # 正在进行的登录后台线程
        self._api_test_worker = None     # 正在进行的「测试连接」后台线程
        self.setWindowTitle("NVlDlA 设置")
        self.setMinimumSize(420, 320)
        self.setSizeGripEnabled(True)   # 右下角拖拽手柄，可自由调整窗口大小
        scr = self.screen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(620, g.width() - 80), min(720, g.height() - 80))
        else:
            self.resize(620, 720)

        root = QVBoxLayout(self)

        # 内容区包进滚动区域：设置项多时右侧可下拉，窗口也可缩放
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        cbox = QVBoxLayout(container)
        cbox.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(container)

        form = QFormLayout()
        form.setSpacing(10)

        # 快捷键
        self.hotkey_edit = HotkeyEdit(self)
        self.hotkey_edit.setText(cfg.hotkey)
        form.addRow("快捷键：", self.hotkey_edit)

        # 文本透明度（唯一透明度滑块：除文本外窗口全部透明）
        self.answer_opacity_slider, al = self._make_slider(cfg.answer_opacity)
        form.addRow("文本透明度：", al)

        # 回答字号（像素点大小），无快捷键，仅设置里调整
        self.font_size_spin = QSpinBox(self)
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(cfg.font_size)
        self.font_size_spin.valueChanged.connect(self._on_any_change)
        fbox = QHBoxLayout()
        fbox.addWidget(self.font_size_spin)
        fbox.addWidget(QLabel("pt", self))
        form.addRow("回答字号：", fbox)

        # 提供商 + 模型（api = OpenAI 兼容接口 + API Key 直连）
        self.provider_combo = QComboBox(self)
        for pid, label in PROVIDER_CHOICES:
            self.provider_combo.addItem(label, pid)
        idx = self.provider_combo.findData(cfg.provider)
        self.provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.model_combo = QComboBox(self)
        self._refill_models(self.provider_combo.currentData() or cfg.provider, cfg.model)
        self.provider_combo.currentIndexChanged.connect(
            lambda _i: self._on_provider_changed(self.provider_combo.currentData())
        )
        pbox = QHBoxLayout()
        pbox.addWidget(self.provider_combo)
        pbox.addWidget(QLabel("模型：", self))
        pbox.addWidget(self.model_combo)
        form.addRow("提供商：", pbox)

        # dsh（Harness 本体）说明：仅 provider == dsh 时显示
        self.dsh_hint = QLabel(DSH_HINT, self)
        self.dsh_hint.setStyleSheet("color:#80868b; font-size:11px;")
        self.dsh_hint.setWordWrap(True)
        self.dsh_hint.setVisible(
            (self.provider_combo.currentData() or cfg.provider) == DSH_PROVIDER
        )
        form.addRow("", self.dsh_hint)

        # API 接口设置：仅 provider == api 时显示
        self.api_box = QWidget(self)
        abox = QFormLayout(self.api_box)
        abox.setContentsMargins(0, 0, 0, 0)
        self.api_base_edit = QLineEdit(self)
        self.api_base_edit.setPlaceholderText(f"留空 = {DEFAULT_API_BASE}")
        self.api_base_edit.setText(cfg.api_base)
        self.api_key_edit = QLineEdit(self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("sk-…（仅保存在本地 nvldla_config.json）")
        self.api_key_edit.setText(cfg.api_key)
        abox.addRow("接口地址：", self.api_base_edit)
        abox.addRow("API Key：", self.api_key_edit)

        # 从本机 DeepSeek Harness 导入它已配置的通道（只读 DSH 两个配置文件）
        self.dsh_import_btn = QPushButton("从 DeepSeek Harness 导入", self)
        self.dsh_import_btn.setToolTip(
            "读取本机 DSH 已配置的供应商 / 密钥 / 模型（只读，不改动 Harness），一键填好下方三项"
        )
        self.dsh_import_btn.clicked.connect(self._import_dsh_channel)
        self.dsh_import_result = QLabel("", self)
        self.dsh_import_result.setWordWrap(True)
        self.dsh_import_result.setStyleSheet("color:#5a6472; font-size:11px;")
        hrow = QHBoxLayout()
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.addWidget(self.dsh_import_btn)
        hrow.addWidget(self.dsh_import_result, 1)
        abox.addRow("Harness：", hrow)

        # 连通测试：不截图，直接用当前地址/Key/模型发一条最短请求
        self.api_test_btn = QPushButton("测试连接", self)
        self.api_test_btn.setToolTip(
            "用当前的地址 / Key / 模型发一条最短请求（不发截图），立刻验证能否接通"
        )
        self.api_test_btn.clicked.connect(self._start_api_test)
        self.api_test_result = QLabel("", self)
        self.api_test_result.setWordWrap(True)
        self.api_test_result.setStyleSheet("color:#5a6472; font-size:11px;")
        trow = QHBoxLayout()
        trow.setContentsMargins(0, 0, 0, 0)
        trow.addWidget(self.api_test_btn)
        trow.addWidget(self.api_test_result, 1)
        abox.addRow("连通测试：", trow)

        api_hint = QLabel(API_HINT, self)
        api_hint.setStyleSheet("color:#80868b; font-size:11px;")
        api_hint.setWordWrap(True)
        abox.addRow("", api_hint)
        form.addRow("API 接口：", self.api_box)
        self.api_box.setVisible(cfg.provider == API_PROVIDER)

        # 第一段提示词：截图后发给 AI 的提问（可留空=直接问）
        self.prompt_edit = QPlainTextEdit(self)
        self.prompt_edit.setPlaceholderText("默认：根据图片，分析图片中的问题，然后详细解析推理给出答案。可留空=直接问")
        self.prompt_edit.setPlainText(cfg.prompt)
        self.prompt_edit.setFixedHeight(80)
        form.addRow("第一段提示词：", self.prompt_edit)

        # 二次提取答案：AI 回复完整内容后，再让它提炼出最终答案
        self.extract_check = QCheckBox(
            "二次提取答案（AI 回复完整内容后，再让 AI 提炼出最终答案）", self
        )
        self.extract_check.setChecked(cfg.extract_answer)
        form.addRow("", self.extract_check)

        # 第二段提示词：把第一段完整回答再发给 AI，提炼最终答案
        self.extract_prompt_edit = QPlainTextEdit(self)
        self.extract_prompt_edit.setPlainText(cfg.extract_prompt)
        self.extract_prompt_edit.setFixedHeight(80)
        etip = QLabel("{answer} 占位符会被替换为第一段的完整回答，请保留", self)
        etip.setStyleSheet("color:#80868b; font-size:11px;")
        ebox = QVBoxLayout()
        ebox.setContentsMargins(0, 0, 0, 0)
        ebox.addWidget(self.extract_prompt_edit)
        ebox.addWidget(etip)
        form.addRow("第二段提取提示词：", ebox)

        # 初始显示文本（尚无回答时悬浮窗显示的占位文字）
        self.placeholder_edit = QLineEdit(self)
        self.placeholder_edit.setText(cfg.placeholder_text)
        self.placeholder_edit.setPlaceholderText("悬浮窗初始显示的文本（可留空）")
        form.addRow("初始文本：", self.placeholder_edit)

        # 控制快捷键（可改键）。默认小键盘数字键，无需修饰键。
        self.control_edits: dict[str, HotkeyEdit] = {}
        head = QLabel("控制快捷键（点击下方框后按新键即可改）", self)
        head.setStyleSheet("font-weight:600;")
        form.addRow("", head)
        for act, label in CONTROL_LABELS:
            edit = HotkeyEdit(self)
            edit.setText(collect_hotkey_text(cfg.control_hotkeys.get(act, "")))
            edit.setPlaceholderText(self._default_for(act))
            self.control_edits[act] = edit
            form.addRow(label + "：", edit)

        cbox.addLayout(form)

        # 账号登录（豆包 / DeepSeek 网页版免费通道）：扫码或浏览器登录，凭据仅存本地
        # provider == api（API Key 直连）或 dsh（Harness 本体）时整块隐藏——都不需要网页登录
        self.login_box = QGroupBox("账号登录（豆包 / DeepSeek 网页版）", self)
        login_box = self.login_box
        lg = QVBoxLayout(login_box)
        self.login_state_label = QLabel(self._login_state_text(), self)
        self.login_state_label.setWordWrap(True)
        lg.addWidget(self.login_state_label)
        login_btns = QHBoxLayout()
        self.btn_login_doubao = QPushButton("登录豆包（扫码）", self)
        self.btn_login_deepseek = QPushButton("登录 DeepSeek（浏览器）", self)
        self.btn_login_doubao.clicked.connect(self._login_doubao)
        self.btn_login_deepseek.clicked.connect(self._login_deepseek)
        login_btns.addWidget(self.btn_login_doubao)
        login_btns.addWidget(self.btn_login_deepseek)
        lg.addLayout(login_btns)
        cbox.addWidget(login_box)
        login_box.setVisible(cfg.provider not in (API_PROVIDER, DSH_PROVIDER))

        ch = QLabel("控制键按小键盘数字（无需 Ctrl/Alt，需 NumLock 亮起）；"
                    "小键盘 +/- 调字号；点上方框再按键即可改成其它键。", self)
        ch.setStyleSheet("color:#80868b; font-size:11px;")
        ch.setWordWrap(True)
        cbox.addWidget(ch)

        hint = QLabel("提示：除文本外窗口全部透明。\n"
                      "「文本透明度」为真实半透明：0% 完全隐形，100% 全显灰色，中间值可透出桌面。", self)
        hint.setStyleSheet("color:#80868b; font-size:11px;")
        hint.setWordWrap(True)
        cbox.addWidget(hint)

        # 快捷键说明（只读标注，帮助用户了解控制方式）
        # 控制全部用「小键盘数字键」（不加 Ctrl/Alt），任意程序焦点下都可用；
        # 不需要修饰键，直接按小键盘数字即可触发。上面那排数字键不受影响。
        sc = QLabel(
            "默认快捷键（全局、不需按 Ctrl/Alt；可在上方框中改键）：\n"
            "· Ctrl+F8  截图并提问\n"
            "· 5  截图提问\n"
            "· 8 / 2  内容上 / 下滚动（按住连续）\n"
            "· 4 / 6  窗口左 / 右移动\n"
            "· 1 / 3  窗口下 / 上移动\n"
            "· 7 / 9  透明度 -1% / +1%\n"
            "· 小键盘 - / +  字号 -1pt / +1pt（上面那排 +/- 照常输入）\n"
            "（默认用小键盘数字，上面那排数字键照常输入；需 NumLock 亮起）\n"
            "· ↑ / ↓ 方向键滚动（窗口聚焦时）\n"
            "· 双击  隐藏窗口", self)
        sc.setStyleSheet("color:#5a6472; font-size:11px;")
        sc.setWordWrap(True)
        cbox.addWidget(sc)

        root.addWidget(scroll, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ---------------------------------------------------------- 辅助
    def _default_for(self, act: str) -> str:
        return _default_control_hotkeys().get(act, "")

    def _make_slider(self, value: int):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(value)
        label = QLabel(f"{value}%")
        label.setFixedWidth(48)
        slider.valueChanged.connect(lambda v: label.setText(f"{v}%"))
        slider.valueChanged.connect(self._on_any_change)
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(slider, 1)
        box.addWidget(label)
        return slider, box

    def _on_any_change(self, _value=None) -> None:
        """滑块/下拉变化时实时预览到悬浮窗（不保存配置）。"""
        if self._preview is not None:
            try:
                self._preview(self._peek())
            except Exception:
                pass

    def _peek(self) -> AppConfig:
        """从当前控件状态构造 AppConfig（用于实时预览）。"""
        cfg = self._cfg
        out = AppConfig(
            hotkey=cfg.hotkey,
            answer_opacity=self.answer_opacity_slider.value(),
            font_size=self.font_size_spin.value(),
            prompt=cfg.prompt,
            placeholder_text=self.placeholder_edit.text(),
            provider=self.provider_combo.currentData() or cfg.provider,
            model=self.model_combo.currentText(),
            api_base=self.api_base_edit.text().strip(),
            api_key=self.api_key_edit.text().strip(),
            extract_answer=self.extract_check.isChecked(),
            extract_prompt=self.extract_prompt_edit.toPlainText(),
            window_x=cfg.window_x, window_y=cfg.window_y,
            window_w=cfg.window_w, window_h=cfg.window_h,
            control_hotkeys=cfg.control_hotkeys,
        )
        return out

    def _on_provider_changed(self, provider: str) -> None:
        self._refill_models(provider)
        is_api = provider == API_PROVIDER
        is_dsh = provider == DSH_PROVIDER
        self.api_box.setVisible(is_api)
        self.login_box.setVisible(not is_api and not is_dsh)
        self.dsh_hint.setVisible(is_dsh)
        self._on_any_change()

    def _refill_models(self, provider: str, keep: str = "") -> None:
        if provider == DSH_PROVIDER:
            # 模型由 Harness 自身配置决定，这里只读展示，不保存业务含义
            self.model_combo.setEditable(False)
            self.model_combo.clear()
            self.model_combo.addItems(["跟随 Harness 默认"])
            return
        if provider == API_PROVIDER:
            # api 模型名不固定：下拉给常用候选，同时允许手输任意模型名
            self.model_combo.setEditable(True)
            self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.model_combo.clear()
            self.model_combo.addItems(API_MODEL_SUGGESTIONS)
            # 保留用户保存过的模型名；只有豆包档位名（普通回复/深度思考…）
            # 才不属于 api 通道，回落到默认。deepseek-chat 等网页版同名模型
            # 也是合法 API 模型名，应原样保留。
            if keep and keep not in DOUBAO_MODELS:
                self.model_combo.setEditText(keep)
            else:
                self.model_combo.setEditText(DEFAULT_API_MODEL)
            return
        self.model_combo.setEditable(False)
        models = DEEPSEEK_MODELS if provider == "deepseek" else DOUBAO_MODELS
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if keep in models:
            self.model_combo.setCurrentText(keep)

    # -------------------------------------------------- API 连通测试
    def _import_dsh_channel(self) -> None:
        """读本机 DSH 的通道配置（供应商/Key/模型）填入当前表单。"""
        try:
            channel, msg = import_dsh_channel()
        except Exception as exc:  # 任何读盘/解析失败都要给出可读提示
            channel, msg = None, f"读取 DeepSeek Harness 配置失败：{exc}"
        if channel is None:
            self.dsh_import_result.setStyleSheet("color:#c4320a; font-size:11px;")
        else:
            self.api_base_edit.setText(channel.base_url)
            self.api_key_edit.setText(channel.api_key)
            if channel.model:
                self.model_combo.setEditText(channel.model)
            self.dsh_import_result.setStyleSheet("color:#1a7f37; font-size:11px;")
        self.dsh_import_result.setText(msg)

    def _start_api_test(self) -> None:
        if self._api_test_worker is not None and self._api_test_worker.isRunning():
            return
        model = self.model_combo.currentText().strip()
        self.api_test_btn.setEnabled(False)
        self.api_test_btn.setText("测试中…")
        self.api_test_result.setStyleSheet("color:#5a6472; font-size:11px;")
        self.api_test_result.setText(f"正在用「{model or DEFAULT_API_MODEL}」发最短请求…")
        self._api_test_worker = ApiTestWorker(
            self.api_base_edit.text().strip(),
            self.api_key_edit.text().strip(),
            model,
        )
        self._api_test_worker.done.connect(self._on_api_test_done)
        self._api_test_worker.start()

    def _on_api_test_done(self, ok: bool, msg: str) -> None:
        self.api_test_btn.setEnabled(True)
        self.api_test_btn.setText("测试连接")
        self.api_test_result.setText(msg)
        self.api_test_result.setStyleSheet(
            "color:#1a7f37; font-size:11px;" if ok else "color:#c4320a; font-size:11px;"
        )

    def _detach_api_test_worker(self) -> None:
        """窗口关闭时若测试仍在跑，把线程转入模块列表继续跑完，避免随窗口销毁。"""
        w = self._api_test_worker
        if w is None or not w.isRunning():
            return
        try:
            w.done.disconnect(self._on_api_test_done)
        except TypeError:
            pass
        _RUNNING_API_TEST_WORKERS.append(w)
        w.finished.connect(
            lambda w=w: _RUNNING_API_TEST_WORKERS.remove(w)
            if w in _RUNNING_API_TEST_WORKERS else None
        )

    def done(self, result: int) -> None:
        """无论保存/取消/X 关闭，都保证测试线程不随窗口销毁。"""
        self._detach_api_test_worker()
        super().done(result)

    # ---------------------------------------------------------- 登录
    def _login_state_text(self) -> str:
        st = get_login_status()
        doubao = "已登录" if st["doubao"] else "未登录"
        deepseek = "已登录" if st["deepseek"] else "未登录"
        return f"豆包：{doubao}　·　DeepSeek：{deepseek}（凭据仅保存在本地）"

    def _login_doubao(self) -> None:
        self._start_login(DoubaoLoginWorker())

    def _login_deepseek(self) -> None:
        self._start_login(DeepSeekLoginWorker())

    def _start_login(self, worker) -> None:
        """打开登录对话框并持有 worker；关闭对话框后登录在后台继续。"""
        self._login_worker = worker
        worker.done.connect(self._on_login_done)
        dlg = LoginDialog(worker, parent=self)
        dlg.exec()

    def _on_login_done(self, ok: bool, msg: str) -> None:
        self.login_state_label.setText(self._login_state_text())
        if self._notify is not None:
            self._notify(msg if ok else f"登录失败：{msg}")

    def closeEvent(self, e) -> None:
        """设置窗口关闭时，若登录仍在进行，转入后台继续跑。"""
        self._detach_login_worker()
        super().closeEvent(e)

    def _detach_login_worker(self) -> None:
        """登录未结束时把 worker 从本窗口解绑，后台跑完自动清理。"""
        w = self._login_worker
        if w is None or not w.isRunning():
            return
        w.setParent(None)
        _RUNNING_LOGIN_WORKERS.append(w)
        try:
            w.status.disconnect()
            w.done.disconnect()
        except TypeError:
            pass
        if self._notify is not None:
            w.done.connect(
                lambda ok, msg: self._notify(msg if ok else f"登录失败：{msg}")
            )
        w.finished.connect(
            lambda w=w: _RUNNING_LOGIN_WORKERS.remove(w)
            if w in _RUNNING_LOGIN_WORKERS else None
        )

    # ---------------------------------------------------------- 读取
    def collect(self) -> AppConfig:
        cfg = AppConfig()
        cfg.hotkey = collect_hotkey_text(self.hotkey_edit.text()).strip() or "F8"
        cfg.answer_opacity = self.answer_opacity_slider.value()
        cfg.font_size = self.font_size_spin.value()
        cfg.provider = self.provider_combo.currentData() or "doubao"
        cfg.model = self.model_combo.currentText().strip()
        cfg.api_base = self.api_base_edit.text().strip()
        cfg.api_key = self.api_key_edit.text().strip()
        cfg.prompt = self.prompt_edit.toPlainText()
        cfg.placeholder_text = self.placeholder_edit.text()
        cfg.extract_answer = self.extract_check.isChecked()
        cfg.extract_prompt = self.extract_prompt_edit.toPlainText() or DEFAULT_EXTRACT_PROMPT
        # 控制快捷键：规范化后取值；空/非法则回退到该动作的默认键。
        defaults = _default_control_hotkeys()
        ch: dict[str, str] = {}
        for act, edit in self.control_edits.items():
            text = collect_hotkey_text(edit.text()).strip() or defaults[act]
            ch[act] = text
        cfg.control_hotkeys = ch
        return cfg


# ---------------------------------------------------------- 登录对话框
# 设置窗口关闭时若登录仍在进行，worker 转入此列表继续后台运行，
# 避免 QThread 随窗口销毁；完成后自动移出。
_RUNNING_LOGIN_WORKERS: list = []

# 「测试连接」同理：窗口关闭但请求未返回时，线程转入此列表跑完自动移出。
_RUNNING_API_TEST_WORKERS: list = []


class ApiTestWorker(QThread):
    """设置窗口「测试连接」：后台发一条最短非流式请求，不阻塞界面。"""

    done = pyqtSignal(bool, str)

    def __init__(self, api_base: str, api_key: str, model: str):
        super().__init__()
        self._api_base = api_base
        self._api_key = api_key
        self._model = model

    def run(self) -> None:
        try:
            from webchat.openai_api import OpenAICompatClient
            client = OpenAICompatClient(
                api_key=self._api_key,
                api_base=self._api_base,
                model=self._model,
                timeout=(10, 60),
            )
            reply = client.chat("连通性测试，请只回复：ok", stream=False)
            text = (reply.text or "").strip().replace("\n", " ")[:40]
            self.done.emit(
                True, f"连接成功（{self._model or '默认模型'}）：" + (text or "（空回复）")
            )
        except Exception as exc:  # 测试按钮：任何失败都原样显示给用户排查
            self.done.emit(False, str(exc) or exc.__class__.__name__)


def shutdown_login_workers(timeout_ms: int = 3000) -> None:
    """程序退出前等待后台登录线程结束（防止 QThread 销毁警告）。"""
    for w in list(_RUNNING_LOGIN_WORKERS):
        if w.isRunning():
            w.wait(timeout_ms)


class LoginDialog(QDialog):
    """登录进度对话框：豆包显示二维码，DeepSeek 提示浏览器操作。

    关闭对话框不中断登录（worker 由设置窗口持有并转入后台），
    完成/失败后仍会通过托盘气泡提示。
    """

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self._worker = worker
        is_doubao = isinstance(worker, DoubaoLoginWorker)
        self.setWindowTitle("登录豆包" if is_doubao else "登录 DeepSeek")
        self.setModal(True)
        self.setMinimumWidth(340)

        root = QVBoxLayout(self)

        self.image_label = QLabel(self)
        self.image_label.setFixedSize(280, 280)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.image_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.status_label = QLabel("准备中…", self)
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status_label)

        close_btn = QPushButton("后台继续（关闭后登录不中断）", self)
        close_btn.clicked.connect(self.reject)
        root.addWidget(close_btn)

        self._worker.status.connect(self.status_label.setText)
        if is_doubao:
            self._worker.qr_ready.connect(self._show_qr)
        self._worker.done.connect(self._on_done)
        if not self._worker.isRunning():
            self._worker.start()

    def _show_qr(self, path: str) -> None:
        pix = QPixmap(path)
        if not pix.isNull():
            self.image_label.setPixmap(pix.scaled(
                self.image_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _on_done(self, ok: bool, msg: str) -> None:
        self.image_label.clear()
        self.status_label.setText((msg if ok else f"登录失败：{msg}") + "\n可关闭此窗口")
