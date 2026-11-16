# async双写幻影根因与修复提案 (20261027)

## 根因链条(全链实证)
1. westock订单真值(唯一真源) → 2. trade_records本地成交台账 → 3. closed_loop_sync全史FIFO配对 → 4. position_history
污染发生在第2环: 同一westock订单被**两个路径**写入trade_records:
- `async回报确认`路径: 以**提交日**(早于成交日0~3天)写入
- `盘后审计导入`路径: 以**真成交日**写入(与westock一致)
closed_loop_sync去重键=(code,date,action,price,vol) 含日期 → 双行键不同双双入队 → FIFO产出"幻影孪生行"(exit_date=提交日的假行 + exit_date=成交日的真行)
另有第2缺陷: 盘后审计导入器把**已撤单订单**误录为成交(601169 id310 8/24@5.09, 撤单单号33630710)

## 实证样本(oid级)
- 4笔卖出双写: 000001(9/1vs9/2), 600928(9/1vs9/2), 601229(9/1vs9/2), 000983(9/18vs9/21)
- 1笔撤单误录: 601169 8/24@5.09(撤单33630710) vs 真成交8/25@5.14
- 1笔同键买入双计: 601668 9/4 1100@4.40(async733+recon672)
- 触发条件实证: 2026-09-01/09-02/09-18~21 三个交易日的async路径在成交确认前先行写入

## 已完成的数据层修正(20261027, 可回滚)
- position_history: 删8条幻影/重复(id82/90/92/96/110/114/153/174), 修id83金额, 补录2笔真实缺失(600928-72/601229-144)
- trade_records: 删6条脏行(id310/505/506/507/733/734)
- 验收: closed_loop_sync --execute 重跑=新增0(幂等), 幻影零再生
- 备份: attribution.db.bak_hygiene{,2,3}_20261027; 证据: data/attribution_hygiene_log.jsonl + audit_probe{,2,3}_20261027.json

## 生产代码修复实施记录(20261027主人「继续」令批准, 三段式执行)
- C层已落地: attribution/closed_loop_sync.py load_fills尾部折叠防御(async/audit同键组内async让位canonical), 回归test_closed_loop_async_defense.py 4/4 PASS(双写折叠/新鲜async不误杀/async归属保留/同键同日脏对)
- B层已落地: naked_trade_audit.py status缺失由放行改fail-closed跳过(撤单误录根因封堵, 601169 id310同款)
- A层已落地: fill_confirm_monitor.py _record_fill新增fill_date覆盖参, cmd_poll经_fetch_today_fills匹配真成交日(失败降级旧语义不阻断)
- D层验收: 新回归4/4 PASS + 既有test_fill_confirm_regression 5 tests OK + closed_loop_sync重跑(新增5行经逐条westock真值核验=今日真实平仓: 农行+18/国新健康-1615/-2184(14:51第三方卖)/奥飞-585/+147, 防御零副作用) + py_compile三件OK
- 边界: async路径仍以executor_order_ledger台账行为源头, A层仅在回报确认时以真成交日覆盖; 盲洞段(8月上中旬/9月上旬)数据修复仍待更深接口
A. async回报确认路径: 仅在westock状态=已成交时写入, 且trade_date必须取westock filledAt(非提交日); 与盘后审计导入建立同键互斥(async行在audit孪生出现时自动退场)
B. 盘后审计导入器: 写入前查westock订单状态, 已撤单/未成交/系统撤单一律不写
C. closed_loop_sync防御层(纵深): 配对前折叠async/audit同键异日对(取audit日), 使上游脏行无法再产出幻影
D. 回归验收: tests新增构造用例(async早日期+audit真日期同单) → 修复后FIFO仅产出真行
