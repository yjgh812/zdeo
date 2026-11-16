# -*- coding: utf-8 -*-
"""_s4u_migrate_20261023.py — 故障同根任务族 Interactive→S4U 一次性迁移脚本 v2
（主人 2026-10-23 令"干"；方案源=每日工作流SOP一百七十八补记⑥ + daily_deep_review_20261022 §8.3-2）

v2 修正: schtasks /xml 输出编码不统一(UTF-16带BOM 或 GBK)——v1 对 GBK 文件误用 utf-16
解码产乱码致替换不中。v2 字节级 BOM 检测选编码, 备份/写回同编码。
替换规则: InteractiveToken→S4U 替换; Principal 无 LogonType 节点(缺省=仅交互)则
在 </UserId> 后插入 <LogonType>S4U</LogonType>; 已 S4U 跳过。
不实弹 /run 链任务; 仅 DirectionBrief_0805(纯只读)由调用方单独实弹。
回滚: 备份 XML 按原编码写回 schtasks /create /f /tn <tn> /xml <bak>。
"""
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TASKS = [
    "TulongNightChain_2230",
    "SealSnapshot_1515",
    "SealSnapshot_Live_1457",
    "DirectionBrief_0805",
    "DDProtectGate_0800",
    "AddPositionWatch_1400",
    "ActExclShadow_2320",
    "NightChainFallback_2205",
    "DirectionCensus_1530",
    "TulongSealHealWatch_1520",
]
BAK_DIR = r"D:\zcodesj\data\s4u_backup_20261023"
NEW_DIR = r"D:\zcodesj\data\s4u_new_20261023"
INS = "<LogonType>S4U</LogonType>"


def run(cmd):
    p = subprocess.run(cmd, capture_output=True)
    out = p.stdout.decode("gbk", errors="replace") + p.stderr.decode("gbk", errors="replace")
    return p.returncode, out


def decode_smart(raw: bytes):
    """BOM 判编码: UTF-16(FF/FE BOM) 或 GBK(本地代码页)。返回(文本, 写回编码)。"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    return raw.decode("gbk", errors="strict"), "gbk"


def main():
    os.makedirs(BAK_DIR, exist_ok=True)
    os.makedirs(NEW_DIR, exist_ok=True)
    ok, skip, fail = [], [], []
    for t in TASKS:
        tn = "\\" + t
        p = subprocess.run(["schtasks", "/query", "/tn", tn, "/xml"], capture_output=True)
        if p.returncode != 0:
            out = (p.stdout + p.stderr).decode("gbk", errors="replace")
            fail.append((t, f"导出失败 rc={p.returncode}: {out[:120]}"))
            continue
        try:
            text, enc = decode_smart(p.stdout)
        except Exception as e:
            fail.append((t, f"解码失败: {e}"))
            continue
        with open(os.path.join(BAK_DIR, t + "_orig.xml"), "w", encoding=enc) as f:
            f.write(text)
        if INS in text:
            skip.append((t, "已是S4U"))
            continue
        if "<LogonType>InteractiveToken</LogonType>" in text:
            new_xml = text.replace("<LogonType>InteractiveToken</LogonType>", INS, 1)
            how = "替换InteractiveToken"
        elif "</UserId>" in text and "<Principal" in text:
            new_xml = text.replace("</UserId>", "</UserId>\n      " + INS, 1)
            how = "Principal内插入(原缺省仅交互)"
        else:
            skip.append((t, "无Principal/UserId结构,人工检查备份"))
            continue
        new_path = os.path.join(NEW_DIR, t + "_s4u.xml")
        with open(new_path, "w", encoding=enc) as f:
            f.write(new_xml)
        rc, out = run(["schtasks", "/create", "/f", "/tn", tn, "/xml", new_path])
        if rc != 0:
            fail.append((t, f"重注册失败 rc={rc}: {out[:150]}"))
            continue
        rc, out = run(["schtasks", "/query", "/tn", tn, "/v", "/fo", "LIST"])
        logon = next((l.split(":", 1)[1].strip() for l in out.splitlines()
                      if l.strip().startswith("登录状态")), "?")
        enabled = "已启用" in out
        ok.append((t, f"{how} | 登录状态={logon} 启用={enabled}"))
    print("=" * 60)
    for t, msg in ok:
        print(f"OK   {t}: {msg}")
    for t, msg in skip:
        print(f"SKIP {t}: {msg}")
    for t, msg in fail:
        print(f"FAIL {t}: {msg}")
    print(f"汇总: ok={len(ok)} skip={len(skip)} fail={len(fail)} (备份 {BAK_DIR})")


if __name__ == "__main__":
    main()
