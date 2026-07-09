# Rainy75 (WOB / Evision 320F) 电量查询协议
# 逆向自 wobwxe.com 网页驱动：vendor collection usagePage 0xFF1C，reportId 4，
# 命令帧 [chkLo, chkHi, cmd=26, arg=6]，响应第 8 字节(含 report id)=电量百分比。
#
# 2.4G 注意事项（实测教训）：
# - 回包有 ~2-5s 固有延迟（RF 时隙），多发帧无益；
# - 命令帧与按键报文共享信道，打字时发帧可能挤丢 key-up 造成"键粘连"
#   → 只在系统键鼠空闲 ≥ IDLE_GATE 秒时才发帧；
# - 不持久占用 HID 句柄，查询完立即释放。
import ctypes
import logging
import time

import hid

log = logging.getLogger("battery")

VID = 0x320F
USAGE_PAGE = 0xFF1C
BATTERY_FRAME = bytes([4, 32, 0, 26, 6] + [0] * 59)
ATTEMPTS = 2          # 最多发 2 帧
ATTEMPT_WAIT = 3.0    # 每帧等 3s
IDLE_GATE = 1.2       # 键鼠空闲 ≥1.2s 才允许发帧
IDLE_MAX_WAIT = 6.0   # 等不到空闲最多等 6s，放弃本次查询


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds():
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return IDLE_GATE  # 拿不到就放行
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def _wait_for_idle():
    deadline = time.time() + IDLE_MAX_WAIT
    while time.time() < deadline:
        if _idle_seconds() >= IDLE_GATE:
            return True
        time.sleep(0.2)
    return False


def find_device_path():
    for d in hid.enumerate(VID):
        if d["usage_page"] == USAGE_PAGE:
            return d["path"]
    return None


def query_battery():
    """返回 (percent, status) 或 None。只在键鼠空闲时发帧，避免干扰按键。"""
    t_start = time.time()
    path = find_device_path()
    if not path:
        log.warning("no 0x320F device with usage_page 0xFF1C found")
        return None
    dev = hid.device()
    try:
        dev.open_path(path)
    except OSError:
        log.warning("open_path failed")
        return None
    try:
        dev.set_nonblocking(0)
        for _ in range(8):                      # 清掉上次的迟到回包
            if not dev.read(65, timeout_ms=1):
                break
        for attempt in range(1, ATTEMPTS + 1):
            if not _wait_for_idle():
                log.info("query: user typing, skipped (%.1fs)",
                         time.time() - t_start)
                return None
            try:
                dev.write(BATTERY_FRAME)
            except OSError:
                log.warning("write failed (attempt %d)", attempt)
                return None
            t0 = time.time()
            while time.time() - t0 < ATTEMPT_WAIT:
                r = dev.read(65, timeout_ms=120)
                if r and len(r) >= 10 and r[0] == 4 and r[3] == 26:
                    percent, status = r[8], r[9]
                    if 0 < percent <= 100:
                        log.info("query: %d%% status=%d (attempt %d, %.1fs)",
                                 percent, status, attempt,
                                 time.time() - t_start)
                        return percent, status
        log.info("query: no response after %d attempts (%.1fs) — keyboard "
                 "asleep or off", ATTEMPTS, time.time() - t_start)
        return None
    except OSError:
        log.warning("device I/O error mid-query")
        return None
    finally:
        dev.close()
