"""NVlDlA —— 最前端悬浮窗。

- 无边框、置顶、不在任务栏显示（Qt.Tool）
- 背景透明：**LWA_COLORKEY 色键 + LWA_ALPHA 整窗 alpha 双管齐下**
  （WS_EX_LAYERED + SetLayeredWindowAttributes(crKey, alpha, 两标志)）：
  - 背景填充色键黑 #000000 → 全透明，只有文字有像素
  - 文字像素带**真实整窗 alpha**（LWA_ALPHA）→ 文本透明度是真半透明，
    中间值能透出桌面，不是色键的"要么全透要么不透明"
- 色键选黑色的原因：灰色文字抗锯齿边缘与黑底混合 = 中性深灰，
  不会像品红底那样产生粉色边缘（品红底 = 灰×品红 = 粉色描边）。
- 透明方式选择：Qt 的 WA_TranslucentBackground（UpdateLayeredWindow per-pixel）
  与 SetWindowDisplayAffinity 互斥（错误码 8）；而 SetLayeredWindowAttributes
  （色键/整窗 alpha）与防截图兼容，故采用后者。
- 文字绘制：全部由顶层窗口 paintEvent 自绘。文字先画到 Format_RGB32
  黑底 QImage（无 alpha 通道 → Qt 强制灰度 AA）再 drawImage 合成：
  直绘 ClearType 在色键透明窗上会给西文/符号加彩色子像素边
  （观感 = 加粗 + 重影），灰度 AA 无此问题且 RGB32 无 alpha 合成稀释，
  笔画粗细与正常渲染一致。不能用子控件——实测 LWA_COLORKEY 分层窗口下
  Qt 子控件的内容不合成到屏幕（PrintWindow 全黑、屏幕采样不可见），
  顶层自绘则正常。
- 背景恒为色键黑（全透明），除文本外窗口全部透明
- 文本透明度：整窗 alpha 0（隐形）→ 255（全显灰色），真实半透明
- 任意位置拖动、边缘缩放；滚轮滚动；双击隐藏
- 防截图：SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)。
  它与热键无关，是 DWM 层的显示亲和性——PrtSc / Ctrl+Win+S /
  Win+Shift+S 等系统截图对窗口像素返回全黑，因此截不到本窗口。
"""
from __future__ import annotations

import ctypes

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PyQt6.QtWidgets import QWidget

from anticapture import exclude_from_capture
from config import AppConfig

GRIP = 8
MIN_W, MIN_H = 300, 160  # 仅默认初始尺寸；缩放不再受此下限约束
PAD_X = 6
LINE_SPACING = 2

# 色键透明：背景填充该色即变为透明（同时是"无底"的基准色）。
# 必须用纯黑：灰色文字抗锯齿边缘与黑底混合为中性灰，不会出现粉色描边。
# SetLayeredWindowAttributes 的 colorkey 参数为 COLORREF(0x00BBGGRR)。
COLOR_KEY = QColor(0, 0, 0)
# 回答文字基准色：灰色（用户要求灰色字体）
TEXT_COLOR = QColor(90, 90, 90)

# 拖动/缩放模式：位标志（1=左 2=上 4=右 8=下），0=拖动
MODE_DRAG = 0
FLAG_LEFT, FLAG_TOP, FLAG_RIGHT, FLAG_BOTTOM = 1, 2, 4, 8

WS_EX_LAYERED = 0x00080000
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002
GWL_EXSTYLE = -20
HWND_NOTOPMOST = -2
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
TOPMOST_REFRESH_MS = 1000

user32 = ctypes.windll.user32
user32.SetLayeredWindowAttributes.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint32,
]
user32.SetLayeredWindowAttributes.restype = ctypes.c_int
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.SetWindowPos.restype = ctypes.c_int

# -------------------------------------------------- 命中测试（WNDPROC 子类化）
# 问题：LWA_COLORKEY 的透明(色键)区域会被 Windows 判定为不可点击
# （WM_NCHITTEST 返回 HTTRANSPARENT），鼠标事件直接穿透到桌面/下层窗口，
# 导致悬浮窗拖不动、缩不了。QT 的 nativeEvent 覆盖在 Python3.14 + PyQt6
# 下会崩溃（0xC0000005），故改用 Win32 窗口过程子类化：在本窗口的
# WM_NCHITTEST 一律返回 HTCLIENT，其余消息交回原窗口过程。完全不经过
# Qt 的 native event 桥接，不触碰事件循环。
WM_NCHITTEST = 0x0084
HTCLIENT = 1
GWLP_WNDPROC = -4

_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p
)
user32.SetWindowLongPtrW.argtypes = [
    ctypes.c_void_p, ctypes.c_int, _WNDPROC,
]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
]
user32.CallWindowProcW.restype = ctypes.c_long
# hwnd -> [原窗口过程, 回调对象]（持引用防 GC）
_subclassed: dict[int, tuple[object, object]] = {}


def _hit_test_proc(hwnd: int, msg: int, wp, lp) -> int:
    try:
        old_proc = _subclassed.get(hwnd, (None, None))[0]
        if msg == WM_NCHITTEST:
            return HTCLIENT
        if old_proc is not None:
            return user32.CallWindowProcW(old_proc, hwnd, msg, wp, lp)
    except Exception:
        pass  # 回调里绝不能抛异常（会刷屏/崩溃），兜底丢弃
    return 0


def install_hit_test(hwnd: int) -> None:
    """为 hwnd 子类化窗口过程，使整窗矩形可鼠标点击（幂等）。"""
    if hwnd in _subclassed:
        return
    callback = _WNDPROC(_hit_test_proc)
    old_proc = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, callback)
    _subclassed[hwnd] = (old_proc, callback)


class OverlayWindow(QWidget):
    def __init__(self, config: AppConfig, config_path=None):
        super().__init__()
        self._config = config
        self._config_path = config_path
        self._gesture_mode = None
        self._gesture_global = QPoint()
        self._gesture_geo = None

        # 自绘内容状态（无子控件：色键分层窗口下子控件不合成到屏幕）
        self._text = ""
        self._bg = QColor(0, 0, 0)      # 默认全透底（黑=色键）
        self._fg = TEXT_COLOR
        self._alpha = 255               # 整窗 alpha（LWA_ALPHA），文本真半透明
        self._placeholder = config.placeholder_text or "A"
        self._scroll = 0
        self._hovered = False           # 鼠标悬停时画淡边框，便于定位/缩放

        self.setWindowTitle("NVlDlA")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 周期性重新断言 topmost，防止其他窗口激活时被短暂盖住/消失
        self._topmost_timer = QTimer(self)
        self._topmost_timer.setInterval(TOPMOST_REFRESH_MS)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        # 统一用微软雅黑：中文与西文/符号共用同一字体族，渲染权重一致。
        # 曾用 Consolas（Trae 代码字体），其西文字形在透明窗上观感加粗+重影，
        # 而中文走雅黑回退正常 → 改显式指定雅黑让两者一致。
        # 字号由 config.font_size 控制（设置里可调），不设快捷键。
        self._apply_font(config.font_size)
        self.setMinimumSize(1, 1)   # 允许缩到极小（去掉 300x160 下限约束）
        self.setMouseTracking(True)     # 悬停边缘时显示缩放光标
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # 支持方向键滚动
        self._layered_ready = False

        self.apply_settings(config)
        self.setGeometry(
            config.window_x, config.window_y, config.window_w, config.window_h
        )

    # ------------------------------------------------------- 样式
    def _apply_font(self, size: int) -> None:
        """按字号重建字体并应用到窗口（微软雅黑、常规字重、非常粗体）。"""
        f = QFont("Microsoft YaHei")
        f.setWeight(QFont.Weight.Normal)
        f.setBold(False)
        f.setPointSize(max(1, int(size or 11)))
        self.setFont(f)

    def apply_settings(self, cfg: AppConfig) -> None:
        self._config = cfg
        a = max(0.0, min(1.0, cfg.answer_opacity / 100.0))
        # 背景恒为色键黑（全透明）；文本颜色恒定灰，透明度 = 整窗 alpha
        self._bg = COLOR_KEY
        self._fg = TEXT_COLOR
        self._placeholder = cfg.placeholder_text or "A"
        self._alpha = int(round(255 * a))
        self._apply_font(cfg.font_size)
        if self._layered_ready:
            self._set_layered_alpha()
        self.update()

    # ------------------------------------------------------- 文本布局
    def _line_height(self, fm: QFontMetrics) -> int:
        return fm.height() + LINE_SPACING

    def _wrap(self, fm: QFontMetrics) -> list[str]:
        """按当前宽度自动换行。"""
        if not self._text:
            return []
        max_w = max(self.width() - 2 * PAD_X, 40)
        lines: list[str] = []
        cur = ""
        for ch in self._text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if cur and fm.horizontalAdvance(cur + ch) > max_w:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur or not self._text.endswith("\n"):
            lines.append(cur)
        return lines

    def _max_scroll(self, fm: QFontMetrics) -> int:
        return max(0, len(self._wrap(fm)) * self._line_height(fm) - self.height())

    def _at_bottom(self, fm: QFontMetrics) -> bool:
        ms = self._max_scroll(fm)
        return ms == 0 or self._scroll >= ms - 4

    # ------------------------------------------------------- 内容
    def set_status(self, text: str, color: str = "transparent") -> None:
        # 极简模式：状态文字不可见，仅保留接口（main.py 调用）
        pass

    def set_answer(self, text: str) -> None:
        fm = QFontMetrics(self.font())
        at_bottom = self._at_bottom(fm)
        self._text = text
        if at_bottom:
            self._scroll = self._max_scroll(fm)
        else:
            self._scroll = min(self._scroll, self._max_scroll(fm))
        self.update()

    def current_geometry(self) -> tuple[int, int, int, int]:
        g = self.geometry()
        return g.x(), g.y(), g.width(), g.height()

    # ------------------------------------------------------- 绘制
    def _draw_text(self, painter: QPainter, lines: list[str], fm: QFontMetrics,
                   line_h: int, top: int, fg: QColor) -> None:
        """先画到 RGB32 黑底 QImage 再合成，消除西文/符号的重影加粗。

        直绘走 ClearType 亚像素渲染，在色键透明窗上西文/符号出现彩色
        子像素边（观感 = 加粗 + 重影），中文因字体回退走灰度 AA 所以正常。
        解法：中间层用 Format_RGB32（无 alpha 通道）+ fill(纯黑=色键)：
        RGB32 无 alpha → Qt 强制灰度 AA，零彩边；黑底合成后仍匹配色键
        被 LWA_COLORKEY 抠透（灰色 AA 边缘是黑灰混合，不会被吃掉）；
        无 alpha 合成稀释，笔画粗细与正常渲染一致。
        """
        img = QImage(self.size(), QImage.Format.Format_RGB32)
        img.fill(COLOR_KEY)  # 纯黑 = 色键，合成后依旧被抠透
        q = QPainter(img)
        q.setFont(self.font())
        q.setPen(fg)
        view_h = self.height()
        for line in lines:
            if top + line_h > 0 and top < view_h:
                q.drawText(PAD_X, top + fm.ascent(), line)
            top += line_h
        q.end()
        painter.drawImage(0, 0, img)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setFont(self.font())
        # 背景 = 色键黑（全透明），除文本外窗口全部透明
        p.fillRect(self.rect(), self._bg)
        fm = QFontMetrics(self.font())
        if not self._text:
            # 占位符（同样走 RGB32 预绘，随回答透明度一起调节）
            self._draw_text(p, [self._placeholder], fm,
                            self._line_height(fm), 2, self._fg)
        else:
            self._draw_text(p, self._wrap(fm), fm,
                            self._line_height(fm), 2 - self._scroll, self._fg)
        # 悬停时画 1px 淡边框：放在文本之后（文本的整幅黑底图会盖掉先画的边框）
        if self._hovered:
            p.setPen(QColor(70, 70, 70))
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.end()

    # ------------------------------------------------------- 事件
    def _scroll_by(self, dy: int) -> None:
        self._scroll = min(
            max(0, self._scroll + dy), self._max_scroll(QFontMetrics(self.font()))
        )
        self.update()

    def _adjust_opacity(self, delta_percent: int) -> None:
        """按百分比增减整窗 alpha（真实半透明），并同步到 config/磁盘。"""
        new = min(100, max(0, self._config.answer_opacity + delta_percent))
        self._config.answer_opacity = new
        if self._config_path is not None:
            self._config.save(self._config_path)
        self.apply_settings(self._config)

    def _adjust_font_size(self, delta: int) -> None:
        """按点增减字号（小键盘 +/- 热键），并同步到 config/磁盘。"""
        new = min(72, max(6, int(self._config.font_size) + delta))
        self._config.font_size = new
        if self._config_path is not None:
            self._config.save(self._config_path)
        self.apply_settings(self._config)

    def _move_by(self, dx: int, dy: int) -> None:
        g = self.geometry()
        self.setGeometry(g.x() + dx, g.y() + dy, g.width(), g.height())

    def _reset_position(self) -> None:
        """移到屏幕中心（可靠可点的复位动作）。"""
        screen = self.screen() or self.window().screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        self.setGeometry(
            geo.x() + (geo.width() - w) // 2,
            geo.y() + (geo.height() - h) // 2,
            w, h,
        )

    def keyPressEvent(self, event) -> None:
        k = event.key()
        # 方向键滚动（保留）
        if k == Qt.Key.Key_Up:
            self._scroll_by(-30)
            event.accept()
            return
        if k == Qt.Key.Key_Down:
            self._scroll_by(30)
            event.accept()
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and \
           self._hit_test(event.position().toPoint()) == MODE_DRAG:
            self.hide()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # --------------------------------------------------- 拖动/缩放
    def _hit_test(self, pos: QPoint) -> int:
        r = self.rect()
        x, y = pos.x(), pos.y()
        flags = 0
        if x <= GRIP:
            flags |= FLAG_LEFT
        elif x >= r.width() - GRIP:
            flags |= FLAG_RIGHT
        if y <= GRIP:
            flags |= FLAG_TOP
        elif y >= r.height() - GRIP:
            flags |= FLAG_BOTTOM
        return flags

    def _cursor_for_mode(self, flags: int) -> Qt.CursorShape:
        if flags == 0:
            return Qt.CursorShape.SizeAllCursor
        if flags == FLAG_LEFT or flags == FLAG_RIGHT:
            return Qt.CursorShape.SizeHorCursor
        if flags == FLAG_TOP or flags == FLAG_BOTTOM:
            return Qt.CursorShape.SizeVerCursor
        # 对角
        if (flags & FLAG_LEFT and flags & FLAG_TOP) or \
           (flags & FLAG_RIGHT and flags & FLAG_BOTTOM):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor

    def _start_gesture(self, flags: int, gpos: QPoint) -> None:
        self._gesture_mode = flags
        self._gesture_global = gpos
        self._gesture_geo = self.geometry()

    def _update_gesture(self, gpos: QPoint) -> None:
        flags = self._gesture_mode
        if flags is None:
            return
        geo = self._gesture_geo
        dx = gpos.x() - self._gesture_global.x()
        dy = gpos.y() - self._gesture_global.y()
        if flags == MODE_DRAG:
            self.move(geo.x() + dx, geo.y() + dy)
            return
        # right/bottom 用「left+宽/高」（排他端点），避免 geo.right() 的 1px 误差
        left, top = geo.left(), geo.top()
        right, bottom = left + geo.width(), top + geo.height()
        if flags & FLAG_LEFT:
            left = geo.left() + dx
        if flags & FLAG_TOP:
            top = geo.top() + dy
        if flags & FLAG_RIGHT:
            right = right + dx
        if flags & FLAG_BOTTOM:
            bottom = bottom + dy
        w = max(right - left, 1)
        h = max(bottom - top, 1)
        if flags & FLAG_LEFT:
            left = right - w
        if flags & FLAG_TOP:
            top = bottom - h
        self.setGeometry(left, top, w, h)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._start_gesture(
                self._hit_test(event.position().toPoint()),
                event.globalPosition().toPoint(),
            )
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._gesture_mode is not None:
            self._update_gesture(event.globalPosition().toPoint())
        else:
            if not self._hovered:
                self._hovered = True
                self.update()           # 进入窗口：显示淡边框便于定位
            self.setCursor(self._cursor_for_mode(self._hit_test(event.position().toPoint())))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._gesture_mode = None
            self._gesture_geo = None
            self.unsetCursor()
            self.releaseMouse()
        event.accept()

    def leaveEvent(self, event) -> None:
        self.unsetCursor()
        if self._hovered:
            self._hovered = False
            self.update()               # 移出窗口：隐藏淡边框，恢复全透明
        super().leaveEvent(event)

    # ------------------------------------------------ 透明 / 防截图
    def _set_layered_alpha(self) -> None:
        """按当前 self._alpha 重设色键 + 整窗 alpha（真半透明）。"""
        hwnd = int(self.winId())
        user32.SetLayeredWindowAttributes(
            hwnd,
            # COLORREF 格式是 0x00BBGGRR（与 RGB 相反），不能直接拼 RGB
            (COLOR_KEY.blue() << 16) | (COLOR_KEY.green() << 8) | COLOR_KEY.red(),
            self._alpha,
            LWA_COLORKEY | LWA_ALPHA,
        )

    def _enable_layered(self) -> None:
        """窗口句柄上启用 WS_EX_LAYERED + 色键(背景全透) + 整窗 alpha(文本半透明)。幂等。"""
        if self._layered_ready:
            return
        hwnd = int(self.winId())
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
        # 刷新非客户区，同时显式断言 topmost：
        # 用 HWND_TOPMOST(而非 None+SWP_NOZORDER)，否则 SWP_FRAMECHANGED
        # 重建帧时会丢失 topmost 层级，导致打开其他程序后悬浮窗被盖住。
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED,
        )
        self._set_layered_alpha()
        self._layered_ready = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._enable_layered()
        self.raise_()      # 每次显示重新置顶，避免被其他窗口盖住
        # 防截图与 SetLayeredWindowAttributes 透明兼容（与 WA_TranslucentBackground 不兼容）
        exclude_from_capture(int(self.winId()))
        # 命中测试子类化：透明(色键)区域也保持可点击，支持鼠标拖动/缩放
        install_hit_test(int(self.winId()))
        # 周期性刷新 topmost，持续保持最前
        self._topmost_timer.start()
        self._reassert_topmost()

    def hideEvent(self, event) -> None:
        self._topmost_timer.stop()
        super().hideEvent(event)

    def _reassert_topmost(self) -> None:
        """每隔 TOPMOST_REFRESH_MS 强制把窗口放回最前。

        SWP_NOACTIVATE：不抢走用户正在操作的窗口的焦点，仅调整 z-order。
        """
        try:
            user32.SetWindowPos(
                int(self.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass
