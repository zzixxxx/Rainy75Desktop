# Rainy75Desktop — WOB Rainy75 键盘电量托盘程序
# UI 风格参考 AirPodsDesktop（托盘 + 电量弹窗卡片）
import ctypes
import logging
import logging.handlers
import math
import os
import subprocess
import sys
import threading
import time
import webbrowser

from PySide6.QtCore import (Qt, QThread, Signal, QPoint, QPointF, QTimer,
                            QPropertyAnimation, QParallelAnimationGroup,
                            QEasingCurve, QAbstractAnimation)
from PySide6.QtGui import (QAction, QColor, QFont, QIcon, QPainter, QPen,
                           QPixmap)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog,
                               QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QMenu, QPushButton,
                               QSystemTrayIcon, QToolButton, QVBoxLayout,
                               QWidget)

import battery  # noqa: E402

APP_NAME = "Rainy75Desktop"
APP_VERSION = "1.4.3"
IPC_NAME = "Rainy75Desktop_ipc"
WEB_DRIVER_URL = "https://www.wobwxe.com/"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", "."), APP_NAME)
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
log = logging.getLogger("app")
STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", "."),
    r"Microsoft\Windows\Start Menu\Programs\Startup", f"{APP_NAME}.lnk")


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _reg_light(name):
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return False


def taskbar_is_light():
    return _reg_light("SystemUsesLightTheme")


def apps_are_light():
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "AppsUseLightTheme")[0])
    except OSError:
        return True


def is_autostart():
    return os.path.exists(STARTUP_LNK)


def set_autostart(enable):
    if not enable:
        try:
            os.remove(STARTUP_LNK)
        except OSError:
            pass
        return
    if getattr(sys, "frozen", False):
        target, args, workdir = sys.executable, "", os.path.dirname(sys.executable)
    else:
        target = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        script = os.path.abspath(__file__)
        args = f'"{script}"'
        workdir = os.path.dirname(script)
    ps = (f"$ws = New-Object -ComObject WScript.Shell; "
          f"$l = $ws.CreateShortcut('{STARTUP_LNK}'); "
          f"$l.TargetPath = '{target}'; "
          + (f"$l.Arguments = '{args}'; " if args else "")
          + f"$l.WorkingDirectory = '{workdir}'; "
          f"$l.Description = 'Rainy75 键盘电量'; $l.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   creationflags=subprocess.CREATE_NO_WINDOW, check=False)


def battery_color(percent):
    if percent is None:
        return QColor(128, 128, 128)
    if percent < 20:
        return QColor(214, 69, 56)
    if percent < 50:
        return QColor(232, 160, 32)
    return QColor(58, 176, 92)


class Poller(QThread):
    """按需查询：启动时查一次，之后每次 refresh_now() 触发一次（不做定时轮询）"""
    result_ready = Signal(object)   # (percent, status) 或 None

    def __init__(self):
        super().__init__()
        self._evt = threading.Event()
        self._stop = False

    def run(self):
        try:
            while not self._stop:
                result = battery.query_battery()
                if self._stop:
                    break
                self.result_ready.emit(result)
                self._evt.wait()
                self._evt.clear()
        except Exception:
            log.exception("poller thread crashed")

    def refresh_now(self):
        self._evt.set()

    def stop(self):
        self._stop = True
        self._evt.set()


class SettingsDialog(QDialog):
    """设置页，布局参考 AirPodsDesktop：头部 logo/版本 + 分组项 + 快捷入口"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setWindowIcon(QIcon(resource_path("assets/logo.ico")))
        self.setFixedWidth(400)
        light = apps_are_light()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        logo = QLabel()
        logo_file = ("assets/logo_wide.png" if light
                     else "assets/logo_wide_white.png")
        logo.setPixmap(QPixmap(resource_path(logo_file)).scaledToHeight(
            26, Qt.SmoothTransformation))
        head.addWidget(logo)
        tcol = QVBoxLayout()
        tcol.setSpacing(0)
        t1 = QLabel(APP_NAME)
        t1.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        t2 = QLabel(f"v{APP_VERSION} · Rainy75 电量助手")
        t2.setFont(QFont("Microsoft YaHei UI", 8))
        t2.setStyleSheet("color: rgba(127,127,127,200);")
        tcol.addWidget(t1)
        tcol.addWidget(t2)
        head.addLayout(tcol)
        head.addStretch()
        root.addLayout(head)

        grp_general = QGroupBox("常规")
        g1 = QVBoxLayout(grp_general)
        self.chk_auto = QCheckBox("开机自启（登录 Windows 时自动运行）")
        self.chk_auto.setChecked(is_autostart())
        g1.addWidget(self.chk_auto)
        tip = QLabel("电量按需查询：仅在打开面板且键鼠空闲时向键盘发起，"
                     "不影响按键响应。")
        tip.setWordWrap(True)
        tip.setFont(QFont("Microsoft YaHei UI", 8))
        tip.setStyleSheet("color: rgba(127,127,127,200);")
        g1.addWidget(tip)
        root.addWidget(grp_general)

        grp_tools = QGroupBox("快捷入口")
        g2 = QHBoxLayout(grp_tools)
        btn_log = QPushButton("打开日志文件夹")
        btn_log.clicked.connect(
            lambda: (os.makedirs(LOG_DIR, exist_ok=True),
                     os.startfile(LOG_DIR)))
        g2.addWidget(btn_log)
        btn_web = QPushButton("网页驱动（需有线）")
        btn_web.clicked.connect(lambda: webbrowser.open(WEB_DRIVER_URL))
        g2.addWidget(btn_web)
        root.addWidget(grp_tools)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)


class RotatingImage(QWidget):
    """产品图缓慢摇摆 + 浮动动效（仿 AirPodsDesktop），仅面板可见时运行"""

    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self._pm = pixmap
        self._scaled = None
        self._t = 0.0
        self.setFixedHeight(165)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._t += 0.033
        self.update()

    def showEvent(self, e):
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if self._scaled is None or self._scaled.width() != w - 40:
            self._scaled = self._pm.scaled(
                w - 40, h - 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        angle = 5.0 * math.sin(self._t * 1.1)
        lift = 4.0 * math.sin(self._t * 0.7)
        p.translate(w / 2, h / 2 + lift)
        p.rotate(angle)
        p.drawPixmap(QPointF(-self._scaled.width() / 2,
                             -self._scaled.height() / 2), self._scaled)


class BatteryBar(QWidget):
    """自绘圆角胶囊电量条（QProgressBar 低数值时圆角会畸变）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)
        self._value = 0
        self._color = QColor(127, 127, 127)

    def set_state(self, value, color):
        self._value = max(0, min(100, value))
        self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(127, 127, 127, 55))
        p.drawRoundedRect(self.rect(), r, r)
        if self._value > 0:
            w = max(self.height(), round(self.width() * self._value / 100))
            p.setBrush(self._color)
            p.drawRoundedRect(0, 0, w, self.height(), r, r)


class BatteryPopup(QWidget):
    refresh_clicked = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool |
                         Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._anim = None
        light = apps_are_light()
        self._bg = QColor(250, 250, 252) if light else QColor(38, 40, 46)
        self._fg = QColor(28, 30, 36) if light else QColor(238, 240, 244)
        self._sub = QColor(120, 124, 132) if light else QColor(150, 155, 165)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        logo = QLabel()
        logo_file = ("assets/logo_wide.png" if light
                     else "assets/logo_wide_white.png")
        logo.setPixmap(QPixmap(resource_path(logo_file)).scaledToHeight(
            16, Qt.SmoothTransformation))
        head.addWidget(logo)
        title = QLabel("Rainy75")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        title.setStyleSheet(f"color: {self._fg.name()};")
        head.addWidget(title)
        head.addStretch()
        # Segoe MDL2 Assets：Win10 自带 Fluent 图标字体，保证系列一致
        self.btn_refresh = self._tool_btn("", "立即刷新")   # Refresh
        self.btn_refresh.clicked.connect(self.refresh_clicked.emit)
        head.addWidget(self.btn_refresh)
        btn_close = self._tool_btn("", "关闭")              # ChromeClose
        btn_close.clicked.connect(self.close_animated)
        head.addWidget(btn_close)
        root.addLayout(head)

        self.img = RotatingImage(
            QPixmap(resource_path("assets/rainy75_card.png")))
        root.addWidget(self.img)

        bat_row = QHBoxLayout()
        self.bar = BatteryBar()
        bat_row.addWidget(self.bar, 1)
        self.lbl_pct = QLabel("--")
        self.lbl_pct.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
        self.lbl_pct.setStyleSheet(f"color: {self._fg.name()};")
        self.lbl_pct.setFixedWidth(64)
        self.lbl_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bat_row.addWidget(self.lbl_pct)
        root.addLayout(bat_row)

        foot = QHBoxLayout()
        self.lbl_info = QLabel("正在查询…")
        self.lbl_info.setFont(QFont("Microsoft YaHei UI", 8))
        self.lbl_info.setStyleSheet(f"color: {self._sub.name()};")
        foot.addWidget(self.lbl_info)
        foot.addStretch()
        link = QLabel(f'<a href="{WEB_DRIVER_URL}" style="color:#4a7dd4;'
                      f'text-decoration:none;">网页驱动</a>')
        link.setFont(QFont("Microsoft YaHei UI", 8))
        link.setToolTip("改键/灯效用网页驱动（需拔掉 2.4G 换有线连接）")
        link.setOpenExternalLinks(True)
        foot.addWidget(link)
        root.addLayout(foot)
        self.setFixedWidth(340)

    def _tool_btn(self, glyph, tip):
        b = QToolButton()
        b.setText(glyph)
        b.setFont(QFont("Segoe MDL2 Assets", 10))
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(26, 26)
        b.setStyleSheet(
            "QToolButton { border: none; border-radius: 6px; color: %s; }"
            "QToolButton:hover { background: rgba(127,127,127,40);"
            " color: #4a7dd4; }" % self._sub.name())
        return b

    def update_data(self, percent, charging, ok, last_ok_ts):
        if percent is None:
            self.lbl_pct.setText("--")
            self.bar.set_state(0, battery_color(None))
            self.lbl_info.setText("键盘未应答 — 请确认接收器已插好、键盘已开机")
            return
        self.bar.set_state(percent, battery_color(percent))
        self.lbl_pct.setText(f"{'⚡' if charging else ''}{percent}%")
        ts = time.strftime("%H:%M", time.localtime(last_ok_ts))
        state = "" if ok else " · 键盘当前无响应"
        self.lbl_info.setText(f"{ts} 更新{state}")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(127, 127, 127, 70), 1))
        p.setBrush(self._bg)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)

    def _slide_anim(self, p0, p1, o0, o1, on_done=None):
        if self._anim and self._anim.state() == QAbstractAnimation.Running:
            self._anim.stop()
        grp = QParallelAnimationGroup(self)
        for prop, v0, v1 in ((b"pos", p0, p1), (b"windowOpacity", o0, o1)):
            a = QPropertyAnimation(self, prop)
            a.setStartValue(v0)
            a.setEndValue(v1)
            a.setDuration(180)
            a.setEasingCurve(QEasingCurve.OutCubic)
            grp.addAnimation(a)
        if on_done:
            grp.finished.connect(on_done)
        self._anim = grp
        grp.start()

    def show_bottom_right(self):
        """固定显示在主屏右下角（任务栏上方），淡入 + 上滑"""
        self.adjustSize()
        screen = QApplication.primaryScreen().availableGeometry()
        end = QPoint(screen.right() - self.width() - 12,
                     screen.bottom() - self.height() - 12)
        start = end + QPoint(0, 18)
        self.move(start)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.activateWindow()
        self._slide_anim(start, end, 0.0, 1.0)

    def close_animated(self):
        """淡出 + 下滑收起（仿 AirPodsDesktop）"""
        if not self.isVisible():
            return
        start = self.pos()
        self._slide_anim(start, start + QPoint(0, 18), 1.0, 0.0,
                         on_done=self._finish_close)

    def _finish_close(self):
        # 若已被新的显示动画接管则不隐藏
        if self._anim and self._anim.state() == QAbstractAnimation.Running:
            return
        self.hide()
        self.setWindowOpacity(1.0)

    def event(self, e):
        if e.type() == e.Type.WindowDeactivate:
            self.hide()
        return super().event(e)


class TrayApp:
    def __init__(self, app):
        self.app = app
        self.percent = None
        self.status = 0
        self.last_ok = None
        self.light_taskbar = taskbar_is_light()
        logo_file = "assets/logo.png" if self.light_taskbar else "assets/logo_white.png"
        self.logo_pix = QPixmap(resource_path(logo_file))

        self.popup = BatteryPopup()
        self.popup.refresh_clicked.connect(self.refresh)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(QIcon(self.logo_pix))
        self.tray.setToolTip("Rainy75\n查询中…")
        self.tray.activated.connect(self._on_activated)

        menu = QMenu()
        act_show = QAction("显示电量面板", menu)
        act_show.triggered.connect(self.show_popup)
        menu.addAction(act_show)
        act_refresh = QAction("立即刷新", menu)
        act_refresh.triggered.connect(self.refresh)
        menu.addAction(act_refresh)
        menu.addSeparator()
        self.act_auto = QAction("开机自启", menu, checkable=True)
        self.act_auto.setChecked(is_autostart())
        self.act_auto.triggered.connect(self._toggle_autostart)
        menu.addAction(self.act_auto)
        act_web = QAction("打开网页驱动（需有线连接）", menu)
        act_web.triggered.connect(lambda: webbrowser.open(WEB_DRIVER_URL))
        menu.addAction(act_web)
        act_cfg = QAction("设置…", menu)
        act_cfg.triggered.connect(self.open_settings)
        menu.addAction(act_cfg)
        act_log = QAction("打开日志文件夹", menu)
        act_log.triggered.connect(
            lambda: (os.makedirs(LOG_DIR, exist_ok=True), os.startfile(LOG_DIR)))
        menu.addAction(act_log)
        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.show()

        # 已在运行时再次启动 exe → 通过本地 socket 通知本实例弹出面板
        QLocalServer.removeServer(IPC_NAME)
        self.ipc = QLocalServer()
        if not self.ipc.listen(IPC_NAME):
            log.error("IPC listen failed: %s", self.ipc.errorString())
        self.ipc.newConnection.connect(self._on_ipc)

        self._query_inflight = True   # 启动时 Poller 会先查一次
        self.poller = Poller()
        self.poller.result_ready.connect(self._on_result)
        self.poller.start()
        # 启动后自动弹出一次电量面板
        QTimer.singleShot(600, self.show_popup)

    def _on_ipc(self):
        conn = self.ipc.nextPendingConnection()
        if conn:
            conn.close()
        log.info("second launch detected -> showing panel")
        self.show_popup()

    def _toggle_autostart(self, checked):
        log.info("autostart -> %s", checked)
        set_autostart(checked)

    def _tooltip(self, ok):
        if self.percent is None:
            return "Rainy75\n未检测到键盘"
        ts = time.strftime("%H:%M", time.localtime(self.last_ok))
        chg = " ⚡" if self.status else ""
        state = "" if ok else " · 无响应"
        return f"Rainy75\n{self.percent}%{chg}（{ts}{state}）"

    def _on_result(self, result):
        self._query_inflight = False
        ok = result is not None
        if ok:
            self.percent, self.status = result
            self.last_ok = time.time()
        self.tray.setToolTip(self._tooltip(ok))
        self.popup.update_data(self.percent, bool(self.status), ok,
                               self.last_ok)
        # 键盘睡眠不应答 → 面板开着时自动重试；用户敲键唤醒后即可读到
        if not ok and self.popup.isVisible():
            self.popup.lbl_info.setText("键盘未应答 — 正在自动重试…")
            QTimer.singleShot(3000, self._auto_retry)

    def _auto_retry(self):
        if (self.popup.isVisible() and not self._query_inflight
                and (self.last_ok is None
                     or time.time() - self.last_ok > self.FRESH_SECS)):
            self.refresh()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.popup.isVisible():
                self.popup.close_animated()
            else:
                self.show_popup()

    FRESH_SECS = 20   # 结果新鲜期内重复打开面板不重复查询

    def show_popup(self):
        self.popup.show_bottom_right()
        # 新鲜结果直接展示，过期才触发查询（短时间多次点击只查一次）
        if self.last_ok is None or time.time() - self.last_ok > self.FRESH_SECS:
            self.refresh()

    def refresh(self):
        if self._query_inflight:
            return
        self._query_inflight = True
        self.popup.lbl_info.setText("正在查询…（最长约 15 秒）")
        self.poller.refresh_now()

    def open_settings(self):
        self.popup.hide()
        dlg = SettingsDialog()
        if dlg.exec() == QDialog.Accepted:
            set_autostart(dlg.chk_auto.isChecked())
            self.act_auto.setChecked(dlg.chk_auto.isChecked())
            log.info("settings saved: autostart=%s", dlg.chk_auto.isChecked())

    def quit(self):
        log.info("quit requested from tray menu")
        self.poller.stop()
        self.tray.hide()
        self.poller.wait(500)
        logging.shutdown()
        os._exit(0)


def setup_logging():
    """滚动运行日志 + 未捕获异常记录（含子线程），日志在 %APPDATA%\\Rainy75Desktop\\logs"""
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if os.environ.get("RAINY75_DEBUG")
                  else logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "rainy75desktop.log"),
        maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"))
    root.addHandler(handler)

    def hook(tp, val, tb):
        log.critical("uncaught exception", exc_info=(tp, val, tb))
    sys.excepthook = hook

    def thread_hook(args):
        log.critical("uncaught exception in thread %s",
                     args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = thread_hook


def main():
    setup_logging()
    log.info("=== %s v%s starting (frozen=%s, exe=%s) ===",
             APP_NAME, APP_VERSION, getattr(sys, "frozen", False), sys.executable)
    app = QApplication(sys.argv)
    # 已有实例 → 通知它弹出面板，然后退出
    sock = QLocalSocket()
    sock.connectToServer(IPC_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        log.info("instance already running -> notified it to show panel, exiting")
        sys.exit(0)
    ctypes.windll.kernel32.CreateMutexW(None, False, "Rainy75DesktopMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:
        log.warning("another instance holds the mutex but IPC connect "
                    "failed; exiting")
        sys.exit(0)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(resource_path("assets/logo.ico")))
    tray_app = TrayApp(app)   # 必须持有引用，否则被 GC 连带销毁托盘/IPC
    log.info("tray ready, entering event loop")
    rc = app.exec()
    tray_app.poller.stop()
    sys.exit(rc)


if __name__ == "__main__":
    main()
