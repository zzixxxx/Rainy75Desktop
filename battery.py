# Rainy75 (WOB / Evision 320F) 电量查询协议
# 逆向自 wobwxe.com 网页驱动：vendor collection usagePage 0xFF1C，reportId 4，
# 命令帧 [chkLo, chkHi, cmd=26, arg=6]，响应第 8 字节(含 report id)=电量百分比。
#
# 2.4G 注意事项（实测教训，2026-07 三分钟探测定量验证）：
# - 回包按 ~5s 一班的节奏到达，且从首帧到第一个回包可能要 5~12s
#   → 监听窗口必须 ≥15s，期间每 ~1.5s 补一帧、全程持续读；
# - 键盘长时间空闲(实测 idle 5 分钟)也照常应答，"睡眠不应答"是误判；
# - 命令帧与按键报文共享信道，打字瞬间发帧可能挤丢 key-up 造成"键粘连"
#   → 每帧发送前要求键鼠空闲 ≥ IDLE_GATE（0.3s，打字间隙即可满足）；
# - 不持久占用 HID 句柄，查询完立即释放。
import ctypes
import logging
import time

import hid

log = logging.getLogger("battery")

VID = 0x320F
USAGE_PAGE = 0xFF1C
BATTERY_FRAME = bytes([4, 32, 0, 26, 6] + [0] * 59)
WINDOW = 15.0         # 监听窗口（回包最长 ~12s 才来，不能再短）
FRAME_GAP = 1.5       # 补帧间隔
IDLE_GATE = 0.3       # 每帧要求键鼠空闲 ≥0.3s（避开打字瞬间）


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _idle_seconds():
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return IDLE_GATE  # 拿不到就放行
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def find_device_path():
    for d in hid.enumerate(VID):
        if d["usage_page"] == USAGE_PAGE:
            return d["path"]
    return None


def query_battery():
    """返回 (percent, status) 或 None。

    15s 窗口内每 ~1.5s 补一帧（每帧要求键鼠短暂空闲），全程持续监听，
    命中即返回。
    """
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
        sent = 0
        next_send = 0.0
        while time.time() - t_start < WINDOW:
            if time.time() >= next_send and _idle_seconds() >= IDLE_GATE:
                dev.write(BATTERY_FRAME)
                sent += 1
                next_send = time.time() + FRAME_GAP
            r = dev.read(65, timeout_ms=100)
            if r and len(r) >= 10 and r[0] == 4 and r[3] == 26:
                percent, status = r[8], r[9]
                if 0 < percent <= 100:
                    log.info("query: %d%% status=%d (%d frames, %.1fs)",
                             percent, status, sent, time.time() - t_start)
                    return percent, status
                log.warning("query: implausible payload %s", list(r[:12]))
        log.info("query: no response (%d frames, %.1fs) — receiver absent "
                 "or keyboard off", sent, time.time() - t_start)
        return None
    except OSError:
        log.warning("device I/O error mid-query")
        return None
    finally:
        dev.close()
