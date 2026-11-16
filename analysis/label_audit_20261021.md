# B10 标签口径审计（label_audit_20261021）

## 结论（三行，按开工令要求）

1. **标签类型**：L1 固定窗口方向。`LABEL0 = Ref($close,-2)/Ref($close,-1) - 1`（次日收盘→后日收盘，qfq 前复权、next-bar 口径，**无止损路径模拟**）。
   位置：`strategy_libs/MASTER/tulong_build_data.py:59`（build_label 函数，qlib 表达式直读，非推测）。
2. **N = 1 个交易日**。
3. **是否脱钩**：**中度脱钩**。实际持有 251 次买卖配对（2026-07-22→10-21，attribution.db trade_records FIFO）：中位 2 个交易日、均值 4.14、p75=5、p90=10；**37% 持仓超 3 日**的路径在 N=1 标签中零表达；−8% 止损先触发的路径（例：10/21 晨止损级联 6 笔）在标签中完全无编码。

## 按开工令分流表的对位

属「标签是 L1」分支。补充本系统特有事实：**执行面从未存在概率阈值**（trade_records 611 笔 signal_type 全为 manual，`probability_threshold` 类型零订单），score 仅进 Top20 排序——「L1 概率仅排序不阈值」是对现状的确认而非新约束。B12 校准诊断已以替代形式完成（`analysis/score_calibration_probe_20261021_result.json`：时序切分 fit12→embargo2→eval7，样本外 Brier 不优于基准率，无增量信息）。**L2/L4 重训立项待主人批准**；管理员建议先做 forward_up_prob_gauge 组分类判别力检验（成本更低、对象更对），再决定是否立项重训。

## 证据明细

| 项 | 值 | 来源 |
|---|---|---|
| 标签构造 | `D.features(..., ["Ref($close,-2)/Ref($close,-1)-1"])` → 列名 LABEL0 | tulong_build_data.py:59-61 |
| 持有分布 | n=251：1日38.2% / 2-3日24.7% / 4-7日22.3% / 8-15日11.2% / 16日+3.6% | attribution.db FIFO×qfq日历 |
| 持有统计 | 中位2、均值4.14、p25=1、p75=5、p90=10（交易日） | 同上 |
| 止损模拟 | 标签无；系统真实止损链存在且实战触发（10/21 六笔级联） | trade_executor 卖出链 |
| 信号类型 | 611 笔全 manual；probability_threshold 零订单 | attribution.db GROUP BY signal_type |

## Manifest

| 输入 | sha256(前16位) |
|---|---|
| strategy_libs/MASTER/tulong_build_data.py | 03566045013c9075 |
| attribution/attribution.db | 8054e891311fd4c9 |
| analysis/score_calibration_probe_20261021_result.json | 27c1b884d53acbcd |

复现命令（D:\zcodesj）：

```bash
# 标签定义
sed -n '55,62p' strategy_libs/MASTER/tulong_build_data.py
# 持有周期(FIFO配对, 只读)
python -c "import sqlite3;con=sqlite3.connect('file:D:/zcodesj/attribution/attribution.db?mode=ro',uri=True);print(con.execute('SELECT COUNT(*) FROM trade_records').fetchone())"
# 信号类型分布
python -c "import sqlite3;con=sqlite3.connect('file:D:/zcodesj/attribution/attribution.db?mode=ro',uri=True);print(con.execute('SELECT signal_type,COUNT(*) FROM trade_records GROUP BY signal_type').fetchall())"
```

参数：FIFO 配对、交易日粒度（qfq 库日历映射）、无 status 过滤（表无该列，动作 BUY/SELL/PARTIAL_SELL）。
局限：trade_records 为 attribution 台账口径（开工令禁用 fills_clean.db，未用）；26 笔交易日历外记录跳过。
