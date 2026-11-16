# -*- coding: utf-8 -*-
"""
P1判别树fork生成器（2026-10-19「继续」令，只读影子，零生产改动）
从正本 bt_full_report_dose_20261018.py 文本fork出判别树专用脚本，插入L层开关：
  - TULONG_LADDER_SIGNAL=raw  → bp_ep信号跳过close<=30过滤+L2情绪置1.0+L3不降权（L0名单）
  - TULONG_BPEP_STOP          → 覆盖stop_loss_pct（L0b/L1用1.0禁用逐票止损）
  - TULONG_BPEP_DDLIMIT       → 覆盖max_drawdown_limit（L0b/L1用100禁用组合熔断）
  - TULONG_BPEP_MAX_HOLDING   → 已有旋钮（L0b/L1/L2用20固定持有）
正本零改动；fork文件独立可审计。
"""
import re

SRC = "D:/zcodesj/analysis/bt_full_report_dose_20261018.py"
DST = "D:/zcodesj/analysis/bt_full_report_ladder_20261019.py"

src = open(SRC, encoding="utf-8").read()

# ---- 改动1: close<=30 过滤加 LADDER 开关 ----
old1 = '''    df = df[df["close"] <= 30].copy()
    if len(df) < 20:
        return {}, {}

    # M-2 解禁排雷研究开关(默认关)'''
new1 = '''    _LADDER_RAW = os.environ.get("TULONG_LADDER_SIGNAL", "").strip() == "raw"
    if not _LADDER_RAW:
        df = df[df["close"] <= 30].copy()
    if len(df) < 20:
        return {}, {}

    # M-2 解禁排雷研究开关(默认关)'''
assert src.count(old1) == 1, f"锚1命中{src.count(old1)}次"
src = src.replace(old1, new1)

# ---- 改动2: sentiment_mult raw置1 ----
old2 = "    sentiment_mult = loader.get_sentiment_multiplier(date_str)\n"
assert src.count(old2) >= 1, "锚2未命中"
# 只改 bp_ep_signal 内的那一处（后文紧跟 M1优化注释）
old2b = '''    sentiment_mult = loader.get_sentiment_multiplier(date_str)

    # M1优化: bp_ep侧去掉p2确认'''
new2b = '''    sentiment_mult = loader.get_sentiment_multiplier(date_str)
    if _LADDER_RAW:
        sentiment_mult = 1.0  # L0名单: L2情绪置中性

    # M1优化: bp_ep侧去掉p2确认'''
assert src.count(old2b) == 1, f"锚2b命中{src.count(old2b)}次"
src = src.replace(old2b, new2b)

# ---- 改动3: L3降权 raw跳过 ----
old3 = '''    for idx in top20.index:
        mult = 1.0
        if idx in vt_set or idx in rc_set:'''
new3 = '''    for idx in top20.index:
        mult = 1.0
        if (not _LADDER_RAW) and (idx in vt_set or idx in rc_set):'''
assert src.count(old3) == 1, f"锚3命中{src.count(old3)}次"
src = src.replace(old3, new3)

# ---- 改动4: bp_ep STRATEGIES 参数覆盖加环境旋钮 ----
old4 = '''        ("bp+ep_core", bp_ep_signal, "monthly", {"max_holding_bars": int(os.environ.get("TULONG_BPEP_MAX_HOLDING", "0") or "0")}),'''
new4 = '''        ("bp+ep_core", bp_ep_signal, "monthly", {"max_holding_bars": int(os.environ.get("TULONG_BPEP_MAX_HOLDING", "0") or "0"), "stop_loss_pct": float(os.environ.get("TULONG_BPEP_STOP", "0.08") or "0.08"), "max_drawdown_limit": float(os.environ.get("TULONG_BPEP_DDLIMIT", "10.0") or "10.0")}),  # LADDER判别树fork: STOP/DDLIMIT环境旋钮(默认0.08/10.0=生产口径零变化)'''
assert src.count(old4) == 1, f"锚4命中{src.count(old4)}次"
src = src.replace(old4, new4)

# ---- 改动5: 输出目录默认名加 ladder 标记 ----
old5 = '''    os.path.join(r"D:\\zcodesj\\reports", "backtest_full_" + datetime.now().strftime("%Y%m%d")))'''
new5 = '''    os.path.join(r"D:\\zcodesj\\reports", os.environ.get("TULONG_LADDER_TAG", "ladder") and "backtest_full_" + datetime.now().strftime("%Y%m%d") + "_ladder_" + os.environ.get("TULONG_LADDER_TAG", "ladder")))'''
if src.count(old5) == 1:
    src = src.replace(old5, new5)
else:
    # 兜底: 用环境变量 TULONG_BT_OUT 指定输出目录即可, 不改默认值
    pass

open(DST, "w", encoding="utf-8").write(src)

# 语法自检
import py_compile
py_compile.compile(DST, doraise=True)
print("FORK_OK", DST)
# 打印各锚点行号供审计
for i, line in enumerate(src.split("\n"), 1):
    if "_LADDER_RAW" in line or "TULONG_BPEP_STOP" in line:
        print(i, line.strip()[:120])
