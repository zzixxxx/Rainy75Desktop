# Rainy75Desktop
# 不好用 不用了
WOB Rainy75 键盘的 Windows 托盘电量助手 —— 2.4G 连接下也能看电量百分比。

官方桌面驱动和网页驱动都不显示电量百分比，本工具通过逆向 [WOB 网页驱动](https://www.wobwxe.com/) 的 WebHID 协议实现电量读取，UI 交互参考 [AirPodsDesktop](https://github.com/SpriteOvO/AirPodsDesktop)。

## 功能

- 托盘常驻 WOB logo 图标，tooltip 两行显示电量百分比与更新时间
- 点击托盘图标 / 重复启动 exe 弹出电量面板（固定右下角，淡入上滑 / 淡出下滑动效）
- 面板内键盘实拍图摇摆浮动动效、圆角胶囊电量条、Fluent 系列图标按钮
- 设置页：开机自启开关、日志文件夹 / 网页驱动快捷入口
- 滚动运行日志（`%APPDATA%\Rainy75Desktop\logs`，1MB×3），含未捕获异常堆栈

## 电量协议（逆向自 WOB 网页驱动）

- 设备：VID `0x320F`（Evision），2.4G 接收器 PID `0x5088` / 有线 `0x5055`
- 通道：vendor HID collection，usagePage `0xFF1C`，usage `0x92`，reportId `4`
- 查询帧：`[0x04, 32, 0, 26, 6]` 补零至 64 字节（`[chkLo, chkHi, cmd=26, arg=6]`，校验和 = 后续字节求和）
- 响应：回显帧第 8 字节（含 report id）= 电量百分比，第 9 字节疑似充电状态
- 2.4G 回包按 ~5s 一班的节奏到达，首帧发出到第一个回包需 5~12s，
  因此**监听窗口必须 ≥15s**（窗口内每 ~1.5s 补一帧、持续读、命中即返回）
- 键盘长时间空闲也照常应答；查询无响应通常意味着接收器未插或键盘关机

## 不干扰按键的设计

2.4G 下 vendor 命令帧与按键报文共享信道，打字瞬间发帧可能挤丢 key-up 报文，
造成单键重复（"t" 变 "ttttt"）。因此：

- **空闲门控**：`GetLastInputInfo` 检测系统键鼠输入，每帧发送前要求空闲 ≥0.3s（打字间隙即可满足，不拖慢查询）
- **按需查询**：无定时轮询，仅启动时 / 打开面板 / 手动刷新时查询，20s 新鲜期内不重查；失败且面板可见时每 3s 自动重试
- 查询完立即释放 HID 句柄，平时与键盘零交互

## 构建

```powershell
pip install PySide6 hidapi pyinstaller
pyinstaller --noconfirm --onefile --windowed --name Rainy75Desktop `
    --icon assets/logo.ico --add-data "assets;assets" app.py
```

产物在 `dist\Rainy75Desktop.exe`（onefile 运行时有引导+主两个进程，属正常现象）。

## 说明

- 网页驱动仅支持有线连接（设备过滤白名单只有 PID `0x5055`），改键/灯效请插线后使用
- 充电状态字节的含义未经充电实测验证
- 仅在 Rainy75 RGB 版（2.4G）上实测过；同为 Evision 320F 方案的键盘理论上可按同协议适配
