# -*- coding: utf-8 -*-
"""
notify.py — 屠龙系统统一告警通道 (2026-08-15 H1 补缺; 2026-09-08 按主人裁决精简)
================================================================================
**主人 2026-09-08 裁决: 手机推送通道「不要、禁止、不需要」** — 外部通道
(pushplus/Server酱/bark/wecom/telegram) 代码路径已整体移除, 永久禁用,
禁止再提议或配置。本脚本仅保留两条通道:
  1. 本地通道: Windows 桌面通知(toast)  (机器开机即弹, 无需外网)
  2. 永久留痕: 全部通知追加写入 reports/notifications.log

用法 (CLI 合同与旧版一致, cron prompt 无需改动):
  python notify.py send --title "标题" --content "内容" [--level error|warn|info] [--channel toast]
  python notify.py test           # 通道自检

退出码: 0 = 送达(toast 成功或纯日志模式); 1 = toast 失败仅写入日志。
"""
import os
import subprocess
import sys
from datetime import datetime

BASE = "D:/zcodesj"
LOG_PATH = os.path.join(BASE, "reports", "notifications.log")
TOAST_SCRIPT = os.path.join(BASE, "_notify_toast.ps1")

# 主人 2026-09-08 裁决: 外部推送通道永久禁用(--channel 传外部通道名直接拒绝)
BANNED_CHANNELS = ("pushplus", "sct", "bark", "wecom", "telegram")


def write_toast_script():
    """生成 PowerShell WinRT toast 脚本(仅生成一次)。"""
    if os.path.exists(TOAST_SCRIPT):
        return
    ps = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $t.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($t.CreateTextNode($args[0])) | Out-Null
$nodes.Item(1).AppendChild($t.CreateTextNode($args[1])) | Out-Null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('TulongSystem').Show($n)
'''
    with open(TOAST_SCRIPT, "w", encoding="utf-8-sig") as f:
        f.write(ps)


def send_toast(title, content):
    """Windows 桌面通知(尽力而为,失败不致命)。"""
    write_toast_script()
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", TOAST_SCRIPT, title, content],
        capture_output=True, text=True, timeout=25)
    return r.returncode == 0


def write_log(level, title, content, channel, ok, detail=""):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    line = "%s|%s|%s|%s|%s|%s" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, channel,
        "OK" if ok else "FAIL", title, (detail or content)[:300])
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _spool_relay(title, content):
    """2026-10-23: S4U/session-0 调用方 toast 不可达 → 写 spool 并触发
    NotifyToastRelay(Interactive) 在用户会话内补弹。fire-and-forget 不影响调用链;
    用户已注销时中继不启动(物理上限), spool 由中继 24h 自清。"""
    try:
        spool_dir = os.path.join(BASE, "reports", "_toast_spool")
        os.makedirs(spool_dir, exist_ok=True)
        name = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".txt"
        with open(os.path.join(spool_dir, name), "w", encoding="utf-8") as f:
            f.write(title + "\n" + content + "\n")
        subprocess.run(["schtasks", "/run", "/tn", "NotifyToastRelay"],
                       capture_output=True, timeout=10)
    except Exception as e:
        write_log("warn", title, content, "relay", False, str(e))


def deliver(title, content, level, channel=None):
    if channel and channel.lower() in BANNED_CHANNELS:
        msg = "通道 %s 已按主人 2026-09-08 裁决永久禁用(手机推送不要/禁止/不需要), 仅落日志" % channel
        write_log(level, title, content, channel, False, msg)
        print("[notify] %s → %s" % (datetime.now().strftime("%H:%M:%S"), msg))
        channel = None  # 降级为 toast, 不因禁用通道中断调用链

    ok = False
    try:
        ok = send_toast(title, content)
    except Exception as e:
        write_log(level, title, content, "toast", False, str(e))
        print("[notify] %s → toast FAIL: %s" % (datetime.now().strftime("%H:%M:%S"), e))
    if ok:
        write_log(level, title, content, "toast", True)
        print("[notify] %s → toast OK" % datetime.now().strftime("%H:%M:%S"))
        return 0
    if not channel:  # 无显式通道要求时 toast 失败仍视为仅日志, 不报调用链错
        write_log(level, title, content, "log-only", False, "toast失败仅留痕")
    _spool_relay(title, content)
    return 1


def main():
    args = sys.argv[1:]
    if not args or args[0] == "test":
        title, content = "屠龙系统通知通道自检", "notify.py 链路正常(toast/日志; 外部通道已按主人9/8裁决禁用)"
        return deliver(title, content, "info")

    if args[0] == "send":
        kwargs = {}
        i = 1
        while i < len(args):
            if args[i] in ("--title", "--content", "--level", "--channel") and i + 1 < len(args):
                kwargs[args[i][2:]] = args[i + 1]
                i += 2
            else:
                i += 1
        title = kwargs.get("title", "屠龙系统通知")
        content = kwargs.get("content", "")
        level = kwargs.get("level", "info")
        return deliver(title, content, level, kwargs.get("channel"))

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
