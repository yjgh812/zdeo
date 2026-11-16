#!python
# -*- coding: utf-8 -*-
"""
屠龙候选总闸 (2026-08-19 错误闭环落地)

背景: 8/19 选股事故——临时扫描绕过正规工具链, 选入炸板股(秦川机床)与
停板污染资金股(中鼎股份), 且3只农业链与持仓6只农业严重重叠。
根因: 靠人脑记住教训不可靠, 必须机器拦截。

五道闸门(前3道全自动, 后2道参数化提示):
  G1 池来源闸  : 候选必须出现在最新 universe_scan CSV (三池扫描器输出),
                 杜绝临时扫描的前置过滤偏差
  G2 负面信号闸: pool_b=炸板 → REJECT(实证: 炸板次日63%低开-0.45%);
                 近5日停板天数>0 → DOWNGRADE 争议区, 须拆逐日核验
  G3 双口径闸  : 复用 r22_dual_check.r22() 读 em_flow_snapshot.db,
                 THS与EM方向相反→REJECT; 同负→DOWNGRADE; 同正→PASS
  G4 组合分散闸: --holdings 传入现有持仓, 内置板块字典查板块重合,
                 同板块新增>2只 → DOWNGRADE(人工确认)
  G5 超买阈值闸: --rsi2 逐只传入RSI2(逗号分隔, 可选), >90 者不得进前二

用法:
  python tulong_candidate_gate.py 000725 300131 [--holdings 000998,600598,...]
      [--rsi2 66.4,84.7,95.8] [--scan-csv 路径]

输出: 每只候选 verdict = PASS / DOWNGRADE(附原因) / REJECT(附原因)
铁律: REJECT 一律不得进入备选清单; DOWNGRADE 不得排进前二优先级。
"""
import sys, os, csv, glob, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r22_dual_check import r22  # 复用双口径闸

# 内置板块字典(常用标的, 未覆盖的标人工确认)
SECTOR_MAP = {
    # 面板
    "000725": "面板", "000100": "面板", "002047": "面板",
    # 半导体/芯片
    "300131": "半导体", "002119": "半导体", "300077": "半导体", "002185": "半导体",
    "300655": "半导体", "600703": "半导体", "600330": "半导体",
    # 农业
    "000998": "农业", "600598": "农业", "601952": "农业", "002385": "农业",
    "000735": "农业", "300021": "农业", "301058": "农业基建", "000930": "农业基建",
    "002215": "农业基建", "000019": "农业基建", "002157": "农业",
    # 机器人/汽零/母机
    "000887": "机器人汽零", "000837": "机器人汽零", "002031": "机器人汽零",
    "002248": "机器人汽零", "300024": "机器人汽零", "002703": "机器人汽零",
    "603166": "机器人汽零",
    # 算力/数据
    "603881": "算力", "300017": "算力", "002491": "通信",
    # 医药
    "600267": "医药", "600216": "医药", "002262": "医药", "600829": "医药",
    # 其他持仓
    "600186": "调味品", "002378": "小金属", "601609": "铜加工", "601919": "航运",
    "600629": "建筑", "002167": "锆材料",
}

def load_latest_scan(scan_csv=None):
    """读最新 universe_scan CSV → {code: row_dict}"""
    if scan_csv is None:
        files = sorted(glob.glob(r"D:\zcodesj\reports\universe_scan_*.csv"))
        if not files:
            return {}, "无扫描CSV(先跑 tulong_universe_scanner.py)"
        scan_csv = files[-1]
    pool = {}
    try:
        with open(scan_csv, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                pool[row["code"].zfill(6)] = row
    except Exception as e:
        return {}, f"读CSV失败: {e}"
    return pool, os.path.basename(scan_csv)

def gate(code, holdings, pool, csv_note):
    """单只候选过五道闸, 返回 (verdict, reasons[])"""
    reasons, verdict = [], "PASS"
    code = code.zfill(6)

    # G1 池来源
    row = pool.get(code)
    if row is None:
        return "REJECT", [f"G1不在三池扫描CSV({csv_note}) → 临时来源, 杜绝前置过滤偏差"]
    reasons.append(f"G1池内✅({row.get('name','')})")

    # G2 负面信号 (炸板标记在pool_a或pool_b列, 两列都查)
    marks = ((row.get("pool_a") or "") + "|" + (row.get("pool_b") or ""))
    limit_days = row.get("近5日停板天数") or "0"
    if "炸板" in marks:
        return "REJECT", reasons + ["G2炸板股 → 实证次日63%低开-0.45% (最强负面信号)"]
    try:
        ld = int(float(limit_days))
    except ValueError:
        ld = 0
    if ld > 0:
        verdict = "DOWNGRADE"
        reasons.append(f"G2近5日停板{ld}天 → 争议区, 资金口径被污染须拆逐日")
    else:
        reasons.append("G2无炸板/停板✅")

    # G3 双口径
    r = r22(code)
    st = r["status"]
    if st == "reject":
        return "REJECT", reasons + [f"G3双口径反向: {r['note']}"]
    if st == "warn":
        verdict = "DOWNGRADE"
        reasons.append(f"G3双口径警告: {r['note']}")
    elif st == "pass":
        reasons.append(f"G3双口径同正✅: {r['note']}")
    else:  # no_data / stale
        verdict = "DOWNGRADE" if verdict == "PASS" else verdict
        reasons.append(f"G3快照缺数(fail-open, 须westock手动双口径核验): {r['note']}")

    # G4 组合分散
    sec = SECTOR_MAP.get(code, "未知板块")
    if holdings:
        hold_secs = [SECTOR_MAP.get(h.zfill(6), "未知板块") for h in holdings]
        same = hold_secs.count(sec)
        if same >= 2:
            verdict = "DOWNGRADE"
            reasons.append(f"G4持仓已有{same}只'{sec}' → 再买=板块过重")
        elif same == 1:
            reasons.append(f"G4持仓已有1只'{sec}', 可买但合计≤2")
        else:
            reasons.append(f"G4板块'{sec}'与持仓无重合✅")
    else:
        reasons.append(f"G4板块={sec} (未传--holdings, 人工确认集中度)")

    return verdict, reasons

def main():
    ap = argparse.ArgumentParser(description="屠龙候选总闸: 五道闸门机器拦截")
    ap.add_argument("codes", nargs="+", help="候选6位代码")
    ap.add_argument("--holdings", default="", help="现有持仓代码, 逗号分隔")
    ap.add_argument("--rsi2", default="", help="逐只RSI2值, 逗号分隔与codes同序(可选)")
    ap.add_argument("--scan-csv", default=None)
    args = ap.parse_args()

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    holdings = [h for h in args.holdings.replace("，", ",").split(",") if h.strip()]
    rsi2s = [x for x in args.rsi2.replace("，", ",").split(",") if x.strip()] if args.rsi2 else []
    pool, csv_note = load_latest_scan(args.scan_csv)

    print(f"=== 屠龙候选总闸 | 池源: {csv_note} | 候选{len(args.codes)}只 | 持仓基准{len(holdings)}只 ===")
    summary = {"PASS": [], "DOWNGRADE": [], "REJECT": []}
    for i, c in enumerate(args.codes):
        code = c.zfill(6)
        verdict, reasons = gate(code, holdings, pool, csv_note)
        # G5 超买提示(人工传入RSI2时)
        if rsi2s and i < len(rsi2s):
            try:
                v = float(rsi2s[i])
                if v > 90:
                    if verdict == "PASS":
                        verdict = "DOWNGRADE"
                    reasons.append(f"G5 RSI2={v}>90 超买 → 不得进前二优先级")
                else:
                    reasons.append(f"G5 RSI2={v}✅")
            except ValueError:
                pass
        name = pool.get(code, {}).get("name", "")
        tag = {"PASS": "✅", "DOWNGRADE": "⚠️", "REJECT": "❌"}[verdict]
        print(f"\n{tag} {code} {name} → {verdict}")
        for r in reasons:
            print(f"   - {r}")
        summary[verdict].append(f"{code}{name}")

    print("\n=== 汇总 ===")
    print(f"PASS    : {', '.join(summary['PASS']) or '无'}")
    print(f"DOWNGRADE: {', '.join(summary['DOWNGRADE']) or '无'} (不得排进前二)")
    print(f"REJECT  : {', '.join(summary['REJECT']) or '无'} (一律不得入选)")

if __name__ == "__main__":
    main()
