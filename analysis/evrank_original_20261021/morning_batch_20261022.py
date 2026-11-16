# -*- coding: utf-8 -*-
"""20261022 开盘执行: 5只验证组合买入(主人指令,总额14.2万遵守A1 80%).
守卫: 时段/竞价异动(开盘涨跌>±3%跳过该票)/逐笔留痕. A1已调0.80(仓位79.6%封顶内).
"""
import json
import math
import os
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
EXECUTOR = r"D:\zcodesj\trade_executor.py"
NOTE = ("主人20261021指令:买入5只验证组合(雅戈尔80%胜率/中行75%+3.84%/陕煤66%/翰宇医药次线4涨停/新北洋AI主线刚突破);"
        "四重复验框架+双维概率排名;A1已调0.80;DD_GATE特批;止损-8%")

ORDERS = [  # (code, name, volume, ref_price) 金额=volume*ref
    ("600177", "雅戈尔", 5400, 8.28),      # ≈4.47万 纺服
    ("601988", "中国银行", 3100, 7.24),    # ≈2.24万 银行(R30b剩余3.7万内)
    ("601225", "陕西煤业", 1000, 27.69),   # ≈2.77万 煤炭
    ("300199", "翰宇药业", 1000, 26.71),   # ≈2.67万 医药次线
    ("002376", "新北洋", 2700, 7.49),      # ≈2.02万 AI主线刚突破
]
SKIP_BAND = 0.03   # 开盘较昨收异动>±3%跳过等指示


def gtimg(code6):
    pre = "sh" if code6.startswith(("6",)) else "sz"
    raw = urllib.request.urlopen(f"http://qt.gtimg.cn/q={pre}{code6}", timeout=8) \
        .read().decode("gbk", errors="replace")
    f = raw.split('"')[1].split("~")
    return {"price": float(f[3]), "prev_close": float(f[4]), "open": float(f[5]),
            "bid1": float(f[9]) if len(f) > 9 else float(f[3])}


def run(cmd):
    r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
    return r.returncode, r.stdout or ""


def main():
    execute = "--execute" in sys.argv
    now = time.localtime()
    hm = (now.tm_hour, now.tm_min)
    in_session = (hm >= (9, 35) and hm <= (11, 30)) or (hm >= (13, 0) and hm <= (14, 55))
    print(f"[mode] {'EXECUTE' if execute else 'CHECK-ONLY'} | {time.strftime('%H:%M:%S')} "
          f"| in_session={in_session}")
    if execute and not in_session:
        print("[exit] 非交易时段(09:35-11:30/13:00-14:55)")
        return 2
    if not execute and not in_session:
        print("[note] 盘外时段, 仅做行情检查, 不调用executor")

    results = []
    for code6, name, vol, ref in ORDERS:
        try:
            q = gtimg(code6)
        except Exception as e:
            results.append({"code": code6, "status": "SKIP_NO_QUOTE", "err": str(e)})
            print(f"[SKIP] {code6} {name}: 行情失败 {e}", flush=True)
            continue
        gap = q["open"] / q["prev_close"] - 1
        print(f"[check] {code6} {name}: 开盘{q['open']} 现价{q['price']} "
              f"竞价涨跌{gap:+.2%} (昨收{q['prev_close']})")
        if abs(gap) > SKIP_BAND:
            results.append({"code": code6, "name": name, "status": "SKIP_GAP",
                            "gap": round(gap, 4)})
            print(f"[SKIP] {code6} {name}: 竞价异动{gap:+.2%}超±3%, 跳过等指示", flush=True)
            continue
        px = math.floor(max(q["bid1"], q["price"] * 0.999) * 100) / 100
        cmd = [sys.executable, "-u", EXECUTOR, "buy", "--code", code6, "--name", name,
               "--source", "manual_confirmed", "--sector", "auto", "--note", NOTE,
               "--price", str(px), "--volume", str(vol), "--execute",
               "--owner-approve-ddgate"]
        if not execute:
            print("[dry-run skipped] 盘外不调用executor, 明早09:35后 --execute 执行", flush=True)
            continue
        rc, out = run(cmd)
        placed = '"executed": true' in out
        status = "PLACED" if (rc == 0 and placed) else f"REJECT_rc{rc}"
        # 提取 sector 校验: executor 内部按 l1 归属校验, 传 auto 时回退预检--用预检脚本
        results.append({"code": code6, "name": name, "vol": vol, "limit": px,
                        "status": status})
        print(f"[{status}] BUY {code6} {name} {vol}股 limit={px}", flush=True)
        if status != "PLACED":
            print("  output:", out[-400:], flush=True)
        time.sleep(1)

    ok = [r for r in results if r.get("status") == "PLACED"]
    amt = sum(r["vol"] * r["limit"] for r in results if r.get("status") == "PLACED")
    print(f"[done] execute={execute} 成功{len(ok)}/5笔 提交≈{amt:.0f}元")
    print("[next] westock MCP核验持仓; 5笔入场价记入验证台账; 止损-8%逐一设定;"
          "REJECT明细若为R30b行业占用(中行)则属预期保护, 不绕过")
    date8 = time.strftime("%Y%m%d")
    assert date8.isdigit() and len(date8) == 8, "非法日期格式"
    out_path = os.path.join(r"D:\zcodesj\analysis", f"verify5_exec_{date8}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"date": time.strftime("%Y-%m-%d"), "execute": execute,
                   "results": results, "amt": round(amt, 0)}, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
