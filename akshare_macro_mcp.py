"""
akshare 宏观数据 MCP 服务
通过 FastMCP 暴露中国宏观数据工具，供 ZCode 对话中直接调用

数据来源：akshare (https://github.com/akfamily/akshare)
"""

from mcp.server.fastmcp import FastMCP
import akshare as ak
import pandas as pd
from datetime import datetime

# 创建 MCP 服务，名称用于在 ZCode 配置中标识
mcp = FastMCP("akshare-macro")

# ─── 中国经济总量 ──────────────────────────────────────────────

@mcp.tool()
def macro_gdp_yearly() -> str:
    """获取中国GDP年率报告（年度同比增速）
    
    Returns:
        CSV格式数据，包含：商品、日期、今值、预测值、前值
    """
    df = ak.macro_china_gdp_yearly()
    return df.to_csv(index=False)

@mcp.tool()
def macro_gdp_quarterly() -> str:
    """获取中国GDP季度数据
    
    Returns:
        CSV格式数据，包含国内生产总值各季度绝对值与同比
    """
    df = ak.macro_china_gdp()
    return df.to_csv(index=False)

# ─── 价格指数 ──────────────────────────────────────────────────

@mcp.tool()
def macro_cpi_yearly() -> str:
    """获取中国CPI年率报告（居民消费价格指数同比）
    
    Returns:
        CSV格式：商品、日期、今值、预测值、前值
    """
    df = ak.macro_china_cpi_yearly()
    return df.to_csv(index=False)

@mcp.tool()
def macro_cpi_monthly() -> str:
    """获取中国CPI月度数据（含城市/农村/食品/非食品分类）
    
    Returns:
        CSV格式：月度CPI详细分类数据
    """
    df = ak.macro_china_cpi_monthly()
    return df.to_csv(index=False)

@mcp.tool()
def macro_ppi_yearly() -> str:
    """获取中国PPI年率报告（工业生产者出厂价格指数同比）
    
    Returns:
        CSV格式：商品、日期、今值、预测值、前值
    """
    df = ak.macro_china_ppi_yearly()
    return df.to_csv(index=False)

# ─── 采购经理指数 ──────────────────────────────────────────────

@mcp.tool()
def macro_pmi() -> str:
    """获取中国PMI数据（制造业/非制造业，月度）
    
    含制造业指数及同比、非制造业指数及同比
    """
    df = ak.macro_china_pmi()
    return df.to_csv(index=False)

@mcp.tool()
def macro_non_man_pmi() -> str:
    """获取中国非制造业PMI月度数据
    
    Returns:
        CSV格式：建筑业/服务业PMI详细分类
    """
    df = ak.macro_china_non_man_pmi()
    return df.to_csv(index=False)

# ─── 货币与信贷 ────────────────────────────────────────────────

@mcp.tool()
def macro_money_supply() -> str:
    """获取中国货币供应量（M0/M1/M2，月度）
    
    含数量、同比、环比，从2008年起
    """
    df = ak.macro_china_money_supply()
    return df.to_csv(index=False)

@mcp.tool()
def macro_m2_yearly() -> str:
    """获取中国M2年率报告（货币供应量同比）
    
    Returns:
        CSV格式：商品、日期、今值、预测值、前值
    """
    df = ak.macro_china_m2_yearly()
    return df.to_csv(index=False)

@mcp.tool()
def macro_lpr() -> str:
    """获取中国LPR（贷款市场报价利率，1年期/5年期）
    
    含LPR1Y、LPR5Y及基准利率，更新至最新
    """
    df = ak.macro_china_lpr()
    return df.to_csv(index=False)

@mcp.tool()
def macro_shibor() -> str:
    """获取Shibor（上海银行间同业拆放利率）全品种日数据
    
    含O/N(隔夜)、1W、2W、1M、3M、6M、9M、1Y及涨跌幅
    """
    df = ak.macro_china_shibor_all()
    return df.to_csv(index=False)

@mcp.tool()
def macro_reserve_requirement() -> str:
    """获取中国存款准备金率调整历史
    
    含大型/中小型金融机构调整前后，及消息公布次日股市涨跌
    """
    df = ak.macro_china_reserve_requirement_ratio()
    return df.to_csv(index=False)

# ─── 对外贸易 ──────────────────────────────────────────────────

@mcp.tool()
def macro_trade_balance() -> str:
    """获取中国贸易顺差/逆差月度数据
    
    Returns:
        CSV格式：出口、进口、贸易差额
    """
    df = ak.macro_china_trade_balance()
    return df.to_csv(index=False)

@mcp.tool()
def macro_exports_yoy() -> str:
    """获取中国出口总额同比增速月度数据
    
    Returns:
        CSV格式：月度出口同比增速
    """
    df = ak.macro_china_exports_yoy()
    return df.to_csv(index=False)

@mcp.tool()
def macro_imports_yoy() -> str:
    """获取中国进口总额同比增速月度数据
    
    Returns:
        CSV格式：月度进口同比增速
    """
    df = ak.macro_china_imports_yoy()
    return df.to_csv(index=False)

# ─── 金融市场 ──────────────────────────────────────────────────

@mcp.tool()
def macro_stock_market_cap() -> str:
    """获取中国股票市场市值数据（沪深）
    
    Returns:
        CSV格式：总市值、流通市值等
    """
    df = ak.macro_china_stock_market_cap()
    return df.to_csv(index=False)

@mcp.tool()
def macro_margin_sh() -> str:
    """获取沪市融资融券余额历史数据
    
    Returns:
        CSV格式：上海市场融资/融券余额
    """
    df = ak.macro_china_market_margin_sh()
    return df.to_csv(index=False)

@mcp.tool()
def macro_margin_sz() -> str:
    """获取深市融资融券余额历史数据
    
    Returns:
        CSV格式：深圳市场融资/融券余额
    """
    df = ak.macro_china_market_margin_sz()
    return df.to_csv(index=False)

# ─── 利率与汇率 ────────────────────────────────────────────────

@mcp.tool()
def macro_rmb_exchange_rate() -> str:
    """获取人民币汇率中间价历史数据（美元/欧元/日元/港元/英镑等）
    
    Returns:
        CSV格式：各币种每日中间价
    """
    df = ak.macro_china_rmb()
    return df.to_csv(index=False)

@mcp.tool()
def macro_foreign_reserves() -> str:
    """获取中国外汇储备年度数据
    
    Returns:
        CSV格式：外汇储备金额
    """
    df = ak.macro_china_fx_reserves_yearly()
    return df.to_csv(index=False)

# ─── 行业与景气 ────────────────────────────────────────────────

@mcp.tool()
def macro_industrial_production() -> str:
    """获取中国工业增加值同比增速月度数据
    
    Returns:
        CSV格式：工业增加值同比增速
    """
    df = ak.macro_china_industrial_production_yoy()
    return df.to_csv(index=False)

@mcp.tool()
def macro_enterprise_boom_index() -> str:
    """获取中国企业景气指数（季度）
    
    Returns:
        CSV格式：企业景气指数及企业家信心指数
    """
    df = ak.macro_china_enterprise_boom_index()
    return df.to_csv(index=False)

# ─── 全球宏观 ──────────────────────────────────────────────────

@mcp.tool()
def macro_usa_non_farm_payrolls() -> str:
    """获取美国非农就业数据（月度）
    
    最重要的全球宏观指标之一，影响美元/美股/A股外资流动
    """
    df = ak.macro_usa_non_farm()
    return df.to_csv(index=False)

@mcp.tool()
def macro_usa_cpi_yoy() -> str:
    """获取美国CPI年率报告（消费者物价指数同比）
    
    Returns:
        CSV格式：商品、日期、今值、预测值、前值
    """
    df = ak.macro_usa_cpi_yoy()
    return df.to_csv(index=False)

@mcp.tool()
def macro_usa_ism_pmi() -> str:
    """获取美国ISM制造业PMI月度数据
    
    Returns:
        CSV格式：ISM制造业PMI历史数据
    """
    df = ak.macro_usa_ism_pmi()
    return df.to_csv(index=False)

@mcp.tool()
def macro_bdi_index() -> str:
    """获取BDI（波罗的海干散货指数）历史数据
    
    全球航运/贸易景气度的领先指标
    """
    df = ak.macro_shipping_bdi()
    return df.to_csv(index=False)

# ─── 辅助工具 ──────────────────────────────────────────────────

@mcp.tool()
def macro_list_available() -> str:
    """列出所有可用的宏观数据工具及其简要说明"""
    tools_info = {
        "macro_gdp_yearly": "中国GDP年率同比",
        "macro_gdp_quarterly": "中国GDP季度数据",
        "macro_cpi_yearly": "中国CPI年率同比",
        "macro_cpi_monthly": "中国CPI月度详细分类",
        "macro_ppi_yearly": "中国PPI年率同比",
        "macro_pmi": "中国PMI（制造业/非制造业）",
        "macro_non_man_pmi": "中国非制造业PMI详细",
        "macro_money_supply": "中国M0/M1/M2货币供应量",
        "macro_m2_yearly": "中国M2年率同比",
        "macro_lpr": "中国LPR（1年期/5年期）",
        "macro_shibor": "Shibor全品种日数据",
        "macro_reserve_requirement": "存款准备金率调整历史",
        "macro_trade_balance": "中国贸易顺差/逆差",
        "macro_exports_yoy": "出口总额同比增速",
        "macro_imports_yoy": "进口总额同比增速",
        "macro_stock_market_cap": "沪深股市总市值",
        "macro_margin_sh": "沪市融资融券余额",
        "macro_margin_sz": "深市融资融券余额",
        "macro_rmb_exchange_rate": "人民币汇率中间价",
        "macro_foreign_reserves": "外汇储备",
        "macro_industrial_production": "工业增加值同比",
        "macro_enterprise_boom_index": "企业景气指数",
        "macro_usa_non_farm_payrolls": "美国非农就业",
        "macro_usa_cpi_yoy": "美国CPI年率同比",
        "macro_usa_ism_pmi": "美国ISM制造业PMI",
        "macro_bdi_index": "波罗的海干散货指数(BDI)",
    }
    df = pd.DataFrame(list(tools_info.items()), columns=["tool_name", "description"])
    return df.to_csv(index=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")