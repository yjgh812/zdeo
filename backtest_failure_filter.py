# -*- coding: utf-8 -*-
"""
backtest_failure_filter.py — 选股侧回测大亏失败形态匹配模块（L3优化方案落地）

来源：reports/TULONG_MULTI_MODEL_BACKTEST_2016_2026.md 第3.2节 + attribution/experience_library BT-FAIL-001~004
两类量化失败模式（2016-2026回测实证，非主观判断）：

  1. value_trap（价值陷阱）：mom20≤0.5% + amplitude20≤8% + 银行/保险/券商行业
     → bp_ep低动量死水股，实测51笔银行股大亏，均亏-13.7%，民生银行21次反复选中
     行业限定的依据：BT-FAIL-001实证大亏全部集中在银行板块，茅台等低波动消费股不适用
  2. rebound_chase（反弹追高）：mom20<0 + mom5>0 + mom5>|mom20|*0.5 → ml下跌趋势中追反弹
     实测：13笔集中大亏(2024-01)，均亏-14.1%，5天即止损(98%)

性质：提示性参考（与 verified_risk_flags 一致），不构成硬性排除规则。
铁律：硬规则须主人明示，本模块仅打标并返回 risk_score 供选股层参考。

用法:
    from backtest_failure_filter import compute_failure_flags
    flags = compute_failure_flags("600016")  # 最近交易日
    flags = compute_failure_flags("600016", days_back=1)  # 指定往前第N个交易日
"""
import sqlite3
import pandas as pd

# QFQ库路径：优先根目录，回退backups（与verified_risk_flags保持一致的读取模式）
import os

_QFQ_CANDIDATES = [
    # 2026-08-29 优化专项修复: 根目录qfq/fundamental从来不是真库位置(daily_chain_update.py:43-46为真源),
    # 旧候选链曾静默落到backups 8/23陈旧快照(缺8/24后数据); 2026-10-14 全面合并定谳=单一真源wb,
    # 冻结候选已移除(防静默读陈旧, 见 check_db_freshness 冻结引用巡检)。
    r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db",
]

# 行业映射库（fundamental_data.db 的 industry_map_ths 表）
_INDUSTRY_DB_CANDIDATES = [
    # 2026-08-29 优化专项修复: 根目录qfq/fundamental从来不是真库位置(daily_chain_update.py:43-46为真源),
    # 旧候选链会静默落到backups 8/23陈旧快照(缺8/24后数据); 真库提为首位, backups保留兜底。
    r"D:/workbuddyspace/databases/fundamental_data.db",
    r"D:/zcodesj/backups/fundamental_data/weekly/fundamental_data_20260823.db",
]

# 价值陷阱适用行业（BT-FAIL-001实证：大亏全部集中在银行/非银金融板块）
_VALUE_TRAP_SECTORS = ("银行", "非银金融", "保险", "证券")


def _resolve_qfq_path() -> str:
    for p in _QFQ_CANDIDATES:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return _QFQ_CANDIDATES[0]


def _resolve_industry_db_path() -> str:
    for p in _INDUSTRY_DB_CANDIDATES:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return _INDUSTRY_DB_CANDIDATES[0]


QFQ_DB = _resolve_qfq_path()
INDUSTRY_DB = _resolve_industry_db_path()


def _get_industry(code6: str, db_path: str | None = None) -> str | None:
    """查个股行业分类（申万/同花顺口径，industry_map_ths表）。"""
    path = db_path or INDUSTRY_DB
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT industry FROM industry_map_ths WHERE code=?", (code6,)
        )
        row = cur.fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _is_value_trap_sector(industry: str | None) -> bool:
    """判断行业是否属于价值陷阱适用范围（银行/非银金融）。"""
    if not industry:
        return False
    return any(sec in industry for sec in _VALUE_TRAP_SECTORS)


def compute_failure_flags(code6: str, days_back: int = 0, qfq_path: str | None = None) -> dict:
    """计算个股最近交易日的回测大亏失败形态特征。

    参数:
        code6: 6位股票代码
        days_back: 0=最新交易日, 1=前一交易日…
        qfq_path: 自定义QFQ库路径

    返回:
        dict: {
            date, value_trap, rebound_chase,
            detail: {vol20, mom20, mom5, amplitude20, ...},
            risk_score: 0-3 (命中特征数),
            error
        }
        数据不足/出错时 flags 全 False + error 字段说明（优雅降级，不抛异常）。
    """
    path = qfq_path or QFQ_DB
    out = {
        "date": None,
        "value_trap": False,
        "rebound_chase": False,
        "detail": {},
        "risk_score": 0,
        "error": None,
    }

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume, amount, change_pct "
            "FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 90",
            con, params=(code6,),
        )
        con.close()
    except Exception as e:
        out["error"] = f"qfq读取失败: {type(e).__name__} {e}"
        return out

    if df is None or len(df) < 25:
        out["error"] = f"数据不足({0 if df is None else len(df)}行<25)"
        return out

    df = df.iloc[::-1].reset_index(drop=True)  # 时间正序
    df["date"] = pd.to_numeric(df["date"], errors="coerce")
    close = df["close"].astype(float)

    idx = len(df) - 1 - days_back
    if idx < 20:
        out["error"] = f"回看{days_back}日后样本不足(idx={idx}<20)"
        return out

    r = df.iloc[idx]
    out["date"] = int(r["date"]) if pd.notna(r["date"]) else None

    # 20日窗口特征
    window20 = close.iloc[max(0, idx - 19): idx + 1]
    window5 = close.iloc[max(0, idx - 4): idx + 1]

    if len(window20) < 20 or len(window5) < 5:
        out["error"] = "窗口数据不足"
        return out

    # 20日波动率（日收益率标准差）
    rets20 = window20.pct_change().dropna()
    vol20 = rets20.std() * 100 if len(rets20) > 1 else 0.0

    # 20日动量（涨幅）
    mom20 = (window20.iloc[-1] / window20.iloc[0] - 1) * 100

    # 5日动量（涨幅）
    mom5 = (window5.iloc[-1] / window5.iloc[0] - 1) * 100

    # 20日振幅
    high20 = df["high"].astype(float).iloc[max(0, idx - 19): idx + 1]
    low20 = df["low"].astype(float).iloc[max(0, idx - 19): idx + 1]
    amplitude20 = (high20.max() / low20.min() - 1) * 100 if low20.min() > 0 else 0.0

    # 查行业分类（用于value_trap行业限定）
    industry = _get_industry(code6)
    is_financial_sector = _is_value_trap_sector(industry)

    out["detail"] = {
        "vol20": round(vol20, 2),
        "mom20": round(mom20, 2),
        "mom5": round(mom5, 2),
        "amplitude20": round(amplitude20, 2),
        "close": round(float(r["close"]), 2),
        "industry": industry,
    }

    # ① 价值陷阱：低动量 + 低振幅 + 银行/非银金融行业（bp_ep银行股大亏模式 BT-FAIL-001）
    # 行业限定依据：BT-FAIL-001实测51笔大亏全部集中在银行板块，茅台等低波动消费股不适用此规则
    # 阈值来源：实测大亏均值(mom20=0.08%, amplitude=7.41%) vs 盈利单(mom20=1.03%, amplitude=11.90%)
    out["value_trap"] = bool(
        is_financial_sector and mom20 <= 0.5 and amplitude20 <= 8.0
    )

    # ② 反弹追高：20日下跌但5日反弹（ml大亏模式 BT-FAIL-002）
    # 阈值来源：实测大亏均值(mom20=-2.47%, mom5=+2.10%)，反弹幅度相对20日跌幅显著
    out["rebound_chase"] = bool(mom20 < 0 and mom5 > 0 and mom5 > abs(mom20) * 0.5)

    out["risk_score"] = sum([out["value_trap"], out["rebound_chase"]])
    return out


FAILURE_HINT_TEXT = {
    "value_trap": (
        "⚠️价值陷阱(BT-FAIL-001): mom20≤0.5%+振幅≤8% → bp_ep低动量死水股模式，"
        "实测51笔银行股大亏均亏-13.7%，民生银行21次反复选中"
    ),
    "rebound_chase": (
        "⚠️反弹追高(BT-FAIL-002): mom20<0但mom5>0 → 下跌趋势中追5日反弹，"
        "实测13笔集中大亏(2024-01)均亏-14.1%，5天即止损(98%)"
    ),
}


def flags_to_hints(flags: dict) -> list:
    """把 compute_failure_flags 结果转为提示文案列表（与 verified_risk_flags 接口一致）。"""
    hints = []
    for k, txt in FAILURE_HINT_TEXT.items():
        if flags.get(k):
            hints.append(txt)
    return hints


if __name__ == "__main__":
    # 测试：民生银行(价值陷阱预期) + 九洲集团(反弹追高预期) + 茅台(不应误标)
    test_cases = [
        ("600016", "民生银行", "价值陷阱预期(银行股)"),
        ("601818", "光大银行", "价值陷阱预期(银行股)"),
        ("300040", "九洲集团", "反弹追高预期(2024大亏)"),
        ("600519", "贵州茅台", "不应误标(消费低波动但非银行)"),
    ]
    print(f"QFQ库: {QFQ_DB}")
    print(f"行业库: {INDUSTRY_DB}")
    print()
    for code, name, expectation in test_cases:
        flags = compute_failure_flags(code)
        print(f"=== {code} {name} ({expectation}) ===")
        if flags["error"]:
            print(f"  ❌ {flags['error']}")
        else:
            print(f"  date={flags['date']}")
            print(f"  detail: {flags['detail']}")
            print(f"  value_trap={flags['value_trap']}  rebound_chase={flags['rebound_chase']}  risk_score={flags['risk_score']}")
            hints = flags_to_hints(flags)
            if hints:
                for h in hints:
                    print(f"  {h}")
            else:
                print("  ✅ 无失败形态命中")
        print()
