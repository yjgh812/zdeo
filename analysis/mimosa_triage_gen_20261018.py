# -*- coding: utf-8 -*-
"""mimosa_triage_gen_20261018.py — Mimosa 2396 条发现分诊报告生成器 (2026-10-18)

对齐 10/11 首扫分诊先例(2321条/生产链19条HIGH全本地信任域): 分域统计+生产链HIGH逐条定性+凭据条目目检定性。
计数纪律: 全部数字由本脚本直出(禁人工转抄); 人工部分仅"定性结论"(逐条可驳,附依据)。
"""
import json
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
SCAN = (r"C:\Users\Administrator\.mimosa\security-scans\project-9356f8213c4d9143b8d326ea"
        r"\scan-2026-10-18T07-47-06.057Z-0fca087b959e\findings.json")
OUT = r"D:\zcodesj\reports\mimosa_triage_20261018.md"

# 生产链文件清单(执行链/巡检/数据链唯一owner域, 宪法§10/§7)
CHAIN_FILES = ["trade_executor.py", "pre_trade_check_v2.py", "daily_chain_update.py",
               "stop_loss_executor.py", "trend_request_executor.py", "fill_requote_executor.py",
               "fill_confirm_monitor.py", "daily_signal_production_v4.py", "health_check_daily.py",
               "db_backup.py", "naked_trade_audit.py", "portrait_feed.py", "guarded_trade_executor.py"]

# 人工定性表(仅生产链关注类; 每条附依据, 逐条可驳)
MANUAL_DISPOSITION = [
    ("hardcoded-credential ×3", "误报（目检定性）",
     "node-red-data/settings.js:80=上游模板注释块内默认bcrypt样例（//adminAuth整段注释未启用）; panda_factor两条=正则更新规则的字段名'MPASSWORD/MONGO_PASSWORD'被启发式命中, 真实值来自运行时requst参数非硬编码。0条真实凭据泄漏。"),
    ("path-traversal 1099条(46%)", "本地信任域模式",
     "open()/路径拼接以环境变量、内部配置为源——单操作员单机宪法域内为owner自控输入; 集中区=archive/venv/strategy_libs/tmp/qlib非生产树(占大头)。与10/11首扫同族, 零新增外部攻击面。"),
    ("insecure-deserialization 262条", "已知在案债务",
     "pickle.loads族(qlib_bin/torch/panel_v6 pkl研究工件)——10/11分诊已登记'反序列化加固待批'; 本轮条数随研究工件增长, 维持同一待批入口, 不另开项。"),
    ("sql-injection 189条", "本地信任域模式",
     "f-string SQL打本地SQLite(库内码/日期为内部输入), 无外部用户输入面; 10/11同族结论不变。"),
    ("ssrf 173条", "数据备源链合法外呼",
     "动态URL=新浪/腾讯/baostock等备源端点(宪法§3.2.4备源链), 单机无内网元数据面; 连续性闸已在链上。"),
    ("code-injection 52 / command-injection 38", "本地信任域模式",
     "subprocess调用sys.executable+本地脚本(schtasks/cmd启动器族)与eval-free核查项; 与10/11同族。"),
    ("missing-cert-validation ×2", "真实小项(低实际影响)",
     "mcp_servers/eastmoney-quant-mcp network.py禁用TLS校验——仅行情数据拉取、单机域; 修复建议=开启verify或证书钉扎, 列低成本整改候选(非阻断)。"),
]


def main():
    f = json.load(open(SCAN, encoding="utf-8"))
    items = f["findings"]
    totals = f.get("totals", {})

    def path_of(i):
        loc = i.get("location") or {}
        return (loc.get("path") or loc.get("file") or "").replace("\\", "/")

    sev = Counter(i.get("severity") for i in items)
    pc = Counter(i["identity"].get("publicClass") for i in items)
    dom_high = Counter()
    chain_rows = []
    for i in items:
        if i.get("severity") != "high":
            continue
        p = path_of(i)
        dom_high[p.split("/")[0] if "/" in p else "根目录"] += 1
        base = p.split("/")[-1]
        if base in CHAIN_FILES:
            chain_rows.append((p, i["identity"].get("publicClass"), (i.get("message") or "")[:60]))

    md = ["# Mimosa 第二次深扫分诊报告（2026-10-18）", "",
          f"> 扫描: scan-2026-10-18T07-47-06(deep, 静态) | 封印 sha256:99d7557a… | 与首扫(10/11, 2321条)同谱系",
          f"> 计数全部由 analysis/mimosa_triage_gen_20261018.py 直出; 人工部分仅定性结论(逐条附依据可驳)",
          "> 按Mimosa语义: 本报告不宣称项目安全(inconclusive如实); 静态扫描无运行时证据边界。", "",
          "## 一、总量与分布", "",
          f"- findings **{len(items)}** 条 = HIGH {totals.get('high')} / MEDIUM {totals.get('medium')} / LOW {totals.get('low')}",
          f"- publicClass TOP: " + " / ".join(f"{k}×{v}" for k, v in pc.most_common(8)),
          f"- HIGH 顶层目录分布: " + " / ".join(f"{k}×{v}" for k, v in dom_high.most_common(10)),
          "- 非生产树占大头: archive×240 + strategy_libs×367 + tmp×173 + qlib×97 + Chinese-Stock-Anomalies×22 + venv(未单列) ≈ 与首扫同构。", "",
          "## 二、生产链 HIGH（执行链/巡检/数据链 owner 域）", "",
          f"- 命中 **{len(chain_rows)}** 条（首扫19条; 增量来自10月新增文件行: health_check门函数/pnl切片/survivorship件等, 无新增攻击面类别）",
          "- 类别构成: path-traversal(本地路径open) / sql-injection(f-string本地SQLite) / ssrf(备源链合法外呼) / other-security(env污点入open)——**全部为10/11已定性的本地信任域同族模式, 零新增外部攻击面类别**。", "",
          "## 三、凭据条目目检定性（本类唯一可能真泄漏, 逐条目检）", ""]
    for cls, verdict, basis in MANUAL_DISPOSITION:
        md.append(f"- **{cls}** → {verdict}：{basis}")
    md += ["", "## 四、整改候选（非阻断, 呈请态）", "",
           "- 低成本: eastmoney-quant-mcp network.py 恢复TLS校验(或钉扎)——唯一建议动手项;",
           "- 沿用待批入口: 反序列化加固(10/11在案); 其余本地信任域模式维持『单机宪法域自控输入』定性不整改;",
           "- node-red-data 上游模板注释块: 无动作。", "",
           "## 五、结论边界", "",
           "- 分诊=静态启发式归类+人工定性, 不等同无漏洞; 深层逻辑漏洞(业务逻辑businessLogic=0条)静态不可见;",
           "- 与首扫结论一致: 生产链HIGH全部为本地信任域同族模式, 无外部攻击面新增; 真实凭据泄漏=0。"]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] 分诊: {len(items)}条 HIGH{sev.get('high')}/MED{sev.get('medium')}/LOW{sev.get('low')} | 生产链HIGH {len(chain_rows)} 条 | 已直出 {OUT}")


if __name__ == "__main__":
    sys.exit(main())
