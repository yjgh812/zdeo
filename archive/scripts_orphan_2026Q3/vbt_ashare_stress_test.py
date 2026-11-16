"""适配器高强度回归（常驻 QA 工具）：对涨跌停高频股验证 vbt_ashare_adapter 掩码真实触发。

背景：本脚本 2026-08-28 压测曾抓到真 bug——卖出被涨跌停掩码拦下后，若再来买入信号，
vbt 会累积加仓（均价合并持仓），与"单仓位 long-only"语义不符；已在 _next_bar 加
单仓位状态机过滤修复（仅空仓才买、仅持仓才卖）。本脚本据此保留为常驻回归：
  - S1 entry==open / S4 T+1 / S5 费用(含最低佣金) / S6 权益 与手工模拟对账
  - S8 掩码真实触发（高波动股必须有至少 1 次拦单，证明掩码非空转）
仅研究层 QA，运行于 .venv_vbt，不改生产。
"""
import sys
sys.path.insert(0, r"D:\zcodesj")
import numpy as np
import vbt_ashare_adapter as A

STRESS = [("002693", "双成药业"), ("600579", "中化装备"), ("603221", "爱丽家居")]


def check(code, name):
    df = A.load_stock(code)
    close = df["close"]
    fast, slow = close.rolling(5).mean(), close.rolling(20).mean()
    up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    warm = close.rolling(20).count() < 20
    up, dn = up & ~warm, dn & ~warm

    pf, ents, exts, masks = A.run_ashare(df, up, dn, size=100, code=code)
    tr = pf.trades.records_arr
    closed = tr["status"] == 1
    open_n = df["open"].to_numpy()

    # S8: 掩码真实触发量 = 未加掩码的shift信号数 - 加掩码后的信号数（在涨跌停/停牌日被拦下的信号数）
    raw_ents = up.shift(1).fillna(False)
    raw_exts = dn.shift(1).fillna(False)
    blocked_buy = int((raw_ents & ~ents).sum())
    blocked_sell = int((raw_exts & ~exts).sum())

    s1 = float(np.max(np.abs(tr["entry_price"].astype(float) - open_n[tr["entry_idx"].astype(int)]))) if len(tr) else 0.0
    lu, ld, susp = masks["limit_up_open"].to_numpy(), masks["limit_dn_open"].to_numpy(), masks["suspended"].to_numpy()
    on_limit = int(lu[tr["entry_idx"].astype(int)].sum() + ld[tr["exit_idx"][closed].astype(int)].sum()) if len(tr) else 0
    on_susp = int(susp[tr["entry_idx"].astype(int)].sum() + susp[tr["exit_idx"][closed].astype(int)].sum()) if len(tr) else 0
    diff = tr["exit_idx"][closed].astype(int) - tr["entry_idx"][closed].astype(int)
    t1min = int(diff.min()) if closed.any() else None
    man_eq, man_fee, man_ep, man_xp = A.manual_sim_ashare(df, up, dn, size=100, code=code)
    vbt_eq = float(pf.value().iat[-1, 0])
    vbt_fee = float(tr["entry_fees"].astype(float).sum() + tr["exit_fees"].astype(float).sum())

    print(f"\n=== {code} {name} === 涨跌停开盘日: 涨停{int(lu.sum())}/跌停{int(ld.sum())}/停牌{int(susp.sum())}")
    print(f"  S8 掩码真实触发: 买入被拦 {blocked_buy} 次 / 卖出被拦 {blocked_sell} 次")
    print(f"  S1 entry==open 偏差 {s1:.2e} | 成交落在涨跌停日 {on_limit} | 停牌日 {on_susp} | T+1 最小间隔 {t1min}")
    print(f"  费用 vbt={vbt_fee:.4f} manual={man_fee:.4f} | 权益 vbt={vbt_eq:.2f} manual={man_eq:.2f}")

    ok = (s1 < 1e-4) and on_limit == 0 and on_susp == 0 and (t1min is None or t1min >= 1) \
        and abs(vbt_fee - man_fee) / max(man_fee, 1e-9) < 1e-4 \
        and abs(vbt_eq - man_eq) / max(man_eq, 1e-9) < 1e-4
    # S8 条件：该票必须有至少 1 次掩码触发（证明掩码在高波动股上真实工作，非空转）
    s8 = (blocked_buy + blocked_sell) > 0
    print(f"  S8 掩码非空转: {'PASS' if s8 else 'FAIL(掩码未触发)'}")
    return ok and s8


if __name__ == "__main__":
    allok = all(check(c, n) for c, n in STRESS)
    print("\n===== 压测汇总:", "ALL PASS" if allok else "有 FAIL", "=====")
    sys.exit(0 if allok else 1)
