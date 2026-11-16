"""
baostock MCP Server — 免费 A 股数据
数据源: baostock (完全免费，无需注册)
覆盖: 日K/周K/月K/分钟K/除权因子/行业分类/交易日历
"""

from mcp.server.fastmcp import FastMCP
import baostock as bs
import os
from pathlib import Path
import pandas as pd

mcp = FastMCP("baostock-mcp")


def _norm_date(d: str) -> str:
    """把 YYYYMMDD 规范化为 baostock 要求的 YYYY-MM-DD；空值/已是 YYYY-MM-DD 则原样返回"""
    if not d:
        return d
    d = d.strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def _check(rs):
    """统一校验 baostock 查询结果，出错抛明确错误（避免静默 No data）"""
    if rs is None or rs.error_code != "0":
        code = getattr(rs, "error_code", "None")
        msg = getattr(rs, "error_msg", "query returned None")
        raise ConnectionError(f"baostock 查询失败: code={code} msg={msg}")
    return rs


def _load_api_key() -> str:
    """API Key: env BAOSTOCK_API_KEY 优先，D盘密钥文件兜底；都没有则匿名登录"""
    v = os.environ.get("BAOSTOCK_API_KEY", "").strip()
    if not v:
        p = Path("D:/zcodesj/config/baostock_api_key.txt")
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
    return v


def _login():
    """登录 baostock（幂等）；有 Key 先 set_API_key"""
    key = _load_api_key()
    if key:
        bs.set_API_key(key)
    lg = bs.login()
    if lg.error_code != "0":
        raise ConnectionError(f"baostock 登录失败: {lg.error_msg}")


@mcp.tool()
def get_daily_kline(code: str, start_date: str = "", end_date: str = "", adjust: str = "2") -> str:
    """
    获取日 K 线数据
    Args:
        code: 股票代码，格式 sh.600519 或 sz.000001
        start_date: 开始日期 YYYYMMDD，默认上市首日
        end_date: 结束日期 YYYYMMDD，默认最新
        adjust: 复权 1=后复权 2=前复权 3=不复权
    Returns: 日 K 线 CSV
    """
    try:
        _login()
        rs = bs.query_history_k_data_plus(
            code, 
            "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST",
            start_date=_norm_date(start_date), end_date=_norm_date(end_date),
            frequency="d", adjustflag=adjust
        )
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_minute_kline(code: str, freq: str = "5", start_date: str = "", end_date: str = "") -> str:
    """
    获取分钟 K 线数据
    Args:
        code: 股票代码 sh.600519
        freq: 频率 5=5分钟 15=15分钟 30=30分钟 60=60分钟
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
    Returns: 分钟 K 线 CSV
    """
    try:
        _login()
        rs = bs.query_history_k_data_plus(
            code,
            "date,time,code,open,high,low,close,volume,amount,adjustflag",
            start_date=_norm_date(start_date), end_date=_norm_date(end_date),
            frequency=freq, adjustflag="3"
        )
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_weekly_kline(code: str, start_date: str = "", end_date: str = "", adjust: str = "2") -> str:
    """
    获取周 K 线数据
    Args:
        code: 股票代码 sh.600519
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
        adjust: 复权 2=前复权
    Returns: 周 K 线 CSV
    """
    try:
        _login()
        rs = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,volume,amount,pctChg",
            start_date=_norm_date(start_date), end_date=_norm_date(end_date),
            frequency="w", adjustflag=adjust
        )
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_monthly_kline(code: str, start_date: str = "", end_date: str = "", adjust: str = "2") -> str:
    """
    获取月 K 线数据
    Args:
        code: 股票代码 sh.600519
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
        adjust: 复权 2=前复权
    Returns: 月 K 线 CSV
    """
    try:
        _login()
        rs = bs.query_history_k_data_plus(
            code,
            "date,code,open,high,low,close,volume,amount,pctChg",
            start_date=_norm_date(start_date), end_date=_norm_date(end_date),
            frequency="m", adjustflag=adjust
        )
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_all_stocks() -> str:
    """
    获取所有 A 股股票列表
    Returns: 证券代码及上市状态 CSV
    """
    try:
        _login()
        import datetime
        rows = []
        day = datetime.date.today()
        for _ in range(7):  # 当天非交易日时向前回退查找最近交易日
            rs = bs.query_all_stock(day=day.isoformat())
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rows:
                break
            day -= datetime.timedelta(days=1)
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_stock_basic(code: str) -> str:
    """
    获取股票基本信息
    Args:
        code: 股票代码 sh.600519
    Returns: 股票基本资料 JSON
    """
    try:
        _login()
        rs = bs.query_stock_basic(code)
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_industry() -> str:
    """
    获取行业分类
    Returns: 股票行业分类 CSV
    """
    try:
        _login()
        rs = bs.query_stock_industry()
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_trade_dates(start_date: str, end_date: str) -> str:
    """
    获取交易日历
    Args:
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
    Returns: 交易日列表 CSV
    """
    try:
        _login()
        rs = bs.query_trade_dates(start_date=_norm_date(start_date), end_date=_norm_date(end_date))
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_adjust_factor(code: str, start_date: str = "", end_date: str = "") -> str:
    """
    获取复权因子
    Args:
        code: 股票代码 sh.600519
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD
    Returns: 复权因子 CSV
    """
    try:
        _login()
        rs = bs.query_adjust_factor(code, start_date=_norm_date(start_date), end_date=_norm_date(end_date))
        _check(rs)
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


def _daily_batch(date: str, fn):
    """全市场批量接口统一逻辑：日期规范化 + 当天闭市前自动回退最近交易日，返回 (day, rows, fields)"""
    import datetime
    day = datetime.date.today() if not date else datetime.date.fromisoformat(_norm_date(date))
    rows = []
    rs = None
    for _ in range(7):  # 当天非交易日/闭市前为空时，向前回退查找最近已更新交易日
        rs = fn(day.isoformat())
        rows = []
        if rs.error_code == "0":
            while rs.next():
                rows.append(rs.get_row_data())
        if rows:
            break
        day -= datetime.timedelta(days=1)
    return day, rows, rs


@mcp.tool()
def get_daily_history_k_astock(date: str = "") -> str:
    """
    获取某日全市场A股日K线（一次返回全部A股）
    Args:
        date: 交易日，支持 YYYYMMDD 或 YYYY-MM-DD；为空取当天，闭市前自动回退最近已更新交易日
    Returns: 全市场A股日K CSV（17字段）。⚠️ 返回不复权原始价(adjustflag=3)，不含复权因子，与 qfq 库无关
    """
    try:
        _login()
        day, rows, rs = _daily_batch(date, bs.query_daily_history_k_AStock)
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_daily_history_k_etf(date: str = "") -> str:
    """
    获取某日全市场ETF日K线（一次返回全部ETF）
    Args:
        date: 交易日，支持 YYYYMMDD 或 YYYY-MM-DD；为空取当天，闭市前自动回退最近已更新交易日
    Returns: 全市场ETF日K CSV（17字段，估值字段为空）。⚠️ 返回不复权原始价(adjustflag=3)，不含复权因子，与 qfq 库无关
    """
    try:
        _login()
        day, rows, rs = _daily_batch(date, bs.query_daily_history_k_ETF)
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_daily_adjust_factor_batch(date: str = "") -> str:
    """
    获取某日全市场复权因子（code,dividOperateDate,foreAdjustFactor,backAdjustFactor,adjustFactor）
    Args:
        date: 交易日，支持 YYYYMMDD 或 YYYY-MM-DD；为空取当天，非交易日自动回退
    ⚠️⚠️⚠️ 复权口径警告 ⚠️⚠️⚠️
    baostock 使用【涨跌幅复权算法】，与通达信/新浪/腾讯/同花顺口径均不同！
    严禁用于修正 dragon_quant_qfq.db（该库=通达信本地日线×新浪qfq.js因子口径）。
    本接口因子仅作研究参考，若需复权行情请直接用 get_daily_history_k_astock 的 qfq 复权参数或既有 qfq 库。
    """
    try:
        _login()
        day, rows, rs = _daily_batch(date, bs.query_daily_adjust_factor)
        bs.logout()
        if not rows:
            return "No data"
        df = pd.DataFrame(rows, columns=list(rs.fields))
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()