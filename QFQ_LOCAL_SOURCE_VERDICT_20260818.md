# 历史 K 线数据源正式切换为本地 qfq 库 · 数据源裁决

**裁决**：主人 2026-08-18 授权——**历史日 K 线数据源正式切换为本地 qfq 库**
（`D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db`），作为脚本化历史
取数的唯一首选源；网络 K 线接口降级为兜底。

## 一、切换原因（事实，2026-08-18 实测）

1. **腾讯 WAF 拦截高频 K 线**：`web.ifzq.gtimg.cn` fqkline 接口在连续数百次请求后返回
   501 WAF 页（`waf.tencent.com/501page.html`），requests/urllib 均触发，无法批量取数。
2. **新浪接口同源限流**：`money.finance.sina.com.cn` getKLineData 在并发 6×1800+ 请求后
   开始返回空/非 JSON（JSONDecodeError），仅适合低频手工取数。
3. **本地 qfq 库质量达标**：`dragon_quant_qfq.db → stock_daily_meta_qfq`：
   - 6011 只标的（沪深主板+创业板+科创+北交，qfq 复权口径）
   - 日期覆盖 19901219 ~ 20260817（8756 个交易日）
   - 字段：code/date/open/high/low/close/volume/amount/change_pct
   - **零网络、并发安全、单次读取 <10ms**——实测 15 交易日 × ~90 只/日全量形态扫描
     （breakout_hitrate_baseline）仅需数十秒，而网络方案被限流卡死 8 分钟+。
4. **数据新鲜度同步机制已存在**：`daily_chain_update.py` 每日 16:30 后更新该库
   （记忆：qfq库=组合回测唯一价格源，每日链刷新），当日收盘数据可于盘后取用。

## 二、裁决清单（新取数优先级）

| 用途 | 首选源 | 兜底 | 状态 |
|:--|:--|:--|:--|
| 历史日 K（批量/脚本化/回测基线） | **本地 qfq 库** | 新浪（低频） | ✅ 正式切换 |
| 实时/最新快照 | 腾讯 qt.gtimg.cn / 东财 push2delay | 新浪 | 维持不变 |
| 分钟 K | 本地 vipdoc / mootdx（记忆：tdx-local） | 腾讯 | 维持不变 |
| 涨停/跌停/炸板池 | 东财 push2ex（历史仅覆盖近 15 交易日） | 本地 zt_pool 缓存 | 维持（需注意覆盖边界） |

## 三、已切换的消费方

- `attribution/breakout_hitrate_baseline.py`（2026-08-18 线索③）——已改为本地 qfq 库读取，
  实测 15 交易日基线生成成功（均值 19.2%）。
- 后续脚本化历史取数（因子/形态/回测）一律以本地 qfq 库为先，**禁止脚本直接高频打
  腾讯/新浪 K 线接口**（触发限流既慢又不稳）。

## 四、边界与约束

1. **当日新鲜度**：盘前（<16:30）读到的 qfq 库最新交易日=上一交易日；需要当日收盘 K 线
   必须等 daily_chain_update 完成（16:30+）或直接走实时接口。
2. **历史覆盖边界**：东财 push2ex 涨停池历史仅覆盖近 15 个交易日（更早返回 None），
   20 日滚动基线如实标注有效样本数，不虚报。
3. **复权口径**：qfq 库为前复权，组合回测/形态计算均基于复权价，与网络不复权 K 线
   存在口径差异，比较时需显式标注。

*——屠龙系统·知微 2026-08-18*
