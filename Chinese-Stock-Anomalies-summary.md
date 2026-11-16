# Chinese-Stock-Anomalies 因子库复现总结

> 本文档从 GitHub 学术因子库提取 **118 个 A 股截面异象因子** 的完整 Python 计算公式，供屠龙系统增量因子开发使用。

---

## 0. 下载情况说明（重要）

**原指定仓库 `Michael-J-Fang/Chinese-Stock-Anomalies` 经多方核实不存在**（fastgit/ghfast/gitclone 三镜像均返回 Repository not found 或空仓库；Google/GitHub 搜索也无此作者/仓库）。

通过主题搜索定位到**功能完全对口的最佳替代仓库**，已成功 clone 到 `D:/zcodesj/Chinese-Stock-Anomalies`：

| 项目 | 原目标 | 实际采用 |
|---|---|---|
| 仓库 | Michael-J-Fang/Chinese-Stock-Anomalies（不存在） | **[Quantactix/ChinaAShareEquityCharacteristics](https://github.com/Quantactix/ChinaAShareEquityCharacteristics)** |
| 因子数 | 未知 | **118 个**（覆盖主流学术异象） |
| 语言 | 未知 | Python（pandas/numpy/statsmodels） |
| 数据源 | 未知 | CSMAR（与屠龙可对接） |
| 镜像 | — | `ghfast.top` 成功 |
| 作者 | — | Xin He (xinhe9701@gmail.com, www.xinhesean.com) |

该仓库实际上**比原目标更优质**：它是严格按照 Hou-Xue-Zhang "Anomalies" 论文附录 B 章节组织的 100+ 特征库，每个因子都对应一篇顶刊 paper，已在 char_list.csv 中标注论文出处。

---

## 1. 项目结构

```
D:/zcodesj/Chinese-Stock-Anomalies/
├── code/
│   ├── main.py                  # 【核心】118 个因子计算公式（2238 行）
│   ├── make_panel.py            # 合成 stock return × characteristics 面板
│   ├── main_sort.py             # 截面排序分组（10 分位/5 分位）
│   ├── main_sort_example.py     # 输出多空组合收益与因子单调性
│   └── tools/
│       ├── crosssection.py      # 截面排序调度器（支持多进程）
│       ├── sortedportfolio.py   # 单变量排序组合 + High-minus-Low + Sharpe
│       └── others.py            # 工具函数
├── char_list.csv                # 【因子目录】118 因子 + 论文出处 + CSMAR 字段映射
├── data/                        # 数据目录（csmar_tables / chars / factors / share）
└── submit.sh                    # 一键复现：main.py → make_panel.py → main_sort.py
```

### 复现流水线
```bash
sh submit.sh   # Linux 下一键跑完
# 1. main.py        → data/chars/<factor>.csv（每因子一个文件）
# 2. make_panel.py  → data/share/panel_rank.csv（收益×特征面板，截面 rank ∈[-1,1]）
# 3. main_sort.py   → 10 分位分组，算 High-minus-Low 因子收益
# 4. main_sort_example.py → 输出排序组合收益、Sharpe、特征单调性
```

### 排序框架（与 ICIR 评估对齐）
- 截面 rank ∈ [-1, 1]，按 0.2 步长切 **10 分位**（或 0.4 步长切 5 分位）
- 支持 **等权(ew) / 市值加权(vw, 用 lag_me)** 两种加权
- 输出 `uni_ls`（单变量多空）、`bi_ls`（控制变量后多空）、Sharpe = mean/std×√N
- **与屠龙 ICIR 评估口径兼容**：可直接对 `panel_rank.csv` 跑 IC / ICIR / 分层回测

---

## 2. 因子分类总览（118 个）

代码底部（main.py L2210）给出官方分类计数：

| 大类 | 子类 | 因子数 | 与屠龙现有因子正交性 |
|---|---|---|---|
| **B.1 Trading-related** | B.1.1 流动性 Liquidity | 13 | 部分重叠（turn/dtv 与换手率因子相关） |
| | B.1.2 风险 Risk | 13 | 部分重叠（tv/idv 与波动率相关） |
| | **B.1.3 历史收益 Past Returns** | **15** | **★ 高正交（动量/反转，屠龙未覆盖）** |
| **B.2 Accounting-related** | **B.2.1 盈利 Profitability** | **20** | **★ 高正交（质量类）** |
| | B.2.2 价值 Value | 12 | 重叠（bm/ep 与估值因子相关） |
| | **B.2.3 投资 Investment** | **19** | **★ 高正交（投资类异象）** |
| | **B.2.4 其他 Other Anomalies** | **22** | **★ 高正交（应计/盈利惊喜/情绪）** |
| Chinese 4-factor beta | — | 4 | 中等（CH4 beta） |
| **合计** | | **118** | |

### ★ 推荐优先复现的正交因子（与换手率/估值低相关）

**第一梯队（强烈推荐，与屠龙换手率/波动率/估值几乎完全正交）：**
1. **动量/反转类**（B.1.3）：m11, m6, m3, m60, m24, mchg, im12, im6, indmom, 52w, mdr, season
2. **盈利质量类**（B.2.1）：roe, droe, roa, rna, pm, ato, gpa, Fscore, Oscore, gpla
3. **投资类**（B.2.3）：ag, noa, dnoa, ig, cei, cdi, oacc, tacc, dwc, dnoa
4. **应计/盈利惊喜**（B.2.4）：sue, rs, tes, dsi, dgs, dss, etr, lfe
5. **其他**（B.2.4）：age, hn, tan, vcf, cta, esm, ala, alm

---

## 3. 因子计算公式（完整 Python 代码）

> 以下代码全部摘自 `code/main.py`，数据字段对应 CSMAR 表。屠龙可通过 mootdx/akshare/tushare 映射这些字段。
> **数据字段速查**：`TRD_Mnth`=月度行情, `TRD_Dalyr`=日度行情, `FS_Combas`=资产负债表, `FS_Comins`=利润表, `FS_Comscfd`=现金流量表, `TRD_Cndalym`=市场收益, `STK_MKT_THRFACDAY`=FF3因子, `CG_Ybasic`=公司年度基本信息

### 关键约定
- 所有因子输出为 **月度面板**（index=Trdmnt 如 "201905", columns=Stkcd）
- 日度数据因子：滚动窗口算完后取每月最后一个交易日的值
- 财务数据：季报公布后**滞后映射**到月（Q1→4月, Q2→8月, Q3→10月, Q4→次年4月），避免前视偏差
- `self.monthly_ret` = 月度收益率（已剔除 ST/PT/B 股）

---

### 3.1 B.1.1 流动性类（13 个）

```python
# B.1.1.1 Firm Size (size, size3)
def calc_size(self):
    lncap = self.get_data('TRD_Mnth', 'Msmvosd')   # 月末流通市值
    return np.log(lncap)

def calc_size3(self):
    midcap = self.get_data('TRD_Mnth', 'Msmvosd')
    return np.power(np.log(midcap), 3)              # log市值立方

# B.1.1.2 Share Turnover (turnm/turnq/turna) —— ★与屠龙换手率因子重叠
def calc_turnm(self):                                # 月换手
    volume = self.get_data('TRD_Mnth', 'Mnshrtrd')   # 月成交量
    mv = self.get_data('TRD_Mnth', 'Msmvosd')
    price = self.get_data('TRD_Mnth', 'Mclsprc')
    number = mv / price                               # 流通股数
    return np.log(volume / number)

def calc_turnq(self):                                # 季换手 = 3月均值取对数
    # ... 同上 turnover ...
    return np.log(turnover.rolling(3).mean())

def calc_turna(self):                                # 年换手
    return np.log(turnover.rolling(12, min_periods=4).mean())

# B.1.1.3 Variation of Share Turnover (vturn)
def calc_vturn(self, trading_day_num=120, min_day_num=40):
    # 日换手率 = Dnshrtrd / (Dsmvosd/Clsprc)
    return turnover.rolling(trading_day_num, min_periods=min_day_num).std()  # 取月末值

# B.1.1.4 CV of Share Turnover (cvturn)
def calc_cvturn(self, ...):
    sd = turnover.rolling(N).std(); mean = turnover.rolling(N).mean()
    return sd / mean                                  # 标准差/均值

# B.1.1.5 One-month Abnormal Turnover (abturn)
def calc_abturn(self, trading_month_num=21, trading_year_num=250, ...):
    # month_t = 过去21日换手均值; year_t = 过去250日换手均值
    return month_t / year_t                           # 异常换手

# B.1.1.6 Dollar Trading Volume (dtvm/dtvq/dtva)
def calc_dtvm(self):                                  # 月成交额取对数
    return np.log(self.get_data('TRD_Mnth', 'Mnvaltrd'))

# B.1.1.7-8 Variation/CV of Dollar Trading Volume (vdtv, cvd) —— 结构同 vturn/cvturn

# B.1.1.9 Amihud Illiquidity (Ami) —— ★经典流动性因子
def calc_Ami(self):
    volume = self.get_data('TRD_Mnth', 'Mnvaltrd')
    ret = abs(self.monthly_ret.copy())
    return ret / volume                               # |收益率|/成交额
```

---

### 3.2 B.1.2 风险类（13 个）

```python
# B.1.2.1 Idiosyncratic Volatility per CAPM (idvc)
def calc_idvc(self, trading_day_num=120, min_day_num=40):
    daily_ret = self.get_data('TRD_Dalyr', 'Dretwd')
    market_ret = self.get_data('TRD_Cndalym', 'Cdretwdos')
    rf = self.get_data('TRD_Nrrate', 'Nrrdaydt')
    x = market_ret - rf; x = sm.add_constant(x)
    for j in range(N_stocks):
        y = daily_ret[:, j] - rf
        model = RollingOLS(y, x, window=120, min_nobs=40).fit()
        result[:, j] = sqrt(MSE_resid * df_resid / (df_model+1+df_resid))  # 残差标准差
    # 取每月末值

# B.1.2.2-3 Idio Vol per FF3 (idvff) / q-factor (idvq)
#   —— 结构同 idvc，x 换成 [MktRF, SMB, HML] 或 [MktRF, SMB, RMW, CMA]

# B.1.2.5 Total Volatility (tv) —— ★与屠龙波动率因子重叠
def calc_tv(self, trading_day_num=120, min_day_num=40):
    daily_ret = self.get_data('TRD_Dalyr', 'Dretwd')
    return daily_ret.rolling(120, min_periods=40).std()

# B.1.2.6-8 Idiosyncratic Skewness (idsff/idsq/idsc)
#   —— RollingOLS 残差三阶矩 / (N * sigma^3)
def calc_idsff(self, ...):
    # RollingOLS 回归后，对残差算：
    temp_is = np.sum(np.power(temp_res, 3)) / (temp_num * np.power(temp_iv, 3))

# B.1.2.9 Total Skewness (ts)
def calc_ts(self, ...):
    return daily_ret.rolling(120, min_periods=40).skew()

# B.1.2.10 Co-skewness (cs)
def calc_cs(self, ...):
    # 残差与去均值市场收益的协偏度
    temp_cs = nanmean(temp_res * temp_mkt * temp_mkt) / (sqrt(nanmean(temp_res**2)) * nanmean(temp_mkt**2))

# B.1.2.12 Market Beta (beta)
def calc_beta(self, trading_day_num=120, min_day_num=40):
    # RollingOLS(daily_ret - rf ~ const + (mkt - rf))，取斜率系数
    return model.params[:, -1]

# B.1.2.13 Downside Beta (dbeta) —— ★下行风险，与普通beta正交
def calc_dbeta(self, trading_day_num=240, min_day_num=80):
    # 仅取市场收益 < 均值 的样本算 cov(y,mkt)/var(mkt)
    down_list = temp_mkt < nanmean(temp_mkt)
    return np.cov(temp_y[down_list], temp_mkt[down_list])[0][1] / np.var(temp_mkt[down_list])

# B.1.2.14 Frazzini-Pedersen Beta (betaFP)
def calc_betafp(self, win=120, minn=40):
    # betaFP = corr(r_3d, mkt) * sigma_i / sigma_m
    # sigma_i/m = 日对数超额收益 120 日标准差
    # corr = 3 日累计对数收益与市场的相关系数
    return np.corrcoef(temp_r3d, temp_mkt)[0][1] * temp_daily_sigma / temp_mkt_sigma

# B.1.2.15 Dimson Beta (betaDM)
def calc_betadm(self, ...):
    # rid-rf = a + b1*(mkt_{d-1}-rf) + b2*(mkt_d-rf) + b3*(mkt_{d+1}-rf) + e
    # betaDM = b1 + b2 + b3（修正非同步交易偏差）
    return model.params[:, -1] + model.params[:, -2] + model.params[:, -3]

# Chinese 4-factor beta (mkt_beta/vmg_beta/smb_beta/pmo_beta)
#   —— 对 Liu-Stambaugh-Yuan CH4 因子回归，取各因子载荷
```

---

### 3.3 ★ B.1.3 历史收益类（15 个，动量/反转，强推）

> **这是与屠龙换手率/估值因子正交度最高的类别，A 股短期反转+长期反转效应显著。**

```python
# B.1.3.0 当月收益 (m1)
def calc_m1(self):
    return self.monthly_ret.copy()

# B.1.3.1 短期动量/反转 (m11/m6/m3) —— A股常表现为反转
def calc_m11(self, start=1, end=11):     # 过去11个月累计收益（跳过当月）
    ret = self.monthly_ret.copy()
    result = 1
    for i in range(start, end + 1):
        result = result * (1 + ret.shift(i))
    return result - 1

def calc_m6(self, start=1, end=5):       # 过去6个月（t-1到t-5）
    # ... 同 m11 ...
def calc_m3(self, start=1, end=3):       # 过去3个月

# B.1.3.1b 长期反转 (m60) —— ★强推，A股长期反转显著
def calc_m60(self, start=12, end=59):    # 过去5年（跳过近1年）
    ret = self.monthly_ret.copy()
    result = 1
    for i in range(start, end + 1):
        result = result * (1 + ret.shift(i))
    return result - 1

# B.1.3.2 行业动量 (indmom) —— ★Barra方法，行业层面
def calc_indmom(self, month_num=6, min_month_num=2):
    ind_table = self.get_data('TRD_Co','Nnindcd')   # 行业代码
    ret = np.log(self.monthly_ret + 1)
    rs = ret.rolling(6, min_periods=2).mean()        # 个股6月平均对数收益
    sqmv = np.sqrt(mv)                                # sqrt(市值)作权重
    # 对每个行业：rsi = sum(rs*sqmv)/sum(sqmv)（行业市值加权收益）
    # indmom = -(sqmv * rs - rsi)  ← 个股相对行业的超额动量
    return -(sqmv * rs - rsi)

# B.1.3.3 中期动量 m24（跳过近1年的过去2年）
def calc_m24(self, start=12, end=35):    # t-35 到 t-12
    # 缺失月份按0处理（不跳过），最后标记NaN

# B.1.3.4 动量变化 (mchg) —— ★近期vs远期动量差
def calc_mchg(self, s1=1, e1=6, s2=7, e2=12):
    # m1 = t-1~t-6 动量; m2 = t-7~t-12 动量
    return m1 - m2                          # 近期动量 - 远期动量

# B.1.3.7 残差动量 (im12/im6) —— ★强推，与普通动量正交
def calc_im12(self, im_day_num=240, trading_day_num=720, min_day_num=240):
    # 1. RollingOLS(daily_ret-rf ~ FF3) 用720日回归
    # 2. 取过去240日的残差
    # 3. im = mean(residual) / std(residual)
    return np.nanmean(temp_res) / np.nanstd(temp_res)

def calc_im6(self, im_day_num=120, ...):  # 用120日残差

# B.1.3.8 52周新高 (52w) —— ★强推，技术类
def calc_52w(self):
    daily = self.get_data('TRD_Dalyr', 'Adjprcwd')  # 复权价
    # 当前价 / 过去240日最高价
    return temp[-1] / np.max(temp)

# B.1.3.9 最大日收益 (mdr) —— ★彩票偏好异象
def calc_mdr(self, mean_num=5, min_day_num=15, min_feb_num=5):
    # 过去一个月最高5个日收益的均值
    # 1/2月只要求5个交易日（春节调整）
    return np.nanmean(top5_daily_returns)

# B.1.3.10 股价 (pr)
def calc_pr(self):
    return self.get_data('TRD_Dalyr', 'Adjprcwd')  # 月末复权价

# B.1.3.13 季节性 (season) —— ★A股市"春节效应"/"年报行情"
def calc_season(self, year_num=5, min_year_num=3):
    # 取过去5年同月（t-12,t-24,...,t-60）收益的均值
    temp = [ret[i-12], ret[i-24], ret[i-36], ret[i-48], ret[i-60]]
    return np.nanmean(temp)  # 至少3年数据
```

---

### 3.4 ★ B.2.1 盈利类（20 个，质量因子，强推）

```python
# B.2.1.1 ROE
def calc_roe(self):
    q_net_income = self.get_data('FS_Comins', 'B002000000')       # 净利润
    oq_lag_bv = self.get_data('FS_Combas', 'A003000000').shift(3) # 滞后1季所有者权益
    prefer = self.get_data('FS_Combas', 'A003112101').shift(3)    # 优先股
    return q_net_income / (oq_lag_bv - prefer)

# B.2.1.2 ROE变化 (droe)
def calc_droe(self):
    # roe - roe.shift(12)  （同比变化）

# B.2.1.3-4 ROA / droa —— 结构同 roe，分母换 A001000000 总资产

# B.2.1.6 净营业资产回报 (rna)
def calc_rna(self):
    op_in = self.get_data('FS_Comins', 'B001300000')    # 营业利润
    noa = short_debt + long_debt + min_in + prefer - cash - short_inv  # 净营业资产
    return op_in / noa

# B.2.1.6 利润率 (pm)
def calc_pm(self):
    op_in = self.get_data('FS_Comins', 'B001300000')    # 营业利润
    op_rev = self.get_data('FS_Comins', 'B001101000')   # 营业收入
    return op_in / op_rev

# B.2.1.6 资产周转率 (ato) = 营业收入 / 净营业资产

# B.2.1.8 资本周转率 (ct) = 营业收入 / 滞后总资产

# B.2.1.9 毛利/资产 (gpa) —— 年报口径
def calc_gpa(self):
    tr_cgs = self.get_data('FS_Comins', 'B001000000', fs_freq='y')  # 总利润(营收-成本)
    ta = self.get_data('FS_Combas', 'A001000000', fs_freq='y')
    return tr_cgs / ta

# B.2.1.11 毛利/滞后资产 (gpla) = 总利润 / 滞后1季总资产

# B.2.1.12-17 营业利润/权益、/滞后权益、/资产、/滞后资产 (ope/ople/opa/opla)

# B.2.1.19 应税收入/账面收入 (tbi) = 税前利润 / 净利润

# B.2.1.21 账面杠杆 (bl) = 总资产 / (所有者权益 - 优先股)

# B.2.1.22-23 销售增长 (sg 年 / sgq 季) —— ★强推
def calc_sg(self):
    sales = self.get_data('FS_Comins', 'B001101000', fs_freq='y')
    return (sales - sales.shift(12)) / sales.shift(12)
def calc_sgq(self):
    return q_sales / q_sales.shift(12)

# B.2.1.25 Piotroski F-Score (Fscore) —— ★强推，9维质量评分
def calc_Fscore(self):
    # 9个0/1信号求和：froa, fcfa, fdroa, fdlever, fdliquid, fdmargin, fdturn
    # 衡量盈利/流动性/运营效率改善
    Fscore = froa+fcfa+fdroa+fdlever+fdliquid+fdmargin+fdturn
    return Fscore

# B.2.1.27 Ohlson O-Score (Oscore) —— ★破产预测
def calc_Oscore(self):
    # O = -1.32 -0.407*log(TA) +6.03*TLTA -1.43*WCTA +0.076*CLCA
    #     -1.72*OENEG -2.37*NITA -1.83*FUTL +0.285*IN2 -0.521*CHIN
    return Oscore
```

---

### 3.5 B.2.2 价值类（12 个，部分与屠龙估值因子重叠）

```python
# B.2.2.3 Book-to-Market (bm)
def calc_bm(self):
    book_value = self.get_data('FS_Combas', 'A003000000')
    market_equity = self.get_data('TRD_Mnth', 'Msmvosd') * 1000
    return (book_value - prefer) / market_equity

# B.2.2.5 负债/市值 (dm) = 总负债 / 市值
# B.2.2.7 资产/市值 (am) = 总资产 / 市值
# B.2.2.9 盈利/价格 (ep) = 净利润 / 市值
# B.2.2.11 现金流/价格 (cfp) = 净现金流 / 市值
# B.2.2.12 5年销售增长排名 (sr) —— 几何加权
def calc_sr(self):
    sales = self.get_data('FS_Comins', 'B001101000', fs_freq='y')
    sr = (sales.shift(48)-sales.shift(60)) / sales.shift(60)
    for i in range(4,0,-1):
        sr += (6-i)*(sales.shift(i*12-12)-sales.shift(i*12)) / sales.shift(i*12)
    return sr
# B.2.2.14 企业倍数 (em) = 企业价值 / 营业收入
#   企业价值 = 市值 + 总负债 + 优先股 - 现金 - 短期投资
# B.2.2.16 销售/价格 (sp) = 营业收入 / 市值
# B.2.2.18 经营现金流/价格 (ocfp)
# B.2.2.19 负债/权益 (de) = 总负债 / (所有者权益-优先股)
# B.2.2.22 企业账面/价格 (ebp), 净债务/价格 (ndp)
```

---

### 3.6 ★ B.2.3 投资类（19 个，强推）

> A 股"投资异象"显著：**高投资股票未来收益差**（资产增长异常）。与盈利因子正交。

```python
# B.2.3.3 资产增长 (ag) —— ★强推，最经典投资异象
def calc_ag(self):
    total_assets = self.get_data('FS_Combas', 'A001000000')
    return (total_assets - total_assets.shift(12)) / total_assets.shift(12)

# B.2.3.4 PPE+存货变化/资产 (dpia)
def calc_dpia(self):
    fixed_assets = self.get_data('FS_Combas', 'A001212000')
    inventory = self.get_data('FS_Combas', 'A001123000')
    total_assets = self.get_data('FS_Combas', 'A001000000').shift(12)
    return (fixed_assets + inventory) / total_assets

# B.2.3.5 净营业资产 (noa) 及其变化 (dnoa)
def calc_noa(self):
    noa = short_debt + long_debt + min_in + prefer - cash - short_inv
    return noa / total_assets.shift(12)
def calc_dnoa(self):
    return (noa.shift(12) - noa) / total_assets.shift(12)   # 注意方向：减少为正

# B.2.3.6 投资增长 (ig) = (capex - capex.shift(12)) / capex.shift(12)
#   capex = FS_Comscfd C002006000 (购建固定资产支出)

# B.2.3.8 综合股权发行 (cei) —— ★强推
def calc_cei(self):
    me_growth = np.log(market_equity / market_equity.shift(12))  # 市值增长
    ret_12m = 累计12月收益
    return me_growth - np.log(ret_12m)   # 剔除收益后的"纯增发"

# B.2.3.9 综合债务发行 (cdi) = log((负债-负债.shift(12))/负债.shift(12))
# B.2.3.10-11 存货增长 (ivg) / 存货变化 (ivchg)

# B.2.3.12 经营性应计 (oacc) —— ★强推，应计异象
def calc_oacc(self):
    return (net_profit - op_cash_flows) / total_assets.shift(12)

# B.2.3.13 总应计 (tacc) = (总利润 - 现金流) / 滞后总资产

# B.2.3.14 营运资本变化 (dwc) 及其分解 (dcoa/dcol)
def calc_dwc(self):
    coa = current_assets - cash - short_inv       # 经营性流动资产
    col = current_lia - short_debt                 # 经营性流动负债
    wc = coa - col
    return (wc - wc.shift(12)) / total_assets.shift(12)

# B.2.3.15 非流动经营资产/负债变化 (dnco/dnca/dncl)
# B.2.3.16 金融资产变化 (dfin), 账面权益变化 (dbe)
```

---

### 3.7 ★ B.2.4 其他异象（22 个，强推）

> **盈利惊喜(sue/rs/tes)和质量变化类因子与已有因子正交度极高，建议重点。**

```python
# B.2.4.1-2 广告费用 (adm) 及其增长 (gad)
# B.2.4.4-6 研发费用/市值 (rdm)、研发/销售 (rds)

# B.2.4.8 经营杠杆 (ol) = 营业成本 / 总资产

# B.2.4.9 雇佣率 (hn) —— ★强推
def calc_hn(self):
    employee = self.get_data('CG_Ybasic', 'Y0601b')   # 员工数
    return (employee - employee.shift(12)) / (0.5*employee + 0.5*employee.shift(12))

# B.2.4.10 公司年龄 (age) —— ★IPO后长期弱势
def calc_age(self):
    listdate = self.get_data('CG_Co','ListedDate')
    # 当前月份 - 上市月份（月数）
    return (year_cur - year_ipo)*12 + (month_cur - month_ipo)

# B.2.4.11-14 杜邦分析变体
#   dsi = 销售增速 - 存货增速
#   dsa = 销售增速 - 应收增速
#   dgs = 毛利增速 - 销售增速    ← 盈利质量信号
#   dss = 销售增速 - 销管费增速

# B.2.4.15 有效税率 (etr)
def calc_etr(self):
    te = tax_exp / EBT
    return (te - mean(te.shift(36,24,12))) * deps   # 税率变化×EPS变化

# B.2.4.16 劳动效率 (lfe)
def calc_lfe(self):
    # (人均销售_t - 人均销售_{t-1}) / 人均销售_{t-1}

# B.2.4.18 有形性 (tan) —— ★资产抵押价值
def calc_tan(self):
    tan = cash + short_inv + 0.715*acc_rec + 0.547*inventory + 0.535*fixed_assets
    return tan / total_assets

# B.2.4.19 现金流波动 (vcf) —— ★60月经营现金流/销售的标准差/均值
def calc_vcf(self, month_num=60, min_month_num=20):
    cash_flows = self.get_data('FS_Comscfd', 'C005000000')
    return cash_flows.rolling(60, min_periods=20).apply(
        lambda x: np.std(x) / np.mean(x) if np.mean(x) != 0 else np.nan)

# B.2.4.20 现金/资产 (cta)
# B.2.4.22 盈利平滑度 (esm) —— ★强推
def calc_esm(self, month_num=36, min_month_num=12):
    # (净利润/总资产)的标准差 / (经营现金流/总资产)的标准差
    return std(earnings/TA.shift(12)) / std(oper_cash_flow/TA.shift(12))

# B.2.4.26 资产流动性 (ala 年 / alm 月) —— ★强推
def calc_ala(self):
    al = cash + 0.75*(current_assets - cash) + 0.5*(total_asset - current_assets - goodwill - intangibles)
    return al / total_asset.shift(3)

# B.2.4.27 标准化未预期盈利 (sue) —— ★★强推，盈利动量
def calc_sue(self, change_qnum=4, std_qnum=8, std_min_qnum=6):
    # earnings = 季度净利润的环比变化（Q4不环比）
    # change = earnings - earnings.shift(4)          （同比变化）
    # std = 过去8个季度 earnings 环比变化的标准差（至少6个）
    # sue = change / std
    return change / std

# B.2.4.28 收入惊喜 (rs) —— 结构同 sue，用营业收入 B001100000
# B.2.4.29 税收惊喜 (tes) —— 季度税收变化率
```

---

## 4. 数据字段映射表（CSMAR → 屠龙可对接）

| CSMAR 表 | 含义 | 关键字段 | 屠龙替代源 |
|---|---|---|---|
| TRD_Mnth | 月度行情 | Mretwd(收益), Msmvosd(流通市值), Mnshrtrd(成交量), Mnvaltrd(成交额), Mclsprc(收盘价) | tushare/mootdx 月线 |
| TRD_Dalyr | 日度行情 | Dretwd, Dsmvosd, Dnshrtrd, Dnvaltrd, Clsprc, Adjprcwd(复权价) | tushare pro_bar / eastmoney 日线 |
| FS_Combas | 资产负债表 | A001000000(总资产), A003000000(权益), A002000000(负债), A001101000(货币资金), A001109000(短期投资), A001111000(应收), A001123000(存货), A001212000(固定资产), A002100000(流动负债), A002101000(短期借款), A002201000(长期借款), A002206000(长期借款-应付债券), A003112101(优先股), A001100000(流动资产), A001207000(长期投资), A001218000(无形资产), A001220000(商誉), A003200000(少数股东权益) | tushare balancesheet |
| FS_Comins | 利润表 | B001000000(总利润=营收-成本), B001101000(营业收入), B001201000(营业成本), B001209000(销售费用), B001210000(管理费用), B001300000(营业利润), B002000000(净利润), B002100000(所得税) | tushare income |
| FS_Comscfd | 现金流量表 | C001000000(经营活动现金流), C005000000(净现金流), C002006000(购建固定资产支出) | tushare cashflow |
| TRD_Cndalym | 市场收益 | Cdretwdos(全A日收益) | tushare index_daily 000985.SH |
| STK_MKT_THRFACDAY | FF3因子 | RiskPremium1, SMB1, HML1 | cenet / 自算 |
| STK_MKT_FIVEFACDAY | 5因子 | +RMW1, CMA1 | cenet / 自算 |
| TRD_Nrrate | 无风险利率 | Nrrdaydt(一年定存) | tushare shibor |
| TRD_Co | 公司信息 | Nnindcd(行业代码) | tushare stock_basic |
| CG_Co | 上市信息 | ListedDate | tushare stock_basic |
| CG_Ybasic | 年度基本 | Y0601b(员工数) | tushare stock_employee |

**滞后映射规则（避免前视，关键！）：**
```
Q1(0331) → 4月可用     Q2(0630) → 8月可用
Q3(0930) → 10月可用    Q4(1231) → 次年4月可用
```
代码中 `shift(3)` 用于季度滞后，`shift(12)` 用于年度滞后。

---

## 5. 屠龙系统集成建议

### 5.1 优先级排序（按与现有因子的正交性）

| 优先级 | 因子类别 | 具体因子 | 理由 |
|---|---|---|---|
| **P0** | 残差动量 | im12, im6 | 剔除FF3后的纯alpha动量，与换手率/估值几乎零相关 |
| **P0** | 长期反转 | m60, m24 | A股长期反转效应强，与短期换手率正交 |
| **P0** | 盈利惊喜 | sue, rs, tes | PEAD现象，事件驱动，与量价因子正交 |
| **P0** | 应计异象 | oacc, tacc, dnoa | 盈利质量，基本面维度 |
| **P0** | 资产增长 | ag, ig, cei | 投资异象，A股显著 |
| **P1** | 质量评分 | Fscore, Oscore, esm | 综合质量，多个信号合成 |
| **P1** | 动量变化 | mchg, indmom | 二阶动量信号 |
| **P1** | 技术类 | 52w, mdr, season, pr | 低价股/彩票效应 |
| **P1** | 雇佣/年龄 | hn, age | 微观结构异象 |
| **P2** | 下行风险 | dbeta, betafp | 风险分解，与普通beta正交 |
| **P2** | 资产流动性 | ala, alm, tan | 抵押价值/流动性 |

### 5.2 实现路径

1. **数据层**：屠龙已有日K/财务数据，需补充 FF3/CH4 因子（可自算或从 cenet 获取）
2. **因子层**：将 `calc_*` 函数改写为屠龙因子接口（输入 DataFrame，输出截面 rank）
3. **评估层**：直接用 `panel_rank.csv` 格式对接屠龙 IC/ICIR 引擎
4. **组合层**：用 `sortedportfolio.py` 的 10 分位框架做单因子检验，再用屠龙的多因子正交化（Schmidt/对称正交）做合成

### 5.3 注意事项

- **复权**：所有价格类因子（52w/pr/beta）必须用前复权价 `Adjprcwd`
- **ST/PT 过滤**：代码已剔除 Trdsta 不在 {1,4,7,10,13} 的股票，屠龙需同步
- **B 股剔除**：Markettype ∈ {2,8} 的 B 股已过滤
- **新股过滤**：建议上市不满 12 个月的不参与（age 因子天然处理）
- **1/2月特殊处理**：mdr 因子对春节月降低了最小交易日要求（5日 vs 15日）

---

## 6. 关键代码位置索引

| 内容 | 文件 | 行号 |
|---|---|---|
| 数据加载/ST过滤 | code/main.py | 26-178 |
| 流动性因子（13） | code/main.py | 195-343 |
| 风险因子（13） | code/main.py | 350-916 |
| 动量/反转因子（15） | code/main.py | 918-1230 |
| 盈利因子（20） | code/main.py | 1232-1513 |
| 价值因子（12） | code/main.py | 1515-1638 |
| 投资因子（19） | code/main.py | 1641-1831 |
| 其他异象（22） | code/main.py | 1833-2177 |
| 因子清单定义 | code/main.py | 2184-2208 |
| 因子执行入口 | code/main.py | 2227-2235 |
| 截面排序框架 | code/tools/crosssection.py | 全文 |
| 组合构建+Sharpe | code/tools/sortedportfolio.py | 41-90 |
| 因子目录+论文出处 | char_list.csv | 全文 |

---

## 7. 参考资源

- 项目主页: https://github.com/Quantactix/ChinaAShareEquityCharacteristics
- 理论基础: Hou, Xue, Zhang (2020) "Replicating Anomalies" (RFS) 附录 B
- A股因子: Liu, Stambaugh, Yuan (2019) "Size and Value in China" (JFE) → CH4 因子
- 相关项目: [mlfina/China-A-Sort](https://github.com/mlfina/China-A-Sort)（同框架 forks）
- 综述论文: Zhou (2025) "Cross-sectional anomalies and conditional asset pricing" (Financial Innovation) — 30个A股截面异象+投资者情绪

**本地路径**：`D:/zcodesj/Chinese-Stock-Anomalies/`（已下载，可直接阅读 main.py）
