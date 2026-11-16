#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
financial_quick_read.py — 个股财报速读（纯呈现层，零交易权）

定位：输入个股，一分钟内输出财报核心指标一页纸 + 多期趋势 + 七维异常检测 + 同业对标，
给出规则化财务健康评分。源自外部「财报速读」skill 的呈现框架移植
（2026-10-19 主人批准的能力移植件），数据走东财 datacenter/F10 直连（与 lhb_update.py
同源同模式，免 token），同业对标走东财全市场业绩报表 + 其自带东财行业分组。

数据源（全部 HTTP 直连只读，免 token）：
  1. RPT_F10_FINANCE_MAINFINADATA  个股多期主要指标（营收/净利/同比/ROE/毛利率/净利率/负债率）
  2. emweb F10 zcfzbAjaxNew        资产负债表（货币资金/应收/存货/商誉/短借/总资产）
  3. emweb F10 xjllbAjaxNew        现金流量表（经营/投资/筹资净额）
  4. RPT_LICO_FN_CPD               全市场当期业绩（按其自带东财行业 PUBLISHNAME 分组取中位数对标）
  5. stock_name_lookup             名称唯一合法接口（代码必配名称铁律）

异常检测七维（6 可测 + 关联交易无本地源恒 N/A）：均为规则阈值呈现提示，非确定性结论。

评分 A-F（呈现框架，阈值沿用外部 skill，未做本系统统计验证，不构成信号）：
  A=双增且现金流为正零异常 B=稳健(≤1关注) C=2项关注 D=存在高危项
  F=连续两期亏损

用法：
  python financial_quick_read.py --code 600519 [--periods 8] [--no-peer]
退出码：0=成功；2=名称查无/财务数据缺失
"""
import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import get_name  # noqa: E402

REPORT_DIR = Path(r"D:\zcodesj\reports")
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
DISCLAIMER = (
    "> ⚠️ 纯呈现零交易权：本报告仅为财报数据速读，不触发任何交易动作；异常检测与评分"
    "为规则化呈现框架（阈值沿用外部「财报速读」skill，未做本系统统计验证），不构成信号"
    "或投资建议。财报为历史数据不代表未来；金融股指标解读异于一般企业。"
)

MAIN_COLS = ("TOTALOPERATEREVE,TOTALOPERATEREVETZ,PARENTNETPROFIT,PARENTNETPROFITTZ,"
             "ROEJQ,XSMLL,XSJLL,ZCFZL,MGJYXJJE,REPORT_DATE,SECUCODE,SECURITY_NAME_ABBR")


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":          # 服务器间歇返回 gzip, 兼容解压
        import gzip
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8", "ignore"))


def em_datacenter(report, filt, cols="ALL", pagesize=500, pageno=1, sort="REPORT_DATE"):
    q = urllib.parse.quote(filt)
    url = (f"https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={report}"
           f"&columns={cols}&filter={q}&pageSize={pagesize}&pageNumber={pageno}"
           f"&sortColumns={sort}&sortTypes=-1&source=WEB&client=WEB")
    d = _get(url)
    res = d.get("result") or {}
    return res.get("data") or [], res.get("count") or 0


def prev_period(report_date: str) -> str:
    """'2026-06-30' → 上一报告期 '2026-03-31'（自然季度回退）"""
    y, m, _ = (int(x) for x in report_date.split("-"))
    m_prev = m - 3
    if m_prev <= 0:
        return f"{y - 1}-12-31"
    return f"{y}-{m_prev:02d}-30" if m_prev in (6, 9) else f"{y}-{m_prev:02d}-31"


def fetch_balance_cf(secucode: str, dates: list):
    """F10 资产负债表/现金流量表; companyType 因公司而异(4普通/3银行/2保险/1券商), 依次试。"""
    out = {}
    for tbl in ("zcfzbAjaxNew", "xjllbAjaxNew"):
        for ct in (4, 3, 2, 1):
            ds = urllib.parse.quote(",".join(dates))
            url = (f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/{tbl}"
                   f"?companyType={ct}&reportDateType=0&reportType=1&dates={ds}&code={secucode}")
            try:
                rows = _get(url, timeout=20).get("data") or []
            except Exception:
                rows = []
            if rows:
                out[tbl] = rows
                break
    return out


def fetch_peer(industry_of_stock_hint, report_date):
    """全市场当期业绩, 返回 (该股行, {行业名: {指标: 中位数}})。分页拉全(≤30页)。"""
    rd = report_date.split(" ")[0]
    rows = []
    total = None
    page = 1
    while True:
        data, total = em_datacenter(
            "RPT_LICO_FN_CPD", f"(REPORTDATE='{rd}')",
            cols=("SECUCODE,SECURITY_NAME_ABBR,PUBLISHNAME,TOTAL_OPERATE_INCOME,"
                  "PARENT_NETPROFIT,YSTZ,SJLTZ,XSMLL,WEIGHTAVG_ROE"),
            pagesize=500, pageno=page, sort="SECUCODE")
        rows.extend(data)
        if not data or len(rows) >= min(total, 15000) or page >= 30:
            break
        page += 1
    buckets = {}
    stock_industry = None
    for r in rows:
        ind = r.get("PUBLISHNAME") or "未分类"
        if r.get("SECUCODE") == _peer_self:
            stock_industry = ind
        buckets.setdefault(ind, []).append(r)
    med = {}
    for ind, rs in buckets.items():
        row_m = {}
        for f in ("YSTZ", "SJLTZ", "XSMLL", "WEIGHTAVG_ROE"):
            vals = [r.get(f) for r in rs if isinstance(r.get(f), (int, float))]
            if vals:
                row_m[f] = statistics.median(vals)
        med[ind] = {"n": len(rs), **row_m}
    return stock_industry, med


_peer_self = None


def num(v):
    return v if isinstance(v, (int, float)) else None


def pct_s(v, nd=2, suffix="%"):
    return "N/A" if v is None else f"{v:.{nd}f}{suffix}"


def yi(v):
    if v is None:
        return "N/A"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:.1f}万"
    return f"{v:.0f}"


def main():
    global _peer_self
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="6位股票代码, 如 600519")
    ap.add_argument("--periods", type=int, default=8, help="主要指标期数, 默认8")
    ap.add_argument("--no-peer", action="store_true", help="跳过同业对标(快速模式)")
    args = ap.parse_args()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    code = args.code.strip()
    if not (code.isdigit() and len(code) == 6):
        print(f"[FAIL] --code 须为6位数字, 收到 {code!r}")
        sys.exit(2)
    name = get_name(code)
    if not name:
        print(f"[FAIL] 名称接口查无 {code}, 禁止输出")
        sys.exit(2)
    secucode = code + (".SH" if code.startswith(("6", "9")) else ".SZ" if code.startswith(("0", "2", "3")) else ".BJ")

    # 1) 多期主要指标
    main_rows, _ = em_datacenter(
        "RPT_F10_FINANCE_MAINFINADATA", f'(SECUCODE="{secucode}")',
        cols=MAIN_COLS, pagesize=max(args.periods, 4))
    if not main_rows:
        print(f"[N/A] {name}({code}) 东财F10无财务主要指标数据")
        sys.exit(2)
    cur = main_rows[0]
    report_date = (cur.get("REPORT_DATE") or "")[:10]
    _peer_self = secucode

    # 2) 资产负债表 + 现金流量表（当期+上期）
    dates = [report_date, prev_period(report_date)]
    bcf = fetch_balance_cf(secucode, dates)
    bs = (bcf.get("zcfzbAjaxNew") or [{}])
    cf = (bcf.get("xjllbAjaxNew") or [{}])
    b0, b1 = (bs[0] if bs else {}), (bs[1] if len(bs) > 1 else {})
    c0 = cf[0] if cf else {}

    rev = num(cur.get("TOTALOPERATEREVE"))
    rev_tz = num(cur.get("TOTALOPERATEREVETZ"))
    npf = num(cur.get("PARENTNETPROFIT"))
    npf_tz = num(cur.get("PARENTNETPROFITTZ"))
    roe = num(cur.get("ROEJQ"))
    gpm = num(cur.get("XSMLL"))
    npm = num(cur.get("XSJLL"))
    debt = num(cur.get("ZCFZL"))
    ocf = num(c0.get("NETCASH_OPERATE"))
    cash = num(b0.get("MONETARYFUNDS"))
    ar_now, ar_prev = num(b0.get("ACCOUNTS_RECE")), num(b1.get("ACCOUNTS_RECE"))
    inv_now, inv_prev = num(b0.get("INVENTORY")), num(b1.get("INVENTORY"))
    gw = num(b0.get("GOODWILL"))
    ta = num(b0.get("TOTAL_ASSETS"))
    eq = num(b0.get("TOTAL_EQUITY"))
    st_loan = num(b0.get("SHORT_LOAN"))
    npf_prev = num(main_rows[1].get("PARENTNETPROFIT")) if len(main_rows) > 1 else None
    ocf_ratio = (ocf / npf) if (ocf is not None and npf not in (None, 0)) else None

    # 3) 七维异常检测（6可测+1恒N/A）
    flags = []   # (级别, 维度, 说明)  级别: 高危/关注
    if rev_tz is not None and npf_tz is not None and rev_tz > 0 and npf_tz < 0:
        flags.append(("关注", "营收利润背离", f"营收同比+{rev_tz:.1f}% 但归母净利同比{npf_tz:.1f}%"))
    if npf is not None and npf > 0 and ocf is not None and ocf < 0:
        flags.append(("高危", "经营现金流", f"归母净利{yi(npf)}为正但经营现金流{yi(ocf)}为负, 盈利质量存疑"))
    if ar_now is not None and ar_prev not in (None, 0) and rev_tz is not None:
        ar_yoy = (ar_now / ar_prev - 1) * 100
        if ar_yoy > rev_tz + 10:
            flags.append(("关注", "应收激增", f"应收同比+{ar_yoy:.1f}% > 营收同比+{rev_tz:.1f}%+10pp"))
    if inv_now is not None and inv_prev not in (None, 0) and rev_tz is not None:
        inv_yoy = (inv_now / inv_prev - 1) * 100
        if inv_yoy > rev_tz + 20:
            flags.append(("关注", "存货积压", f"存货同比+{inv_yoy:.1f}% 显著高于营收同比+{rev_tz:.1f}%"))
    if gw is not None and ta:
        gwr = gw / ta
        if gwr > 0.3:
            flags.append(("高危", "商誉", f"商誉{yi(gw)}占总资产{pct_s(gwr * 100, 1)}>30% 减值风险高"))
        elif gwr > 0.1:
            flags.append(("关注", "商誉", f"商誉{yi(gw)}占总资产{pct_s(gwr * 100, 1)}>10% 需跟踪"))
    if cash is not None and ta and st_loan is not None and eq:
        if cash / ta > 0.3 and st_loan / eq > 0.3:
            flags.append(("关注", "资金异象", f"货币资金充裕({pct_s(cash / ta * 100, 0)}总资产)同时大额短借"
                                          f"({pct_s(st_loan / eq * 100, 0)}净资产), 存贷双高形态"))
    flags.append(("N/A", "关联交易", "本地无关联交易数据源, 不做推断"))

    # 4) 规则化评分
    n_high = sum(1 for lv, _, _ in flags if lv == "高危")
    n_watch = sum(1 for lv, _, _ in flags if lv == "关注")
    if npf is not None and npf < 0 and npf_prev is not None and npf_prev < 0:
        score, s_desc = "F", "连续两期归母净利为负"
    elif n_high >= 1:
        score, s_desc = "D", f"存在{n_high}项高危异常信号"
    elif n_watch >= 2:
        score, s_desc = "C", f"{n_watch}项关注指标需持续跟踪"
    elif n_watch == 1:
        score, s_desc = "B", "整体稳健, 单项指标需关注"
    elif npf is not None and npf > 0 and ocf is not None and ocf > 0:
        score, s_desc = "A", "盈利为正现金流充裕且未见异常信号"
    else:
        score, s_desc = "B", "未触发异常阈值"

    # 5) 同业对标
    peer_lines = []
    if not args.no_peer:
        try:
            stock_ind, med = fetch_peer(True, report_date)
            if stock_ind and stock_ind in med:
                m = med[stock_ind]
                peer_lines.append(f"- 所属行业(东财口径)：{stock_ind}（对标样本 {m['n']} 家, 报告期 {report_date}）")
                for f, cn_name, mine in (("YSTZ", "营收同比", rev_tz), ("SJLTZ", "净利同比", npf_tz),
                                          ("XSMLL", "毛利率", gpm), ("WEIGHTAVG_ROE", "ROE加权", roe)):
                    mv = m.get(f)
                    peer_lines.append(f"  - {cn_name}: 本股 {pct_s(mine)} vs 行业中位 {pct_s(mv)}"
                                      + (f"（差 {mine - mv:+.1f}pp）" if (mine is not None and mv is not None) else ""))
            else:
                peer_lines.append("- 同业对标：行业中位数据缺失, N/A")
        except Exception as e:
            peer_lines.append(f"- 同业对标：获取失败（{type(e).__name__}）, N/A")
    else:
        peer_lines.append("- 同业对标：--no-peer 跳过")

    L = []
    L.append(f"# 财报速读 · {name}({code}) [报告期 {report_date}]")
    L.append("")
    L.append(DISCLAIMER)
    L.append("")
    L.append("## 核心指标一页纸")
    L.append("|指标|数值|同比|")
    L.append("|---|---|---|")
    L.append(f"|营业总收入|{yi(rev)}|{pct_s(rev_tz)}|")
    L.append(f"|归母净利润|{yi(npf)}|{pct_s(npf_tz)}|")
    L.append(f"|毛利率|{pct_s(gpm)}|-|")
    L.append(f"|净利率|{pct_s(npm)}|-|")
    L.append(f"|ROE(加权)|{pct_s(roe)}|-|")
    L.append(f"|资产负债率|{pct_s(debt)}|-|")
    L.append(f"|经营现金流净额|{yi(ocf)}|现金流/净利 {f'{ocf_ratio:.2f}倍' if ocf_ratio is not None else 'N/A'}|")
    L.append("")
    trend = " | ".join(
        f"{(r.get('REPORT_DATE') or '')[:7]}:净利同比{pct_s(num(r.get('PARENTNETPROFITTZ')), 1)}"
        for r in main_rows[: min(args.periods, 8)])
    L.append(f"## 近期净利同比趋势\n{trend}")
    L.append("")
    L.append("## 财务异常检测")
    if n_high == 0 and n_watch == 0:
        L.append("✅ 六项可测维度未触发异常阈值")
    for lv, dim, desc in flags:
        if lv == "高危":
            L.append(f"- ⚠️⚠️⚠️ [高危] {dim}：{desc}")
        elif lv == "关注":
            L.append(f"- ⚠️ [关注] {dim}：{desc}")
        else:
            L.append(f"- ➖ {dim}：{desc}")
    L.append("")
    L.append("## 同业对标")
    L.extend(peer_lines)
    L.append("")
    L.append(f"## 综合财务健康评分：**{score}**")
    L.append(f"{s_desc}（规则化呈现框架，非统计验证信号）")
    L.append("")

    out = "\n".join(L)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORT_DIR / f"FIN_QUICK_READ_{code}_{report_date.replace('-', '')}.md"
    rp.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] 报告已写入 {rp}")


if __name__ == "__main__":
    main()
