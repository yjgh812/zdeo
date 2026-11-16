# 概率数字决策点全扫（prob_usage_scan_20261021）

## 结论（先读这段）

**全链不存在任何「概率 > X 才买」的数值阈值决策。** 系统里真实存在的"概率"只有两族：(a) ML 引擎 MASTER/HIST 的 **score**——只进 Top20 排序，判别力未证实（样本外 Brier 不优于基准率，见 label_audit_20261021 关联探针）；(b) **forward_up_prob_gauge 条件概率统计**——观测层本体零交易权，唯一闸门消费点是 R38 期望闸，且是**符号级**（负期望×弱市档位→REJECT），不是校准概率数字，缺证据时 fail-open。执行层枚举里的 `probability_threshold` 信号类型 611 笔成交中**零使用**。若未来启用 B14 概率阈值，将是本系统第一个数值阈值决策，须主人书面批准（开工令第四条硬约束已覆盖）。

## 用法清单（文件、行号、用法、是否阈值）

| # | 文件:行号 | 用法 | 排序/阈值 | 判定 |
|---|---|---|---|---|
| 1 | daily_signal_production_v4.py:216-293 | MASTER/HIST 推理 score → `full_score_*.csv` 落盘 + Top20 `signal_*.csv` | 排序 | 仅排序。宇宙=沪深300剔科创(~280只/日)。score 为回归式分数非概率 |
| 2 | forward_up_prob_gauge.py:1-60 | 五画像组×250日滚动窗条件概率：T+1胜率/超额/T+5/T20E(含止损模拟)；MIN_CELL_N=300 | 观测层 | 本体零交易权；带样本量纪律 |
| 3 | **pre_trade_check_v2.py:548-671（R38 期望闸）** | gauge 组级 `t20e_med_pct`：<0 且 LOW_VOL 弱档(新鲜) → **REJECT**（`--owner-approve-r38` 特批降 WARN）；非弱档/过期 → WARN；≥0 → PASS；缓存缺失/样本不足/特征日过期 → **fail-open 标注** | **阈值性消费（符号级）** | 全链唯一闸门消费点。消费的是期望符号×市场档位，非概率数值；有预注册依据（10/8 十二只同型零/负期望照买、当日均值 -1.31%） |
| 4 | trade_executor.py:69,75-76 | signal_type CHECK 约束枚举含 `probability_threshold`；`_norm_signal_type` 未匹配源一律归 manual | 枚举预留 | **611 笔全 manual，从未启用**（attribution.db 实证） |
| 5 | attribution.db trade_records 列 pred_proba/pred_class/expected_return_5d/expected_return_20d | schema 预留列 | — | INSERT 语句（trade_executor.py:784,1264）不含这些列 → 死列，无写入方 |
| 6 | 全链 grep `if prob >` / `> 0.6` 类数值比较 | 无命中 | — | 数值阈值决策不存在 |

## 与 R38 相处的既定纪律（现役先例，供 B14 未来参照）

R38 是本系统"概率派生量进闸门"的唯一现役形态，其三条既有纪律即事实上的预注册模板：①消费符号不消费数值（避免未校准概率冒充数值）；②证据缺失 fail-open 不硬拦（研究仪表缺证据不硬拦）；③特批须显式旗且 WARN 留痕。B14 若立项，建议沿用同构姿态并加第四条：概率数值须先过校准验收（ECE/Brier，见 label_audit 关联探针的否定性结论——当前 score 不满足）。

## T1–T4 分类对齐（按开工令 4.2 分类规则补记，20261021）

| 命中位置 | T1 阈值决策 | T2 排序辅助 | T3 权重调制 | T4 仅记录 |
|---|---|---|---|---|
| daily_signal_production_v4（score→Top20） | — | ✅ T2 | — | — |
| forward_up_prob_gauge（仪表本体） | — | — | — | ✅ T4 |
| pre_trade_check_v2 R38 期望闸 | 特例：符号级阈值（消费期望符号×档位，不消费概率数值） | — | — | — |
| trade_executor signal_type 枚举 + pred_proba 死列 | — | — | — | ✅ T4（未启用） |
| 全链 `if prob > 0.x` 数值阈值扫描 | **T1 = 0 处** | — | — | — |

**结论段（DoD 要求）**：当前 T1 阈值用法 **0 处**，无需"校准前视为未验证"标注；T2 共 1 处（Top20 排序，不受校准影响，无需处置）；T3 共 0 处；T4 共 2 处；另 R38 符号级特例 1 处（已有 fail-open + 预注册 + 特批旗三重纪律，维持现状）。

## Manifest

| 输入 | sha256(前16位) |
|---|---|
| pre_trade_check_v2.py（R38 段实读 646-671） | 见 git HEAD 版本 |
| forward_up_prob_gauge_20261020.json | fd7d659d784c8376 |
| attribution/attribution.db | 8054e891311fd4c9 |
| position_gate_rejects.jsonl | fe1b5633401e6351 |

复现命令（D:\zcodesj）：

```bash
# 数值阈值扫描
grep -rn "prob\|predict\|threshold" pre_trade_check_v2.py trade_executor.py daily_signal_production_v4.py forward_up_prob_gauge.py | grep -v "^\s*#"
# R38 期望闸原文
sed -n '646,671p' pre_trade_check_v2.py
# 信号类型实证
python -c "import sqlite3;con=sqlite3.connect('file:D:/zcodesj/attribution/attribution.db?mode=ro',uri=True);print(con.execute('SELECT signal_type,COUNT(*) FROM trade_records GROUP BY signal_type').fetchall())"
```

局限：扫描覆盖信号生产/闸门/执行/仪表四层主链；analysis/ 下一次性研究脚本（探针/回测）含大量概率计算但均无交易权，未逐个列入——如需全量清点可扩展。
