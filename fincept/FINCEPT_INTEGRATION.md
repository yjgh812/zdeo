# Fincept Terminal × 屠龙系统对接文档

> 建立日期：2026-08-16 ｜ 维护：知微
> 版本：fincept-terminal 2.0.8（PyPI Python 版）+ **v4.4.0（C++/Qt6 桌面版）**

## ⚡ v4.4.0 增补（2026-08-16 第二轮：节点编辑器/MCP/AI Quant Lab）

### 安装与门禁状态

| 项 | 状态 |
|---|---|
| 安装包 | `D:\zcodesj\fincept\FinceptTerminal-4.4.0-setup.exe`（SourceForge 镜像，md5 校验✓） |
| 安装位置 | `C:\Program Files\FinceptTerminal`（pywinauto 自动走完向导） |
| GUI | ✅ 已启动（桌面快捷方式已建），**停在登录页——免费账户需主人邮箱注册，知微不代办** |
| 首次设置向导 | 卡在"下载安装器"（其源走不通），已点"跳过并继续"——内嵌 Python runtime 未装，登录后可在设置中重试 |
| 数据目录 | `C:\Users\yujie\AppData\Local\com.fincept.terminal`（workspace.db/logs/files） |

### 三大功能实测结论

**1. AI Quant Lab = Qlib 14 模块 + RDAgent（scripts/ai_quant_lab/，纯 Python CLI）— ✅ 已激活**
- 协议：`python qlib_service.py <command> '<json_params>'`，stdout JSON
- 屠龙主环境实测：qlib 0.9.8 加载成功，16 模型可用（LGBM/XGB/CatBoost/LSTM/GRU/ALSTM/Transformer/TCN/AdaRNN/HIST/TabNet/SFM/GATs/ADD/DEnsemble），Alpha158/360+vwap 四 handler
- **屠龙数据直读验证✓**：`tulong_qlab.py` 把 provider 指到 `D:/workbuddyspace/qlib_data/qlib_bin`（SH600000 格式，日历至 2026-08-13），SH600519 近 7 日行情真实拉取成功
- 能力面：train_model/predict/run_backtest/因子分析/特征重要性/滚动再训练（qlib_rolling_retraining）/RL（qlib_rl：PPO/DQN/A2C/SAC/TD3）/在线学习/HFT/元学习
- ⚠️ 屠龙纪律提醒：RL/复杂模型按既有裁决（aurumq-rl 只抽不训；LGBM 争议关闭）仅研究用；因子数据必须走 factor_pit.db 口径，qlib 面板数据含 hsj 污染历史教训

**2. MCP（scripts/mcp/edgar/，EDGAR 美股 SEC CLI）— 🔴 已退役（主人裁决：不要美股）**
- 2026-08-16 主人明确"我们不要美股"，EDGAR 对接全线退役：`edgar_cli.py` 已删除、venv 内 edgartools 已卸载，禁复活
- Fincept 的"mcp"目录本就不是 Model Context Protocol，仅此一个 EDGAR CLI，无 A 股能力
- GUI 里 mcp_servers 面板（挂外部 MCP）与市场无关，登录后仍可验证（用于挂屠龙自己的 MCP）

**3. 节点编辑器（GUI node_editor 组件）— ⏸ 待登录**
- 纯 GUI 功能，脚本层无实体；组件目录 `resources/component_catalog.json`（46 组件）
- 需主人注册登录 + 内嵌 Python runtime 装好后方可使用

### v4.4.0 新增屠龙相关组件（component_catalog）
`akshare`（A股宏观数据面板）、`asia_markets`、`data_sources`/`data_mapping`、`alpha_arena`（AI 因子竞技场）——均需登录。
**范围裁决（2026-08-16 主人）：美股全线不要——quantlib 590 端点、EDGAR、美股行情组件全部排除，只保留 A 股/宏观/通用工具向对接。**

### 主人的两个待办
1. **注册免费账户**：GUI 登录页 Create an account（或 Google 登录）→ 登录后节点编辑器/mcp_servers/AI Quant Lab GUI 面板全开
2. 登录后在设置里重跑环境安装（内嵌 Python 下载）；若仍卡网络，AI Quant Lab/EDGAR 的 CLI 路线不受影响（已绕开该依赖）

---

## 一、2.0.8 安装信息（第一轮）

| 项 | 值 |
|---|---|
| 安装方式 | 独立 venv（`D:\zcodesj\fincept\venv`），与主环境完全隔离 |
| 隔离原因 | 依赖树会降级主环境的 pandas(2.3.3→2.2.3)/scipy/requests，直接装必破坏屠龙量化栈 |
| 已清理 | 垃圾包 asyncio 3.4.3（遮蔽标准库，已删） |
| 本地数据 | `~/.fincept/`（credentials/cache/logs/watchlist/watchlist.db） |

**绝不 `pip install` 到全局环境。所有操作走 `D:/zcodesj/fincept/venv/Scripts/python.exe`。**

## 二、实测结论（2026-08-16，全部实跑验证）

| 项目 | 状态 | 证据 |
|---|---|---|
| GUI 启动（DearPyGui） | ✅ | splash 正常渲染，日志无错 |
| 默认云端端点 zrok | ❌ 502 | `finceptbackend.share.zrok.io` 隧道已死 |
| 备用端点 api.fincept.in | ✅ 200 | FastAPI 健康，database: true，780 路由 |
| Guest 免登录通道 | ❌ 已下线 | `/device/register`、`/guest/status` 均 404 |
| 进入主界面 | ⚠️ 离线模式 | 启动器已关 strict，登录页点 **Continue as Guest** 即进 |
| 云端 A 股数据 | ❌ 无覆盖 | exchange 仅 NSE/BSE/SGX/LSE/美股；china 路由 0 条 |
| yfinance 行情（GUI内置） | ❌ 本网络不可达 | Yahoo 连接失败/限流（curl HTTP 000） |
| quantlib 590 基本面端点 | ⏸ 需注册 | APIKeyHeader 认证，注册需主人邮箱（知微不代办） |

**定位裁决：Fincept 在屠龙体系中的角色 = 本地持仓/自选可视化面板（数据由屠龙反哺），不是数据源、不是回测引擎。**

## 三、使用方法

### 1. 启动终端

```
D:/zcodesj/fincept/venv/Scripts/python.exe D:/zcodesj/fincept/start_fincept.py
```

登录页点 **Continue as Guest**（服务端 guest 路由虽已下线，离线 fallback 会直接放行进主界面）。

### 2. 持仓/自选同步（屠龙 → Fincept）

```bash
# 第一步：从 MCP 实时接口取持仓（铁律：不读本地缓存），存成 JSON
# 格式: [{"code":"600519","name":"贵州茅台","quantity":100,"avg_price":1500.0}, ...]
# quantity=0 表示纯自选观察

# 第二步：写入 Fincept
D:/zcodesj/fincept/venv/Scripts/python.exe D:/zcodesj/fincept/tulong_fincept_bridge.py --file positions.json
```

ticker 自动转换 yfinance 格式（6xx→.SS，0/2/3xx→.SZ，4/8/92xx→.BJ）。

### 3. 行情快照反哺（关键：Yahoo 不可达，价格必须由屠龙喂）

```bash
# 从腾讯/新浪实时接口拉价后写快照（price=现价, chg_pct=当日涨跌幅百分数）
# 格式: [{"code":"600519","price":1488.6,"chg_pct":-0.76}, ...]
D:/zcodesj/fincept/venv/Scripts/python.exe D:/zcodesj/fincept/tulong_fincept_bridge.py --quotes quotes.json
```

桥自动计算 total_value / unrealized_pnl。GUI 打开 WatchList Tab 即见。
**频率建议：与盯盘节奏一致（如 14:30 持仓检查后喂一次），不必实时。**

### 4. 清空

```bash
D:/zcodesj/fincept/venv/Scripts/python.exe D:/zcodesj/fincept/tulong_fincept_bridge.py --clear
```

## 四、验证记录

- 2026-08-16 桥写入实测：3 条 demo（600519.SS/300750.SZ/000001.SZ）✓
- 2026-08-16 行情反哺实测：2 条，浮盈/市值计算核对无误（茅台 -1140 = (1488.6-1500)×100）✓
- 2026-08-16 GUI 启动器实测：splash 无错渲染 ✓

## 五、已知限制与风险

1. **行情列不自动刷新**（Yahoo 不可达），GUI 显示的是屠龙最后一次喂入的价格，表内 `last_updated` 可查新鲜度。
2. watchlist 表无 name 列，股票名称不显示——结合代码+名称铁律，喂入 JSON 仍保留 name 字段以便审计，但 GUI 只显 ticker。
3. v2.0.8 是旧线版本（新版 v4.x 为 C++/Qt 重写、AGPL），上游 PyPI 包可能停更（最后发布 2025-09）。
4. 若未来注册 Fincept 账号（api.fincept.in 免费注册），quantlib 590 个基本面端点可用（美股研究向），届时在 GUI 登录即可，启动器不受影响。
5. duckdb 写入时 GUI 需未占用该库（DuckDB 单写者）；喂行情前确认 GUI 处于打开状态下的表会由 GUI 持连接——若写失败，先关 GUI 再写。

## 六、文件清单

```
D:\zcodesj\fincept\
├── venv\                      # 隔离环境（勿动；含 edgartools）
├── start_fincept.py           # 2.0.8 一键启动器（API端点+离线模式）
├── tulong_fincept_bridge.py   # 2.0.8 watchlist 桥（持仓/自选/行情快照）
├── tulong_qlab.py             # ★ AI Quant Lab 屠龙包装器（qlib_service→屠龙数据）
├── FinceptTerminal-4.4.0-setup.exe  # v4.4.0 安装包（md5✓）
├── demo_positions.json / demo_quotes.json
├── openapi.json               # api.fincept.in 路由清单快照（780条）
├── check_gui.ps1              # GUI 进程检查脚本
└── FINCEPT_INTEGRATION.md     # 本文档
```
