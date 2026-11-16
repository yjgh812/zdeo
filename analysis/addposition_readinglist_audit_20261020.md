# 加仓学习书单第六轮到件审计（只读，2026-10-20）

- 到件：外部书单（AFML 精读章节 / Zhang & Zohren 2025 / RL 教材 / A股微观结构中文书，映射工程周次）
- 对账基线：10/17 量价书单张冠李戴教训（书目必须先定锚再入库）+ 联网核验四条
- 置信度：高（存疑条目全部联网核实；两处未验证项已标注）

## 一、总判定

**章节↔工程映射准确，是本轮真价值**（AFML 第3/4/7/11/12章 ↔ 标签/权重/防泄漏CV/DSR·PBO 的对应关系与真实目录一致）；论文引用全部真实。但 **MLAM 条目两处失实**（重印冒充新版 + 虚构章节），Victor Trex 书自出版嫌疑须降级，获取渠道须按"公开层先行"重排。

## 二、逐条定锚结果

| 条目 | 核验结果 | 判定 |
|---|---|---|
| AFML (2018) 第3/4/5/7/11/12章 | 与真实目录一致（Labeling/Sample Weights/FFD/Purged CV/Backtest Statistics） | ✅ 精读清单成立 |
| MLAM "2026年1月新版，第8章 Testing set overfitting" | **两处失实**：①2026-01-08 是同一 2020 版**重印**（ISBN 9781009702423），非扩充新版；②真实目录（Introduction/Denoising/Distance metrics/Optimal clustering/Financial labels/Feature importance/CART/FFS/PPO clustering）**无该章**，"替代 AFML 11-12 章"不成立 | ❌ 纠错：过拟合检验内容只在 AFML 11-12，MLAM 不入清单 |
| Zhang & Zohren《Deep Learning in Quantitative Trading》(2025) | **属实**：Cambridge **Elements**（短专著非全书），2025-10，DOI 10.1017/9781009707091，Zihao Zhang & Stefan Zohren（Oxford）；"184页"未核验 | ✅ 认可（注意是 Element 篇幅）；配套 GitHub 代码公开 |
| Lim, Zohren & Roberts (2019, JFDS) DMN 论文 | 真实（arXiv 有免费版） | ✅ 零成本层 |
| "Slow Momentum with Fast Reversion" | 真实（Wood/Roberts/Roberts/Zohren，JFDS 2021） | ✅ 零成本层 |
| Sutton & Barto 第2版，第3-6章+第13章 | 真实；且**官方在 incompleteideas.net 提供免费全文** | ✅ 零成本层 |
| Hilpisch《RL for Finance》(O'Reilly) | 真实 | 认可（实操向，可选） |
| **Victor Trex《RL for Finance: From Portfolio Allocation to Smart Execution》(2025)** | 存在但**出版信号极弱**：作者名=出版社名（NobleTrex Press），仅德国旧书网站单条在售，无主流渠道/目录/评价——自出版嫌疑 | ❌ 不入精读清单 |
| 韩乾《中国金融市场微结构实务》北大社 2021.6 | **属实**（厦门大学韩乾编著，百度百科+出版信息在案；新版《中国金融市场微结构：理论与实务》与刘彦初合著亦存在） | ✅ 认可（中文纸书需购买） |
| 郭喜才《价格涨跌幅限制制度…》（复旦社） | **未验证**——非承重条目，后置 | ⏳ 待核验 |
| T+1 折价率期刊研究（双边异随机前沿） | **未验证**（无具体文献） | ⏳ 待核验，暂不引用 |

## 三、获取渠道重排（10/17 教训：正文付费墙内公开层≈0，不围绕付费内容做计划）

**零成本层（可直接开工）**：Sutton & Barto 官方免费全文（incompleteideas.net）→ DMN 论文 arXiv 版 → López de Prado 官网 AFML 分章 slides（作者公开）→ Zhang & Zohren Elements 的 GitHub 代码库（书付费、代码公开）。
**付费层（呈请主人定锚购买与否）**：AFML 纸书/电子、韩乾中文纸书、Zhang & Zohren Element 正文。

## 四、执行建议

1. **读书不阻塞工程**：书单按周次并行映射是合理的，但 Week 0 基线回测（R0-R3）不依赖任何阅读，先跑。
2. 纠偏后最小清单 = AFML 第3/4/7/11/12章（配公开 slides 对照 RiskLabAI 源码）+ Zhang & Zohren Element（配 GitHub 代码）+ Sutton & Barto 第3-6/13章（免费全文）+ 韩乾书（案头制度参考，纸书呈请）。
3. 郭喜才/T+1 期刊两条维持"未验证"标签，不得作为引用依据进入报告（反臆想纪律）。

## 附：核验来源

Cambridge MLAM 页（目录与 2026 重印信息）、Cambridge Z&Z Element 页（DOI 10.1017/9781009707091）、buchfreund.de Victor Trex 书单条在售页、百度百科韩乾书条目。
