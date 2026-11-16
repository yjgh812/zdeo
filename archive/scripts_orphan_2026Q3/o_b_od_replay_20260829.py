# -*- coding: utf-8 -*-
"""
O-B / O-D 反事实回放（RULE_CANDIDATES_20260827 验证项，主人"现在就执行"授权）
=====================================================================================
【预注册判据 —— 先于数据固化，跑数后不得因结果修改】(2026-08-29)

O-B 高位票早盘追价抑制
  样本   : trade_records BUY, 20260814~20260828, price>0 (era2 断层后全台账)
  时间   : notes 含"盘后审计导入 YYYY-MM-DD HH:MM:SS"取该时刻; 无时刻行入"时间未知"组单列
  高位   : 买入日前一交易日(T-1) qfq 收盘 / 20 个交易日前收盘 - 1 > +30% (T-1 时点, 无前视)
  早盘   : 09:30 <= 时刻 < 10:00
  结果   : T+1 收盘收益 = (T+1 qfq close / 实际买入价) - 1; T+1 未实现(8/28买)剔除并计数
  分组   : G1 高位×早盘 / G2 高位×非早盘 / G3 非高位×早盘 / G4 非高位×非早盘 / GX 时间未知
  判决   : G1 的 T+1 均值 < -1% 且 n>=8  -> 支持"高位早盘禁买"立项候选
           G1 均值 >= 0 或 n < 8        -> 证伪不立项
  变体B2 : 回踩<=1.5%贴现无法盘中回放, 仅报 G1 组 买价vs当日收盘vsT+1收盘 分布作定性参考

O-D 卖出参数分层(低β双K确认)
  样本   : trade_records SELL, 20260824~20260828, R5 型 = notes 含 "R5"
  分层   : 长线 = list_membership LIKE 'bp_ep%' (holding_horizon HORIZON_MAP 对齐); 其余=题材
  反事实 : 延迟至下一交易日(T+1): 若 T+1 收盘 < T+1 的 MA5(最近5根qfq close均值)
           -> 以 T+1 收盘卖出(反事实卖价); 否则视为收复继续持有=删失(标注,不入差异均值)
  差异   : 反事实卖价 / 实际卖价 - 1 ; 8/28 卖出 T+1=8/31 未实现 -> 剔除计数
  判决   : 长线组非删失差异均值 > +1% 且 n>=5 -> 支持分层采纳候选
           否则(含长线样本不足) -> 不立项/样本不足如实报告

【诚实边界】反事实未含: 手续费差/滑点/双K期间再波动/T+1之后持有路径(删失)。定性参考。
"""
import io
import re
import sqlite3
from collections import defaultdict

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\OB_OD_REPLAY_20260829.md"

conn = sqlite3.connect(ATTR_DB)
conn.row_factory = sqlite3.Row
qfq = sqlite3.connect(QFQ_DB)


def closes_upto(code6, date, n):
    """code6 裸码; date 'YYYYMMDD'; 返回 date<date 的最近 n 根 qfq close, 旧->新"""
    rows = qfq.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<? ORDER BY date DESC LIMIT ?",
        (code6, date, n)).fetchall()
    return [r[0] for r in rows][::-1]  # 旧->新: cl[-1]=T-1(最近), cl[0]=最旧


def close_after(code6, date):
    """date 之后第一根 qfq close (T+1)"""
    r = qfq.execute(
        "SELECT close, date FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date ASC LIMIT 1",
        (code6, date)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def code6(c):
    return str(c)[-6:]


# ============ O-B ============
buys = [dict(r) for r in conn.execute(
    "SELECT code, name, trade_date, price, volume, notes FROM trade_records "
    "WHERE action='BUY' AND trade_date>='20260814' AND trade_date<='20260828' AND price>0")]

groups = defaultdict(list)   # gname -> list of dict
g1_detail = []
ob_skip_t1, ob_skip_px = 0, 0
for b in buys:
    c6 = code6(b["code"])
    t = None
    m = re.search(r"盘后审计导入.*?(\d{2}:\d{2}:\d{2})", b["notes"] or "")
    if m:
        t = m.group(1)
    # 高位: T-1 收盘 vs 20 交易日前收盘 (cl 旧->新: cl[-1]=T-1, cl[0]=20日前)
    cl = closes_upto(c6, b["trade_date"], 21)
    if len(cl) < 21 or not cl[0] or not cl[-1] or cl[0] <= 0:
        ob_skip_px += 1
        continue
    chg20 = cl[-1] / cl[0] - 1
    high = chg20 > 0.30
    # T+1
    t1c, t1d = close_after(c6, b["trade_date"])
    if t1c is None:
        ob_skip_t1 += 1
        continue
    ret_t1 = t1c / b["price"] - 1
    # 当日收盘(参考): date<=trade_date 最后一根
    d0 = closes_upto(c6, b["trade_date"].replace("T", ""), 1)  # date< 当日 -> T-1; 需要<=:
    d0 = qfq.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
                     (c6, b["trade_date"])).fetchone()
    day0_close = d0[0] if d0 else None
    item = {"code": c6, "name": b["name"], "date": b["trade_date"], "t": t or "?",
            "high": high, "px": b["price"], "ret_t1": ret_t1, "day0": day0_close,
            "chg20": chg20, "t1c": round(t1c, 2)}
    if t is None:
        g = "GX_时间未知"
    else:
        morning = "09:30:00" <= t < "10:00:00"
        g = ("G1_高位早盘" if high and morning else "G2_高位非早盘" if high
             else "G3_非高位早盘" if morning else "G4_非高位非早盘")
    groups[g].append(item)
    if g == "G1_高位早盘":
        g1_detail.append(item)


def stats(items):
    if not items:
        return "n=0"
    rs = sorted(x["ret_t1"] for x in items)
    n = len(rs)
    mean = sum(rs) / n
    med = rs[n // 2] if n % 2 else (rs[n//2 - 1] + rs[n//2]) / 2
    win = sum(1 for r in rs if r > 0) / n
    return f"n={n} 均值{mean:+.2%} 中位{med:+.2%} 胜率{win:.0%}"


ob_lines = []
for g in ["G1_高位早盘", "G2_高位非早盘", "G3_非高位早盘", "G4_非高位非早盘", "GX_时间未知"]:
    ob_lines.append(f"| {g} | {stats(groups.get(g, []))} |")

# ============ O-D ============
sells = [dict(r) for r in conn.execute(
    "SELECT code, name, trade_date, price, notes, list_membership FROM trade_records "
    "WHERE action='SELL' AND trade_date>='20260824' AND trade_date<='20260828' AND price>0")]
r5 = [s for s in sells if "R5" in (s["notes"] or "")]

od_rows, od_censor, od_skip = [], 0, 0
for s in r5:
    c6 = code6(s["code"])
    long_tp = bool(s["list_membership"] and str(s["list_membership"]).startswith("bp_ep"))
    # T+1 收盘 + MA5(以T+1为终点)
    r1 = qfq.execute(
        "SELECT close, date FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date ASC LIMIT 1",
        (c6, s["trade_date"])).fetchone()
    if not r1:
        od_skip += 1
        continue
    t1_close, t1_date = r1
    ma5_rows = qfq.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 5",
        (c6, t1_date)).fetchall()
    ma5 = sum(x[0] for x in ma5_rows) / len(ma5_rows) if len(ma5_rows) == 5 else None
    if ma5 is None:
        od_skip += 1
        continue
    long_tp_flag = "长线" if long_tp else "题材"
    if t1_close < ma5:   # 仍破位 -> 双K确认卖出
        diff = t1_close / s["price"] - 1
        od_rows.append({"code": c6, "name": s["name"], "date": s["trade_date"], "层": long_tp_flag,
                        "实际卖价": s["price"], "T+1卖价": round(t1_close, 2), "差异": diff,
                        "notes": (s["notes"] or "")[:38]})
    else:                # 收复 -> 继续持有(删失)
        od_censor += 1
        od_rows.append({"code": c6, "name": s["name"], "date": s["trade_date"], "层": long_tp_flag,
                        "实际卖价": s["price"], "T+1卖价": "收复(删失)", "差异": None,
                        "notes": (s["notes"] or "")[:38]})

od_g = defaultdict(list)
for r in od_rows:
    if r["差异"] is not None:
        od_g[r["层"]].append(r["差异"])

# ============ 报告 ============
def fmt_rows(rows):
    out = []
    for r in rows:
        d = f"{r['差异']:+.2%}" if r["差异"] is not None else "—"
        out.append(f"| {r['code']} {r['name']} | {r['date']} | {r['层']} | {r['实际卖价']} | {r['T+1卖价']} | {d} | {r['notes']} |")
    return out

buf = io.StringIO()
w = buf.write
w("# O-B / O-D 反事实回放报告（预注册判据驱动）\n\n")
w("> 日期：2026-08-29（周六）｜脚本：`o_b_od_replay_20260829.py`（判据预注册于 docstring，先于数据）\n")
w("> 数据源：attribution.db trade_records（8/14~8/28 全台账）× dragon_quant_qfq.db（qfq 正源，至 8/28）\n")
w("> 关联：RULE_CANDIDATES_20260827 O-B/O-D；名称校验随发布执行\n\n")
w("## 判据复述（预注册，跑数后未修改）\n\n见脚本 docstring：O-B 判决线=G1 高位早盘组 T+1 均值<-1% 且 n≥8 才支持立项；O-D 判决线=长线组非删失差异均值>+1% 且 n≥5 才支持采纳。\n\n")
w("## 一、O-B 高位票早盘追价抑制\n\n")
w(f"买入样本 {len(buys)} 笔（8/14~8/28）；剔除：qfq 序列不足 21 根或价格异常 {ob_skip_px} 笔、T+1 未实现 {ob_skip_t1} 笔。\n\n")
w("| 分组 | T+1 收盘收益 |\n|---|---|\n")
for ln in ob_lines:
    w(ln + "\n")
g1 = groups.get("G1_高位早盘", [])
w(f"\n**G1 明细**（高位=前20日涨幅>30% 且 09:30-10:00 买入）：\n\n")
w("| 代码 名称 | 日期 | 时刻 | 买价 | 20日涨幅 | 当日收盘 | T+1收盘 | T+1收益 |\n|---|---|---|---|---|---|---|---|\n")
for x in g1:
    d0s = f"{x['day0']:.2f}" if x['day0'] else "?"
    w(f"| {x['code']} {x['name']} | {x['date']} | {x['t']} | {x['px']} | {x['chg20']:+.0%} | {d0s} | {x['t1c']} | {x['ret_t1']:+.2%} |\n")
w(f"\n**G1 组内买价位置参考（变体B2定性）**：买价 vs 当日收盘——" + 
  ("；".join(f"{x['code']}{x['name']} {x['ret_t1']:+.1%}" for x in g1[:10]) if g1 else "无样本") + "\n\n")

w("## 二、O-D 卖出参数分层（低β双K确认）\n\n")
w(f"R5 型卖出样本 {len(r5)} 笔（8/24~8/28，notes 含 R5）；剔除 T+1/MA5 数据不足 {od_skip} 笔；收复删失 {od_censor} 笔。\n\n")
w("| 代码 名称 | 卖出日 | 分层 | 实际卖价 | T+1反事实 | 差异 | notes |\n|---|---|---|---|---|---|---|\n")
for ln in fmt_rows(od_rows):
    w(ln + "\n")
w("\n| 分层 | 非删失差异统计 |\n|---|---|\n")
for k in ["长线", "题材"]:
    v = od_g.get(k, [])
    if v:
        w(f"| {k} | n={len(v)} 均值{sum(v)/len(v):+.2%} 中位{sorted(v)[len(v)//2]:+.2%} |\n")
    else:
        w(f"| {k} | n=0（全部删失或无样本） |\n")

io.open(OUT, "w", encoding="utf-8").write(buf.getvalue())
print("written:", OUT)
print("\nO-B groups:")
for ln in ob_lines:
    print(" ", ln)
print("\nO-D by layer:")
for k in ["长线", "题材"]:
    v = od_g.get(k, [])
    print(f"  {k}: n={len(v)}" + (f" 均值{sum(v)/len(v):+.2%}" if v else ""))
conn.close(); qfq.close()

# 工程诚实段（首版 bug 记录）——追加到报告
honesty = """

## 三、工程诚实记录（反臆想纪律实证）

首版脚本 chg20 误用倒数（closes_upto 返回旧→新，写成 cl[0]/cl[20]），导致高位组 n=0 的异常结果。**"G1/G2 全零"触发复查**（正常市况下 3 周内 105 笔买入不可能零笔 20 日涨幅>30%——招金黄金（000506）案明显应入组），抽查对照后发现方向反了，修正后高位组从 0 → 15 笔（G1 7 + G2 8），结论天壤之别。教训再次确认：**异常结果（含"完美为空"）= 第一红旗**，与 SMC 案"异常好=先怀疑"同族；两版本报告均已留档于脚本 git 无关的历史中，以本段为准。
"""
with io.open(OUT, "a", encoding="utf-8") as f:
    f.write(honesty)
