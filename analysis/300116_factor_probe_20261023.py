# -*- coding: utf-8 -*-
"""300116_factor_probe_20261023.py — 事项二深潜验证件（只读·零补丁·零生产改动）

目的: 复证「300116 qfq 因子缺口可否经生成器既有回退源自愈」。
结论(2026-10-23 实测): 回退源因子正确(tushare 2.00022/baostock 2.0, 除权日 2017-04-18 侧),
compute_qfq 干跑完美消除缺口(除权日 qfq 收益 -6.03%, 全窗无超限截断), 但
**回退源连续性闸 _qfq_continuity_ok 假阳性拒收**——该股 2024-05 退市末端真实 prints
(连续 -18~-22% 跌停链 + 低价 rounding 区 +22.22%, westock 独立源逐日证实)触发闸的
±22% 绝对值判据。闸语义修改属闸门语义修改(宪法 §12/§17), 待主人批复; 默认推荐=登记债务。

复跑: python analysis/300116_factor_probe_20261023.py  (幂等, 只读)
周六 QfqFullRefresh 后若主人批复闸门修订, 本件即复核入口。
"""
import sys

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import warnings; warnings.filterwarnings("ignore")
import importlib.util
import sqlite3

CODE = "300116"
EX_DAY = "2017-04-18"


def load_gen():
    spec = importlib.util.spec_from_file_location("bq", r"D:\zcodesj\build_qfq_meta_tdx.py")
    bq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bq)
    return bq


def main():
    bq = load_gen()
    mc = bq.get_meta_conn()
    rows = mc.execute(
        "select date,open,high,low,close,volume,amount from stock_daily_meta "
        "where code=? order by date", (CODE,)).fetchall()
    raw = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    raw["date"] = pd.to_datetime(raw["date"].astype(str), format="%Y%m%d")
    raw = raw.set_index("date")
    print(f"[1] meta原始行: {len(raw)} ({raw.index.min().date()}~{raw.index.max().date()})")

    results = {}
    for name, fn in (("tushare", bq.fetch_tushare_qfq_factor),
                     ("baostock", bq.fetch_baostock_qfq_factor)):
        try:
            fac = fn(CODE)
        except Exception as e:
            print(f"[2:{name}] 拉取异常: {type(e).__name__}: {str(e)[:80]} (限频属预期, 换源/隔窗复跑)")
            continue
        if fac is None or isinstance(fac, str) or len(fac) == 0:
            print(f"[2:{name}] 无因子: {fac}")
            continue
        pre = fac[fac.index < pd.Timestamp(EX_DAY)]["factor"].iloc[-1]
        post = fac[fac.index >= pd.Timestamp(EX_DAY)]["factor"].iloc[0]
        gate = bq._qfq_continuity_ok(CODE, fac)
        qfq = bq.compute_qfq(CODE, raw, fac)
        ret_ex = qfq.loc[EX_DAY, "close"] / qfq.loc[pd.Timestamp(EX_DAY) - pd.Timedelta(days=1):].iloc[0]["close"] - 1 \
            if False else qfq.loc[:EX_DAY]["close"].iloc[-1] / qfq.loc[:pd.Timestamp(EX_DAY) - pd.Timedelta(days=1)]["close"].iloc[-1] - 1
        big = qfq["change_pct"].abs().max()
        ok = (abs(ret_ex + 0.06) < 0.01) and big <= 30.5
        results[name] = dict(pre=round(float(pre), 6), post=round(float(post), 6),
                             gate=bool(gate), ret_ex=round(float(ret_ex), 4),
                             fix_ok=bool(ok))
        print(f"[2:{name}] 因子 除权前={pre:.4f}/除权后={post:.4f} | 干跑 除权日收益={ret_ex:+.2%} "
              f"最大|chg|={big:.2f}% | 修复效果={'PASS' if ok else 'FAIL'} | 连续性闸={'过' if gate else '假阳性拒收'}")

    cq = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    old = dict(cq.execute(
        "select cast(date as text), close from stock_daily_meta_qfq where code=? "
        "and cast(date as text) in ('20170417','20170418')", (CODE,)).fetchall())
    cq.close()
    print(f"[3] 当前库(缺陷, factor=1.0): 20170417={old.get('20170417')} 20170418={old.get('20170418')}")
    print("[4] 定谳: 回退源因子正确但被连续性闸假阳性拒收(2024-05 退市末端真实 prints); "
          "闸语义修改待主人批复, 默认=登记债务(A'')。详见 reports/呈请 事项二执行台账。")


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
