#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_wfa_gate.py — 因子组合 Walk-Forward 验证门禁流水线 (V1)
================================================================
定位：屠龙生产策略/因子上线前的统一滚动样本外验证门禁。
  复用 factor_combo_walkforward 的 WFA 内核（PIT 无前视 + IC 通道 + 月度组合模拟，
  口径与生产 bp_ep 完全一致），在其上叠加 4 个门禁要素：
    ① 门禁裁决 PASS / WARN / FAIL（基于"拼接全部 OOS 的组合绩效"）
    ② 一票制协议指纹（同一协议已裁决后禁止无变更重跑，防"看 OOS 调参重跑"）
    ③ 试验次数记账（wfa_trials.jsonl：指纹 + 配置 + 等效试验数 N + 裁决）
    ④ 辝界规则履行自检（禁止/允许清单逐条报告）

  ⚠️ 宇宙口径注记(2026-10-23 审计, reports/AUDIT_EXTERNAL_TDX_PIPELINE_20261023.md §八):
     OOS 组合模拟沿用的 ST 剔除集为 stock_info 当前快照(_run_window_portfolios 载入一次,
     OOS 段内逐调仓日套用)——「终将 ST 的票」被从其健康历史段整体剔除, 方向=抬高 OOS
     收益=系统性利好候选。由此: FAIL 裁决 a fortiori 有效; PASS/PASS_candidate 裁决数字
     为上界, 须待历史 ST 时点源(审计报告§七, 数据源权限门控)复核后方可引为接线依据。

边界规则（WFA 边界规则真源，详见 kb/rules/wfa-boundary-rules.md）：
  [禁止] B1 把 OOS 并入 IS 重训（引擎保证隔离）
         B2 只报告表现最好的窗口（报告强制披露全部窗口 + 拼接 OOS 为唯一发布口径）
         B3 不记录试验次数 N 就宣称"验证通过"（记账强制）
         B4 同一协议下看了 OOS 结果后调参重跑（一票制指纹拦截，--force 记 violation）
         B5 改窗口/步长/OOS 长度等协议参数直到结果好看（协议漂移检测提示）
  [允许] A1 OOS 失败后记录并归因（regime/行业/波动率）——归因是诊断不是调参
         A2 修改规则后，用"从未看过的全新 hold-out 段"重新验证
         A3 同一协议下运行多个变体做对比，但全部记入试验次数 N
         A4 FAIL 时进入观察期/降仓/冷却，不因失败一次性否定，也不静默放行

用法:
  python tulong_wfa_gate.py --combo bp_ep
  python tulong_wfa_gate.py --combo droe roa --train 500 --test 60 --cost 0.001
  python tulong_wfa_gate.py --combo bp_ep --force     # 一票制拦截时显式强制重跑（记 violation）

输出:
  reports/wfa_gate_<combo>_<YYYYMMDD>.md    门禁报告（裁决 + 逐窗 + 边界规则履行）
  reports/wfa_gate_<combo>_<YYYYMMDD>.json  结构化结果
  data/wfa_trials.jsonl                     试验记账（一票制指纹 + N）

口径铁律（继承生产）:
  - 财务因子 = factor_pit.db 法定披露截止 PIT 口径（无前视）
  - 价格 = stock_daily_meta 原始收盘（与 factor_combo_walkforward 生产口径一致）
  - 无 ST / 价格 ≤30 元 / Top10% 月度换仓 / 单边成本 cost
  - 交易纪律：信号只用 ≤T 数据，成交在下一调仓日，无 look-ahead
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from factor_combo_walkforward import (  # noqa: E402  生产 WFA 内核（同目录复用）
    compute_ic_series,
    cs_rank_z,
    load_pit_panels,
    load_price_pivot,
    month_starts,
    perf_stats,
    simulate_portfolio,
    walk_forward,
    weights_from_train,
)

# ─────────────────────────────────────────────────────────────
# 门禁常量（V1 阈值，可后续按规则评审调整，调整即改协议指纹）
# ─────────────────────────────────────────────────────────────
ENGINE_VERSION = "tulong_wfa_gate.v1"
MIN_FOLDS = 5                 # 折数不足 → 样本不足
MIN_OOS_MONTHS = 12           # 整体 OOS 至少 12 个月
POS_WINDOW_PASS = 0.60        # PASS 所需正收益窗口占比
POS_WINDOW_WARN = 0.50        # 低于此 → FAIL
MDD_PASS = -0.25              # PASS 所需 OOS 最大回撤（优于 -25%）
MDD_FAIL = -0.35              # 劣于 -35% → FAIL
TRIALS_LOG = ROOT / "data" / "wfa_trials.jsonl"
PREREG_LOG = ROOT / "data" / "wfa_preregistrations.jsonl"
REPORTS_DIR = ROOT / "reports"
STALE_LIMIT = 60              # 组合打分允许的最大截面过期天数
MAX_LOOKBACK = 250            # 打分取 ≤date 最近因子截面的最大回看


def protocol_fingerprint(combo, train, test, fwd, cost, start, end, top_pct, price_source,
                         cpcv=False):
    """一票制协议指纹：配置 + 数据范围 + 引擎版本 的确定性 hash。
    普通模式（cpcv=False）payload 与 V1 完全一致，保证一票制持续有效；
    --cpcv 开启时是新协议（新增 cpcv 标志）→ 新指纹。"""
    payload = {
        "engine": ENGINE_VERSION,
        "combo": list(combo),
        "train": int(train), "test": int(test), "fwd": int(fwd),
        "cost": float(cost), "top_pct": float(top_pct),
        "start": str(start), "end": str(end), "price_source": price_source,
    }
    if cpcv:
        payload["cpcv"] = True
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def load_trials():
    recs = []
    if TRIALS_LOG.exists():
        with open(TRIALS_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return recs


def append_trial(rec: dict):
    TRIALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def check_one_vote(fp: str, force: bool) -> tuple[str, dict | None]:
    """一票制检查：同指纹是否已裁决。已裁决且非 --force → 拦截。
    返回 (status, last_rec)；status ∈ {"ok","blocked","force"}。"""
    last = None
    for rec in load_trials():
        if rec.get("fingerprint") == fp:
            last = rec
    if last is None:
        return "ok", None
    if last.get("verdict") in ("PASS", "WARN", "FAIL"):
        if force:
            return "force", last
        return "blocked", last
    return "ok", last  # 上次 RUNNING/中断


# ═══════════════════════════════════════════════════════════
#  研究预注册（P2-11）：正式验证前先声明协议，防 p-hacking
# ═══════════════════════════════════════════════════════════
def load_prereg(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_prereg(p: dict, combo, params, engine) -> list[str]:
    """校验预注册声明与本次运行一致；任一不一致 → fail-closed 拒绝。"""
    errs: list[str] = []
    if p.get("combo") != list(combo):
        errs.append(f"combo 不一致（预注册 {p.get('combo')} vs 本次 {list(combo)}）")
    pp = p.get("params", {})
    for k in ("train", "test", "fwd", "cost", "top_pct", "start", "end"):
        if k in pp and pp.get(k) != params.get(k):
            errs.append(f"{k} 不一致（预注册 {pp.get(k)} vs 本次 {params.get(k)}）")
    if p.get("engine") != engine:
        errs.append(f"engine 不一致（预注册 {p.get('engine')} vs 本次 {engine}）")
    if p.get("status") == "completed":
        errs.append("该预注册已 completed，不得复用（一票制语义）")
    return errs


def complete_prereg(path, p: dict, verdict, oos, pbo, cpcv):
    """运行完成后回填预注册文件 + 追加预注册日志（承诺 vs 实际闭环）。"""
    p["status"] = "completed"
    p["result"] = {"verdict": verdict, "oos": oos, "pbo": pbo, "cpcv": cpcv,
                   "ts": datetime.now().isoformat(timespec="seconds")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)
    PREREG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(PREREG_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(p, ensure_ascii=False) + "\n")


# ═══════════════════════════════════════════════════════════
#  门禁对象
# ═══════════════════════════════════════════════════════════
class WfaGate:
    def __init__(self, args):
        self.a = args
        self.combo = list(args.combo)
        self.fp = protocol_fingerprint(
            self.combo, args.train, args.test, args.fwd, args.cost,
            args.start, args.end, args.top_pct, args.price_source,
            cpcv=getattr(args, "cpcv", False))

    def params_dict(self) -> dict:
        return {"train": self.a.train, "test": self.a.test, "fwd": self.a.fwd,
                "cost": self.a.cost, "top_pct": self.a.top_pct,
                "start": self.a.start, "end": self.a.end}

    # ── 载入数据（复用生产口径） ──
    def load(self):
        t0 = time.time()
        print(f"[1/6] 载入价格 {self.a.start}~{self.a.end} ...", flush=True)
        self.prices = load_price_pivot(self.a.start, self.a.end)
        print(f"      价格 {self.prices.shape} ({time.time()-t0:.0f}s)")
        t0 = time.time()
        print("[2/6] 载入 PIT 因子面板 ...", flush=True)
        self.pit = load_pit_panels(self.prices.index, self.prices)
        self.names = sorted(set(self.combo) & set(self.pit.keys()))
        miss = set(self.combo) - set(self.pit.keys())
        if miss:
            print(f"      ⚠️ PIT 通道不支持 {sorted(miss)}（可用: {sorted(self.pit.keys())}）")
        if not self.names:
            raise SystemExit("无可用因子，退出")
        print(f"      PIT 因子 {self.names} ({time.time()-t0:.0f}s)")

    # ── 构建 z 面板 + IC 序列 ──
    def build_scores(self):
        print("[3/6] 构建截面 z 面板与 IC 序列 ...", flush=True)
        t0 = time.time()
        self.z_panels = {}
        self.ic_series = {}
        fwd = (self.prices.shift(-self.a.fwd) / self.prices - 1).stack()
        fwd.index.names = ["date", "asset"]
        for name in self.names:
            s = self.pit[name]
            z = cs_rank_z(s).dropna()
            self.z_panels[name] = z
            self.ic_series[name] = compute_ic_series(z, fwd)
        self.combo_ics = pd.DataFrame({k: self.ic_series[k] for k in self.names}).dropna()
        self.z_dates = {name: self.z_panels[name].index.get_level_values("date").unique().sort_values()
                        for name in self.names}
        # 全市场等权日收益（regime proxy，无前视：仅用当日截面均值）
        self.market_ret = (self.prices.pct_change().mean(axis=1)
                           .replace([np.inf, -np.inf], np.nan).ffill())
        print(f"      IC 对齐 {len(self.combo_ics)} 天 ({time.time()-t0:.0f}s)")

    # ── 每折 OOS 组合模拟（训练段权重 → OOS 段月度组合） ──
    def _latest_z(self, name, date):
        avail = self.z_dates[name]
        pos = avail.searchsorted(date, side="right") - 1
        if pos < 0:
            return None, 10 ** 9
        zdate = avail[pos]
        stale = int(self.prices.index.searchsorted(date) - self.prices.index.searchsorted(zdate))
        if stale > MAX_LOOKBACK:
            return None, stale
        return self.z_panels[name].xs(zdate, level="date"), stale

    def _make_score_fn(self, w: np.ndarray, st_codes_fn):
        """用固定训练权重 w 构造打分函数（OOS 段内逐调仓日调用，严格 ≤date）。"""
        names = self.names

        def score_fn(date):
            st_codes = st_codes_fn(int(pd.Timestamp(date).strftime("%Y%m%d")))
            parts, max_stale = [], 0
            for i, name in enumerate(names):
                zs, stale = self._latest_z(name, date)
                if zs is None:
                    return None, stale
                max_stale = max(max_stale, stale)
                parts.append(zs * w[i])
            score = sum(parts).dropna()
            day_prices = self.prices.loc[date] if date in self.prices.index else None
            if day_prices is not None:
                valid = [c for c in score.index
                         if c not in st_codes and c in day_prices.index
                         and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
                score = score[valid]
            return score, max_stale
        return score_fn

    def run_wfa(self, train, test):
        print(f"[4/6] Walk-Forward（train={train} test={test} fwd={self.a.fwd}）...")
        t0 = time.time()
        wf = walk_forward(self.combo_ics, train, test, schemes=("icir", "equal", "opt"))
        self.wf = wf
        print(f"      折数 {wf['n_folds']} ({time.time()-t0:.0f}s)")
        for s, st_ in wf["oos_summary"].items():
            print(f"      {s:<6} OOS IC={st_['mean_ic']:+.4f} ICIR={st_['icir']:+.3f} 正率={st_['pos_rate']:.0%}")

    def _run_window_portfolios(self, train):
        """每折 OOS 月度组合模拟（独立初始资金），返回 (windows, 拼接OOS净值)。"""
        print(f"[5/6] 每折 OOS 月度组合模拟（独立初始资金，train={train}）...")
        # m4 (PD-049): ST 剔除改 per-date 时点集（st_codes_at），消除快照套历史窗前视
        from factor_combo_walkforward import st_codes_at as _st_at
        windows = []
        navs_all = []          # 拼接整体 OOS（连续复利）
        cum = 1.0
        for fold in self.wf["folds"]:
            tr_end = pd.Timestamp(fold["train"][1])
            te_start = pd.Timestamp(fold["test"][0])
            te_end = pd.Timestamp(fold["test"][1])
            # 训练段 IC 历史 → icir 权重（该折只寻优一次）
            hist = self.combo_ics[self.combo_ics.index <= tr_end].tail(train)
            if len(hist) < 120:
                continue
            w = weights_from_train(hist, "icir")
            # OOS 段价格子集 + 月度调仓日
            prices_te = self.prices.loc[(self.prices.index >= te_start) & (self.prices.index <= te_end)]
            if len(prices_te) < 20:
                continue
            reb_te = month_starts(prices_te.index, from_date=te_start)
            score_fn = self._make_score_fn(w, _st_at)
            nav_te = simulate_portfolio(score_fn, prices_te, reb_te,
                                        top_pct=self.a.top_pct, cost=self.a.cost,
                                        max_stale_days=STALE_LIMIT)
            if len(nav_te) < 20:
                continue
            # 区间绩效（独立资金）
            stats = perf_stats(nav_te["nav"], f"fold{fold['fold']}")
            pos_months = (nav_te["nav"].resample("ME").last().pct_change().dropna() > 0).mean()
            # regime 标注（全市场等权 proxy）：区间涨跌 + 年化波动率 + 分类
            mret = self.market_ret.loc[(self.market_ret.index >= te_start)
                                       & (self.market_ret.index <= te_end)].dropna()
            mk_ret = float(mret.sum()) if len(mret) else 0.0
            mk_vol = float(mret.std() * np.sqrt(243)) if len(mret) > 5 else 0.0
            regime = "up" if mk_ret > 0.02 else ("down" if mk_ret < -0.02 else "flat")
            excess = float(stats["total"]) - mk_ret
            windows.append({
                "fold": fold["fold"],
                "train": fold["train"], "test": fold["test"],
                "oos_total": stats["total"], "oos_mdd": stats["mdd"],
                "oos_pos_month_rate": round(float(pos_months) if not np.isnan(pos_months) else 0.0, 4),
                "w_icir": fold.get("icir", {}),
                "mk_ret": round(mk_ret, 4), "mk_vol": round(mk_vol, 4),
                "regime": regime, "excess": round(excess, 4),
            })
            # 拼接整体 OOS（连续复利：下一折从上一折末值继续）
            seg = nav_te["nav"] / nav_te["nav"].iloc[0] * cum
            navs_all.append(seg)
            cum = float(seg.iloc[-1])
            print(f"      fold{fold['fold']} {te_start.date()}~{te_end.date()} "
                  f"区间={stats['total']:+.2%} 回撤={stats['mdd']:+.2%} 正月率={pos_months:.0%}")

        if not navs_all:
            raise SystemExit("无有效 OOS 窗口，退出")
        oos_all = pd.concat(navs_all)
        oos_all = oos_all[~oos_all.index.duplicated(keep="first")].sort_index()
        return windows, oos_all

    def _run_protocol(self, train, test):
        """跑一个完整协议（WFA + 每折组合）。只返回结果，不写 self 业务快照
        （PBO 扫描会多次调用，避免污染主协议快照）。"""
        self.run_wfa(train, test)   # 更新 self.wf（扫描内部需要）
        windows, oos_all = self._run_window_portfolios(train)
        stats_all = perf_stats(oos_all, "OOS拼接")
        return {"windows": windows, "oos_all": oos_all, "stats_all": stats_all,
                "wf": self.wf, "n_folds": self.wf["n_folds"]}

    def _run_pbo_scan(self):
        """PBO 深度扫描（--pbo）：在协议参数邻域生成变体矩阵 → CSCV/PBO。
        轨道 = 各参数变体的拼接 OOS 月收益；PBO<0.25 稳健 / 0.25~0.5 存疑 / >0.5 过拟合。"""
        from backtest_pbo_check import cscv_pbo
        print("[6/6] PBO 深度扫描（参数邻域变体矩阵，可耗时数分钟）...")
        train_base, test_base = self.a.train, self.a.test
        trains = sorted({max(300, train_base + d) for d in (-100, 0, 100)})
        tests = sorted({max(30, test_base + d) for d in (-20, 0, 20)})
        cols: dict[str, pd.Series] = {}
        var_perf: dict[str, dict] = {}
        for tr in trains:
            for te in tests:
                key = f"tr{tr}_te{te}"
                print(f"    [PBO] {key} ...", flush=True)
                r1 = self._run_protocol(tr, te)
                var_perf[key] = {"cagr": r1["stats_all"]["cagr"], "mdd": r1["stats_all"]["mdd"]}
                cols[key] = r1["oos_all"].resample("ME").last().pct_change().dropna()
        # 按共同月份对齐（交集），保证矩阵无 NaN
        mat = pd.DataFrame(cols)
        mat = mat.dropna(axis=0)
        if mat.shape[1] < 2 or mat.shape[0] < 12:
            print(f"    [PBO] 轨道数 {mat.shape[1]} / 月数 {mat.shape[0]} 不足，跳过 PBO")
            return None
        splits = 6 if mat.shape[0] >= 24 else 4
        try:
            res = cscv_pbo(mat, splits=splits, metric="sharpe", verbose=False)
        except Exception as e:
            print(f"    [PBO] 计算失败: {e}")
            return None
        q = np.percentile(res["lambdas"], [25, 50, 75])
        pbo_out = {
            "pbo": round(float(res["pbo"]), 4),
            "n_tracks": int(mat.shape[1]), "n_months": int(mat.shape[0]),
            "splits": splits, "n_combos": int(res["n_combos"]),
            "lambda_p25_p50_p75": [round(float(x), 3) for x in q],
            "tracks": sorted(trains), "tests": sorted(tests),
            "var_perf": var_perf,
        }
        print(f"    [PBO] N={pbo_out['n_tracks']} T={pbo_out['n_months']} "
              f"S={splits} → PBO={pbo_out['pbo']:.4f}")
        return pbo_out

    # ── 门禁裁决 ──
    def _cpcv_scan(self):
        """CPCV（组合化交叉验证，López de Prado AFML ch12.4）：
        把 IC 序列按时间切 N 块，遍历 C(N,N/2)/2 种训练/测试块组合，每条组合=一条 OOS
        IC 路径，聚合多路径分布——补充 WFA 单路径的稳健性证据（Arian 2024 实证 CPCV
        防假发现优于 WFA）。快速（纯向量，IC 层面），不跑组合模拟。"""
        import itertools
        n_splits = self.a.cpcv_splits
        ics = self.combo_ics
        n = len(ics)
        if n < n_splits * 60:
            print("    [CPCV] IC 序列过短，跳过")
            return None
        edges = np.linspace(0, n, n_splits + 1).astype(int)
        blocks = [ics.iloc[edges[i]:edges[i + 1]] for i in range(n_splits)]
        combos = [c for c in itertools.combinations(range(n_splits), n_splits // 2)
                  if c < tuple(sorted(set(range(n_splits)) - set(c)))]
        paths = []
        for c in combos:
            comp = tuple(sorted(set(range(n_splits)) - set(c)))
            tr = pd.concat([blocks[i] for i in c])
            te = pd.concat([blocks[i] for i in comp])
            if len(tr) < 120:
                continue
            w = weights_from_train(tr, "icir")
            te_ic = (te.values * w).sum(axis=1)
            mu, sd = float(te_ic.mean()), float(te_ic.std())
            paths.append({
                "train_blocks": [int(x) for x in c], "test_blocks": [int(x) for x in comp],
                "mean_ic": round(mu, 4),
                "icir": round(mu / sd, 3) if sd > 0 else 0.0,
                "pos_rate": round(float((te_ic > 0).mean()), 4),
                "n_days": int(len(te_ic)),
            })
        if not paths:
            return None
        icirs = [p["icir"] for p in paths]
        means = [p["mean_ic"] for p in paths]
        pos_paths = sum(1 for p in paths if p["icir"] > 0) / len(paths)
        worst = min(paths, key=lambda p: p["icir"])
        out = {
            "n_paths": len(paths), "n_blocks": n_splits, "n_combos": len(combos),
            "mean_ic_median": round(float(np.median(means)), 4),
            "mean_ic_min": round(float(np.min(means)), 4),
            "icir_median": round(float(np.median(icirs)), 3),
            "icir_min": round(float(np.min(icirs)), 3),
            "pos_path_rate": round(float(pos_paths), 4),
            "worst_path": worst,
            "evidence_weak": bool(pos_paths < 0.5),
        }
        print(f"    [CPCV] 路径 {out['n_paths']} 中位ICIR {out['icir_median']} 最差 {out['icir_min']} "
              f"正路径占比 {out['pos_path_rate']:.0%} 证据{'弱' if out['evidence_weak'] else '足'}")
        return out

    def _stability_report(self) -> dict:
        """参数稳定性（plateau/cliff + 多因子权重 CoV）与逐窗 regime 归因。"""
        out: dict = {"plateau": None, "weight_cov": None, "regime": None}
        # 1) plateau/cliff：基于 PBO 邻域变体绩效分布（--pbo 时才有）
        if self.pbo and self.pbo.get("var_perf"):
            vps = self.pbo["var_perf"]
            cagrs = [v["cagr"] for v in vps.values()]
            m, lo, hi = float(np.median(cagrs)), float(np.min(cagrs)), float(np.max(cagrs))
            main_cagr = float(self.stats_all["cagr"])
            # cliff：主协议为正，但邻域最差变体塌缩到 <0 或 <主协议 30%
            cliff = bool(main_cagr > 0 and lo < min(0.0, main_cagr * 0.3))
            out["plateau"] = {
                "main_cagr": round(main_cagr, 4), "n_variants": len(cagrs),
                "median_cagr": round(m, 4), "worst_cagr": round(lo, 4), "best_cagr": round(hi, 4),
                "cliff_veto": cliff,
                "judge": "参数稳定（邻域无塌缩）" if not cliff
                         else "⚠️ 悬崖信号：邻域最差变体显著塌缩，触发 cliff veto",
            }
        # 2) 多因子权重跨窗 CoV（单因子组合无权重波动，跳过）
        if len(self.names) > 1:
            w_rows = [w_["w_icir"] for w_ in self.windows if w_["w_icir"]]
            if w_rows:
                wdf = pd.DataFrame(w_rows)
                cov = (wdf.std() / wdf.mean().abs().replace(0, np.nan)
                       ).replace([np.inf, -np.inf], np.nan)
                out["weight_cov"] = {str(k): round(float(v), 3)
                                     for k, v in cov.items() if not np.isnan(v)}
        # 3) 逐窗 regime 归因 + 覆盖均衡检查
        if self.windows:
            reg: dict[str, list] = {}
            for w_ in self.windows:
                reg.setdefault(w_["regime"], []).append(w_["oos_total"])
            by_regime = {}
            for k, v in reg.items():
                by_regime[k] = {"n": len(v),
                                "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
                                "mean_oos": round(float(np.mean(v)), 4)}
            total = len(self.windows)
            dominant = max((s["n"] for s in by_regime.values()), default=0)
            out["regime"] = {
                "by_regime": by_regime,
                "dominant_ratio": round(dominant / total, 3) if total else None,
                "evidence_insufficient": bool(total and dominant / total > 0.8),
            }
        return out

    def verdict(self) -> tuple[str, list[str]]:
        n_wins = sum(1 for w_ in self.windows if w_["oos_total"] > 0)
        win_rate = n_wins / len(self.windows)
        oos = self.stats_all
        reasons: list[str] = []
        if len(self.windows) < MIN_FOLDS:
            reasons.append(f"折数不足 {len(self.windows)}<{MIN_FOLDS} → 样本不足")
            return "WARN", reasons
        if self.stability and self.stability.get("plateau") and self.stability["plateau"].get("cliff_veto"):
            reasons.append("cliff veto：PBO 邻域最差变体显著塌缩，参数稳定性不足（P1-7）")
            return "WARN", reasons
        if self.cpcv and self.cpcv.get("evidence_weak"):
            reasons.append(f"CPCV 多路径证据偏弱：正路径占比 {self.cpcv['pos_path_rate']:.0%}<50%，"
                           f"最差路径 ICIR {self.cpcv['icir_min']}（P2-10）")
            return "WARN", reasons
        oos_months = max(1, int(len(self.oos_all) / 21))
        if oos_months < MIN_OOS_MONTHS:
            reasons.append(f"OOS 月数不足 {oos_months}<{MIN_OOS_MONTHS} → 样本不足")
        if oos["cagr"] <= 0.0 or win_rate < POS_WINDOW_WARN:
            reasons.append(f"整体 OOS 年化 {oos['cagr']:+.2%} / 正窗占比 {win_rate:.0%} → 系统性失败")
            return "FAIL", reasons
        if oos["mdd"] <= MDD_FAIL:
            reasons.append(f"OOS 最大回撤 {oos['mdd']:+.2%} ≤ {MDD_FAIL:.0%} → 风险超限")
            return "FAIL", reasons
        if oos["cagr"] > 0.0 and win_rate >= POS_WINDOW_PASS and oos["mdd"] >= MDD_PASS and oos_months >= MIN_OOS_MONTHS:
            reasons.append(f"整体 OOS 年化 {oos['cagr']:+.2%} / 正窗 {win_rate:.0%} / 回撤 {oos['mdd']:+.2%} / {oos_months}月")
            return "PASS", reasons
        reasons.append(f"整体 OOS 年化 {oos['cagr']:+.2%} / 正窗 {win_rate:.0%} / 回撤 {oos['mdd']:+.2%} → 部分指标临界")
        return "WARN", reasons

    # ── 边界规则履行自检 ──
    def boundary_report(self, one_vote_status: str, force_used: bool) -> list[dict]:
        return [
            {"rule": "B1 禁止把 OOS 并入 IS 重训", "status": "OK", "note": "引擎保证：walk_forward 仅在 IS 内寻优，OOS 段仅评估"},
            {"rule": "B2 禁止只报最优窗口", "status": "OK", "note": f"报告披露全部 {len(self.windows)} 个窗口，发布口径=拼接 OOS"},
            {"rule": "B3 禁止不记 N 宣称通过", "status": "OK", "note": f"本次记账到 wfa_trials.jsonl（指纹 {self.fp}）"},
            {"rule": "B4 一票制：同协议禁调参重跑", "status": ("VIOLATION" if force_used else "OK"), "note": f"one-vote={one_vote_status}"},
            {"rule": "B5 禁止改协议参数直到好看", "status": "WATCH", "note": "协议指纹已记录；变更参数=新协议需全新 hold-out"},
            {"rule": "A1 失败归因（诊断非调参）", "status": "GUIDE", "note": "FAIL 时须归因 regime/行业/波动率，见边界规则文档"},
            {"rule": "A2 改规则后全新 hold-out 重验", "status": "GUIDE", "note": "见边界规则文档 A2"},
            {"rule": "A4 FAIL 处置：观察/降仓/冷却", "status": "GUIDE", "note": "见边界规则文档 A4"},
        ]

    # ── 主流程 ──
    def run(self) -> int:
        one_vote_status, last = check_one_vote(self.fp, self.a.force)
        if one_vote_status == "blocked":
            print(f"\n⛔ 一票制拦截（B4）：协议指纹 {self.fp} 已裁决为 {last['verdict']}（{last['ts']}）。")
            print("   禁止无变更重跑。若确需重跑请 --force（会记入 violation_log 并进入试验日志）。")
            print("   调整规则请换协议（改参数=新指纹），并保留从未看过的全新 hold-out 段。")
            return 2
        force_used = one_vote_status == "force"

        # 预注册检查（P2-11）：正式验证须先声明验证协议（禁 p-hacking）
        self.prereg = None
        if self.a.prereg:
            try:
                p = load_prereg(self.a.prereg)
            except Exception as e:
                print(f"⛔ 预注册文件读取失败: {e}")
                return 2
            errs = check_prereg(p, self.combo, self.params_dict(), ENGINE_VERSION)
            if errs:
                print(f"⛔ 预注册校验失败（fail-closed）：{'；'.join(errs)}")
                print("   预注册声明与本次运行不一致，拒绝运行。修正预注册或本次参数后重试。")
                return 2
            self.prereg = p
            print(f"   ✅ 预注册匹配：{p.get('id')}（对象 {p.get('object', '')}；承诺: {p.get('commitment', '')}）")
        else:
            print("   ℹ️ 未指定 --prereg：本次为探索性验证，结果不作正式上线门禁证据（B7）")

        t_start = time.time()
        self.load()
        self.build_scores()
        res = self._run_protocol(self.a.train, self.a.test)
        self.wf, self.windows, self.oos_all, self.stats_all = (
            res["wf"], res["windows"], res["oos_all"], res["stats_all"])
        self.pbo = self._run_pbo_scan() if self.a.pbo else None
        self.cpcv = self._cpcv_scan() if self.a.cpcv else None
        self.stability = self._stability_report()
        verdict, reasons = self.verdict()
        boundary = self.boundary_report(one_vote_status, force_used)

        # 试验记账（--pbo 时等效试验数 += PBO 扫描轨道数）
        n_eff = len(self.windows) * 2
        if self.pbo:
            n_eff += self.pbo["n_tracks"]
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "fingerprint": self.fp,
            "combo": self.combo,
            "params": {"train": self.a.train, "test": self.a.test, "fwd": self.a.fwd,
                       "cost": self.a.cost, "top_pct": self.a.top_pct,
                       "start": self.a.start, "end": self.a.end},
            "n_eff_trials": n_eff,
            "n_folds": len(self.windows),
            "verdict": verdict,
            "oos": {"cagr": self.stats_all["cagr"], "mdd": self.stats_all["mdd"],
                    "sharpe": self.stats_all["sharpe"], "total": self.stats_all["total"]},
            "pbo": self.pbo,
            "cpcv": self.cpcv,
            "stability": self.stability,
            "prereg": ({"id": self.prereg.get("id"), "status": "completed"}
                       if self.prereg else {"status": "exploratory"}),
            "win_window_rate": round(sum(1 for w_ in self.windows if w_["oos_total"] > 0) / len(self.windows), 4),
            "force": force_used,
            "one_vote_status": one_vote_status,
        }
        if not self.a.no_log:
            append_trial(rec)
            if self.prereg:
                complete_prereg(self.a.prereg, self.prereg, verdict, rec["oos"], self.pbo, self.cpcv)
                print(f"   ✅ 预注册已回填：{self.a.prereg}")

        # 报告
        self.write_report(rec, verdict, reasons, boundary)
        # 留存结果供批量汇总使用
        self.last_verdict = verdict
        self.last_reasons = reasons
        self.last_rec = rec
        print(f"\n✅ 门禁裁决: {verdict}  ({time.time()-t_start:.0f}s)")
        for r in reasons:
            print(f"    - {r}")
        return 0 if verdict == "PASS" else 1

    def write_report(self, rec, verdict, reasons, boundary):
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_s = datetime.now().strftime("%Y%m%d")
        tag = "_smoke" if self.a.no_log else ""
        stem = f"wfa_gate_{'_'.join(self.combo)}{tag}_{date_s}"
        # JSON
        (REPORTS_DIR / f"{stem}.json").write_text(
            json.dumps({"fingerprint": self.fp, "verdict": verdict, "reasons": reasons,
                        "params": rec["params"], "n_eff_trials": rec["n_eff_trials"],
                        "windows": self.windows, "oos_all": rec["oos"],
                        "pbo": self.pbo, "boundary": boundary},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        # Markdown
        rows = "\n".join(
            f"| fold{w_['fold']} | {w_['train'][0]}~{w_['train'][1]} | {w_['test'][0]}~{w_['test'][1]} | "
            f"{w_['oos_total']:+.2%} | {w_['oos_mdd']:+.2%} | {w_['oos_pos_month_rate']:.0%} | "
            f"{w_.get('mk_ret', 0):+.2%} | {w_.get('regime', '')} | {w_.get('excess', 0):+.2%} |"
            for w_ in self.windows)
        b_rows = "\n".join(f"| {b['rule']} | {b['status']} | {b['note']} |" for b in boundary)
        if self.pbo:
            q = self.pbo["lambda_p25_p50_p75"]
            pbo_verdict = ("PBO<0.25：参数选择大概率可泛化，较稳健" if self.pbo["pbo"] < 0.25
                           else "0.25≤PBO≤0.5：存在过拟合嫌疑，谨慎解读" if self.pbo["pbo"] <= 0.5
                           else "PBO>0.5：大概率过拟合")
            PBO_MD = (f"| 轨道数（参数邻域变体） | {self.pbo['n_tracks']} |\n"
                      f"| 月数 | {self.pbo['n_months']} |\n"
                      f"| 切分 S（组合数） | {self.pbo['splits']}（{self.pbo['n_combos']}） |\n"
                      f"| **PBO（回测过拟合概率）** | **{self.pbo['pbo']:.4f}** |\n"
                      f"| lambda p25/p50/p75 | {q[0]}/{q[1]}/{q[2]} |\n"
                      f"| 判读 | {pbo_verdict} |\n\n"
                      f"> PBO 语义：训练段最优变体在测试段落入收益下半区的频率。"
                      f"参数邻域 train∈{self.pbo['tracks']} test∈{self.pbo['tests']}。")
        else:
            PBO_MD = ("未开启 `--pbo` 深度扫描。若本组合是参数扫描/变体挑选的产物，"
                      "请对扫描矩阵运行 `python backtest_pbo_check.py --input <sweep_returns.csv>` "
                      "得到 PBO（<0.25 稳健 / 0.25~0.5 存疑 / >0.5 过拟合）。")
        stab = self.stability or {}
        stab_lines = []
        if stab.get("plateau"):
            p = stab["plateau"]
            stab_lines.append(
                f"- **plateau/cliff（PBO 邻域）**：主协议年化 {p['main_cagr']:+.2%}，"
                f"邻域 {p['n_variants']} 变体中位 {p['median_cagr']:+.2%} / 最差 {p['worst_cagr']:+.2%} / "
                f"最好 {p['best_cagr']:+.2%} → **{p['judge']}**")
        if stab.get("weight_cov"):
            cov_s = "、".join(f"{k}={v}" for k, v in stab["weight_cov"].items())
            stab_lines.append(f"- **权重跨窗 CoV**（多因子）：{cov_s}（CoV 大=参数漂移；无权威阈值，需按因子语义自定档）")
        if stab.get("regime"):
            r = stab["regime"]
            byr = "；".join(f"{k}(n={v['n']},胜率{v['win_rate']:.0%},均值{v['mean_oos']:+.2%})"
                            for k, v in r["by_regime"].items())
            ev = ("⚠️ regime 覆盖不均衡（主导 regime 占比>80%）→ 结论证据不足"
                  if r.get("evidence_insufficient")
                  else "regime 覆盖较均衡")
            stab_lines.append(f"- **regime 归因**：{byr}；主导占比 {r.get('dominant_ratio')} → {ev}")
        STAB_MD = "\n".join(stab_lines) if stab_lines else "未生成（需 --pbo 邻域数据）"
        if self.cpcv:
            c = self.cpcv
            wp = c["worst_path"]
            ev = ("⚠️ 多路径证据偏弱（正路径<50%）→ 门禁已降级 WARN"
                  if c.get("evidence_weak") else "多路径证据充分")
            CPCV_MD = (f"| 路径数（C({c['n_blocks']},{c['n_blocks'] // 2})/2） | {c['n_paths']} |\n"
                       f"| mean_IC 中位 / 最差 | {c['mean_ic_median']:+.4f} / {c['mean_ic_min']:+.4f} |\n"
                       f"| ICIR 中位 / 最差 | {c['icir_median']:+.3f} / {c['icir_min']:+.3f} |\n"
                       f"| 正 ICIR 路径占比 | {c['pos_path_rate']:.0%} |\n"
                       f"| 最差路径 | 训练块{wp['train_blocks']}→测试块{wp['test_blocks']} ICIR {wp['icir']} |\n"
                       f"| 判读 | {ev} |\n\n"
                       f"> CPCV 把时间切成 {c['n_blocks']} 块、遍历训练/测试块组合，产生 {c['n_paths']} 条 OOS 路径，"
                       f"比 WFA 单路径更稳健（Arian 2024 实证）。正路径占比 <50% 时门禁降级 WARN。")
        else:
            CPCV_MD = "未开启 `--cpcv`。CPCV 是 WFA 单路径的稳健性补充，月度评审建议开启。"
        md = f"""# WFA 门禁报告：{' + '.join(self.combo)}

> 生成 {datetime.now().isoformat(timespec='seconds')} | 引擎 {ENGINE_VERSION} | 协议指纹 `{self.fp}`
> 数据范围 {self.a.start}~{self.a.end} | train={self.a.train} test={self.a.test} fwd={self.a.fwd} cost={self.a.cost} top_pct={self.a.top_pct}
> 价格源={self.a.price_source}（stock_daily_meta 原始收盘，与生产 factor_combo_walkforward 一致）| PIT 因子=factor_pit.db 法定披露截止
> 预注册：{self.prereg.get('id', '—') if self.prereg else '未预注册'} —— {'正式验证，承诺已回填' if self.prereg else '探索性验证，不作正式上线门禁证据（B7）'}

## 门禁裁决：**{verdict}**

{'；'.join(reasons)}

## 整体 OOS（拼接全部样本外，连续复利，唯一发布口径）

| 指标 | 值 |
|---|---|
| 年化收益 | {self.stats_all['cagr']:+.2%} |
| 最大回撤 | {self.stats_all['mdd']:+.2%} |
| Sharpe（近似） | {self.stats_all['sharpe']:+.2f} |
| 累计收益 | {self.stats_all['total']:+.2%} |
| 正收益窗口占比 | {sum(1 for w_ in self.windows if w_['oos_total']>0)}/{len(self.windows)} |

## 逐窗 OOS（独立初始资金，仅横向比较；大盘=全市场等权 proxy，超额=区间收益-大盘）

| 折 | 训练段 | 测试段 | 区间收益 | 回撤 | 正月率 | 大盘区间 | regime | 超额收益 |
|---|---|---|---|---|---|---|---|---|
{rows}

## WFA 因子 IC 通道（诊断）

| 方案 | OOS IC | ICIR | 正率 |
|---|---|---|---|
{''.join(f"| {s} | {st_['mean_ic']:+.4f} | {st_['icir']:+.3f} | {st_['pos_rate']:.0%} |\n" for s, st_ in self.wf['oos_summary'].items())}

## 过拟合诊断：PBO/CSCV

{PBO_MD}

## 参数稳定性与 regime 归因（P1-7 / P1-9）

{STAB_MD}

## CPCV 多路径诊断（P2-10）

{CPCV_MD}

## 边界规则履行自检

| 规则 | 状态 | 说明 |
|---|---|---|
{b_rows}

## 结论与处置建议

- {verdict} 结论：{'允许上线（仍需过交易闸门 R1-R18 + 名单制）' if verdict=='PASS' else '建议降仓/观察期上线，逐窗归因后再定' if verdict=='WARN' else '禁止上线：进入冷却期，须归因（regime/行业/波动率）并按 A2 用全新 hold-out 重验'}。
- 试验次数 N（本次等效）: {rec['n_eff_trials']}；历史同组合累计尝试请查 data/wfa_trials.jsonl。
"""
        (REPORTS_DIR / f"{stem}.md").write_text(md, encoding="utf-8")
        print(f"      📄 {REPORTS_DIR / f'{stem}.md'}")


def run_batch(combos, base_args) -> int:
    """批量门禁验证：多个候选因子组合逐个跑门禁，汇总横向对比表。
    （评审筛选用；正式上线仍须按 P2-11 对入选组合预注册后单独验证。）"""
    import copy
    rows = []
    print(f"\n{'=' * 70}\n批量门禁验证：{len(combos)} 个候选组合\n{'=' * 70}")
    for combo in combos:
        a2 = copy.deepcopy(base_args)
        a2.combo = combo
        a2.prereg = None  # 批量是评审筛选，不做预注册
        g = WfaGate(a2)
        rc = g.run()
        st = getattr(g, "stats_all", None)
        verdict = getattr(g, "last_verdict", "BLOCKED" if rc == 2 else "ERR")
        rows.append({
            "combo": " ".join(combo), "rc": rc, "verdict": verdict,
            "cagr": round(st["cagr"], 4) if st is not None else None,
            "mdd": round(st["mdd"], 4) if st is not None else None,
            "win_window_rate": getattr(g, "last_rec", {}).get("win_window_rate"),
            "cpcv_pos_path": (g.cpcv or {}).get("pos_path_rate") if hasattr(g, "cpcv") else None,
        })
        print(f"  [{len(rows)}/{len(combos)}] {combo}: {verdict}")

    date_s = datetime.now().strftime("%Y%m%d")
    tag = "_smoke" if getattr(base_args, "no_log", False) else ""
    out = REPORTS_DIR / f"wfa_gate_batch{tag}_{date_s}.md"
    order = {"PASS": 0, "WARN": 1, "FAIL": 2, "BLOCKED": 3, "ERR": 9}
    rows_sorted = sorted(rows, key=lambda r: order.get(r["verdict"], 9))
    lines = ["# WFA 门禁批量验证汇总", "",
             f"> 生成 {date_s} | 引擎 {ENGINE_VERSION} | 候选 {len(rows)} 个", "",
             "| 候选 | 裁决 | OOS年化 | 最大回撤 | 正窗占比 | CPCV正路径 | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows_sorted:
        note = "一票制已裁决，未重跑" if r["verdict"] == "BLOCKED" else ("运行错误" if r["verdict"] == "ERR" else "")
        cagr = f"{r['cagr']:+.2%}" if r["cagr"] is not None else "—"
        mdd = f"{r['mdd']:+.2%}" if r["mdd"] is not None else "—"
        wwr = f"{r['win_window_rate']:.0%}" if r["win_window_rate"] is not None else "—"
        cpcv = f"{r['cpcv_pos_path']:.0%}" if r["cpcv_pos_path"] is not None else "—"
        lines.append(f"| {r['combo']} | {r['verdict']} | {cagr} | {mdd} | {wwr} | {cpcv} | {note} |")
    lines += ["",
              "> 说明：PASS=允许上线（仍过 R1-R18+名单制）；WARN=降仓观察；FAIL=禁止上线+归因；",
              "> BLOCKED=该组合协议此前已裁决（一票制），本次未重跑。正式上线须按 P2-11 预注册后单独验证。"]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n📊 批量汇总: {out}")
    return 0


ML_ENGINE = "tulong_wfa_gate.ml.v1"


def ml_fingerprint(featureset, model, train, test, fwd, cost, start, end):
    payload = {"engine": ML_ENGINE, "featureset": featureset, "model": model,
               "train": int(train), "test": int(test), "fwd": int(fwd),
               "cost": float(cost), "start": str(start), "end": str(end)}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def run_ml_gate(a) -> int:
    """ML 信号通道门禁（V1）：复用 factor_ml_walkforward_v3 管线
    （qfq 价格源 + PIT 特征 + purge/embargo + ML WFA + 组合模拟），
    接入门禁统一裁决/一票制/预注册/试验记账。
    与因子 IC 通道的区别：ML 每折重训（purge/embargo 生效），裁决用整体 OOS 组合绩效。
    V1 不做 CPCV/PBO（ML 重训成本高），逐窗信息来自 fold_meta 的 IC。"""
    import copy
    import factor_combo_walkforward as fcw
    import factor_ml_walkforward_v2 as f2
    import factor_ml_walkforward_v3 as f3

    a2 = copy.deepcopy(a)
    fs = (a2.featureset[0] if isinstance(a2.featureset, list) else a2.featureset)
    model = (a2.model[0] if isinstance(a2.model, list) else a2.model)
    fp = ml_fingerprint(fs, model, a2.train, a2.test, a2.fwd, a2.cost, a2.start, a2.end)

    one_vote_status, last = check_one_vote(fp, a2.force)
    if one_vote_status == "blocked":
        print(f"\n⛔ 一票制拦截（B4）：ML 协议指纹 {fp} 已裁决为 {last['verdict']}（{last['ts']}）。"
              f"\n   禁止无变更重跑；--force 记 violation。")
        return 2
    force_used = one_vote_status == "force"

    # 预注册检查
    prereg = None
    if a2.prereg:
        try:
            p = load_prereg(a2.prereg)
        except Exception as e:
            print(f"⛔ 预注册文件读取失败: {e}")
            return 2
        params = {"train": a2.train, "test": a2.test, "fwd": a2.fwd, "cost": a2.cost,
                  "top_pct": a2.top_pct, "start": a2.start, "end": a2.end}
        errs = check_prereg(p, [f"ml_{fs}_{model}"], params, ML_ENGINE)
        if errs:
            print(f"⛔ 预注册校验失败（fail-closed）：{'；'.join(errs)}")
            return 2
        prereg = p
        print(f"   ✅ 预注册匹配：{p.get('id')}")
    else:
        print("   ℹ️ 未指定 --prereg：本次为探索性验证（B7）")

    t_start = time.time()
    print("[1/4] 构建面板（qfq 价格源 + PIT 特征）...")
    panels = f2.build_panels(a2.start, a2.end, a2.fwd, warmup=a2.warmup)
    cal = panels["calendar"]
    fwd_w, ret_bt, raw_close = panels["fwd"], panels["ret_bt"], panels["raw_close"]
    wides = {k: panels["wides"][k] for k in f3.CLEAN_DB + f3.CLEAN_VOL}
    wides.update(f3.load_pit_wides(cal, raw_close))
    # ⚠️ m4-v2 待迁移: ML 评估路径仍用快照ST（ml腿 8/29 已退役，本路径休眠；迁移
    # 需 simulate_ml_portfolio 改 per-date，随 PD-049 m4-v2 处理）
    st_codes = fcw.load_st_codes()
    features = f3.FEATURESETS[fs]
    aligned = f2.AlignedFeatures(wides, features, cal)
    print(f"      日历 {len(cal)} 天 | 特征 {len(features)} | 污染日剔除 {len(panels['polluted'])}")

    reb_pos = [cal.searchsorted(d) for d in
               fcw.month_starts(cal, from_date=cal[min(a2.train, len(cal) - 1)])]
    reb_pos = [p for p in reb_pos if p < len(cal)]

    print(f"[2/4] ML Walk-Forward（{fs}/{model}，purge+embargo ON，train={a2.train} test={a2.test}）...")
    t1 = time.time()
    wf = f2.walk_forward_ml_v2(aligned, fwd_w, model, train_d=a2.train, test_d=a2.test,
                               fwd_days=a2.fwd, use_purge=True, use_embargo=True,
                               embargo_days=21, max_train_samples=a2.max_train_samples,
                               seed=42, verbose=True)
    print(f"      WFA 耗时 {time.time()-t1:.0f}s")

    print("[3/4] OOS 组合模拟（Top10% 月度，next-bar，成本）...")
    nav, month_rets = f2.simulate_ml_portfolio(wf["folds"], aligned, ret_bt, raw_close,
                                               st_codes, reb_pos, cost=a2.cost)
    stats = fcw.perf_stats(nav.nav, f"ML_{model}_{fs}")
    s_ = wf["summary"]
    print(f"      OOS IC={s_['mean_ic']:+.4f} ICIR={s_['icir']:+.3f} 正率={s_['pos_rate']:.0%} "
          f"折={s_['n_folds']} | 组合 CAGR={stats['cagr']:+.1%} MDD={stats['mdd']:+.1%} "
          f"Sharpe={stats['sharpe']:+.3f} 累计={stats['total']:+.1%}")

    # 裁决（ML 通道 V1：整体 OOS 组合绩效 + IC 正率）
    reasons = []
    if s_["n_folds"] < MIN_FOLDS:
        reasons.append(f"折数不足 {s_['n_folds']}<{MIN_FOLDS}")
        verdict = "WARN"
    elif stats["cagr"] <= 0 or s_["pos_rate"] < 0.5:
        reasons.append(f"整体 OOS CAGR {stats['cagr']:+.2%} / IC 正率 {s_['pos_rate']:.0%} → 系统性失败")
        verdict = "FAIL"
    elif stats["mdd"] <= MDD_FAIL:
        reasons.append(f"OOS 最大回撤 {stats['mdd']:+.2%} ≤ {MDD_FAIL:.0%}")
        verdict = "FAIL"
    elif stats["cagr"] > 0 and s_["pos_rate"] >= 0.6 and stats["mdd"] >= MDD_PASS:
        reasons.append(f"OOS CAGR {stats['cagr']:+.2%} / IC 正率 {s_['pos_rate']:.0%} / 回撤 {stats['mdd']:+.2%}")
        verdict = "PASS"
    else:
        reasons.append(f"OOS CAGR {stats['cagr']:+.2%} / IC 正率 {s_['pos_rate']:.0%} / 回撤 {stats['mdd']:+.2%} → 临界")
        verdict = "WARN"

    # 逐窗信息（fold_meta）
    fold_rows = "\n".join(
        f"| {m['fold']} | {m['train'][0]}~{m['train'][1]} | {m['test'][0]}~{m['test'][1]} | "
        f"{m['n_train']:,} | {m['n_purge_days']} | {m['n_embargo_days']} | {m['fit_s']}s |"
        for m in wf["fold_meta"])

    # 记账
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fp, "engine": ML_ENGINE,
        "combo": [f"ml_{fs}_{model}"],
        "params": {"train": a2.train, "test": a2.test, "fwd": a2.fwd, "cost": a2.cost,
                   "top_pct": a2.top_pct, "start": a2.start, "end": a2.end,
                   "purge": True, "embargo": True, "embargo_days": 21},
        "n_eff_trials": int(s_["n_folds"]),
        "n_folds": s_["n_folds"],
        "verdict": verdict,
        "oos": {"cagr": stats["cagr"], "mdd": stats["mdd"], "sharpe": stats["sharpe"],
                "total": stats["total"], "ic": s_["mean_ic"], "ic_pos_rate": s_["pos_rate"]},
        "force": force_used, "one_vote_status": one_vote_status,
        "prereg": ({"id": prereg.get("id"), "status": "completed"} if prereg
                   else {"status": "exploratory"}),
    }
    if not a2.no_log:
        append_trial(rec)
        if prereg:
            complete_prereg(a2.prereg, prereg, verdict, rec["oos"], None, None)

    # 报告
    tag = "_smoke" if a2.no_log else ""
    stem = f"wfa_gate_ml_{fs}_{model}{tag}_{datetime.now().strftime('%Y%m%d')}"
    md = f"""# WFA 门禁报告（ML 通道）：{fs} / {model}

> 生成 {datetime.now().isoformat(timespec='seconds')} | 引擎 {ML_ENGINE} | 协议指纹 `{fp}`
> 数据 {a2.start}~{a2.end} | train={a2.train} test={a2.test} fwd={a2.fwd} cost={a2.cost}
> 价格源=qfq（dragon_quant_qfq.db）| 特征=PIT 清洁（{len(features)}个）| purge+embargo ON（21 交易日）
> 预注册：{prereg.get('id', '未预注册（探索性，B7）') if prereg else '未预注册（探索性，B7）'}

## 门禁裁决：**{verdict}**

{'；'.join(reasons)}

## 整体 OOS（拼接全部样本外，唯一发布口径）

| 指标 | 值 |
|---|---|
| 年化收益 | {stats['cagr']:+.2%} |
| 最大回撤 | {stats['mdd']:+.2%} |
| Sharpe | {stats['sharpe']:+.3f} |
| 累计收益 | {stats['total']:+.2%} |
| OOS IC / ICIR / 正率 | {s_['mean_ic']:+.4f} / {s_['icir']:+.3f} / {s_['pos_rate']:.0%} |
| 折数 | {s_['n_folds']}（WFA 耗时见下） |

## 逐折（训练/测试/purge/embargo）

| 折 | 训练段 | 测试段 | 训练样本 | purge | embargo | fit |
|---|---|---|---|---|---|---|
{fold_rows}

## 说明

- ML 通道每折重训（purge 剔除标签重叠、embargo 21 交易日），V1 不做 CPCV/PBO（重训成本高）。
- 边界规则 B1-B7 / A1-A6 同因子通道适用；正式上线须预注册（P2-11）。
"""
    (REPORTS_DIR / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"      📄 {REPORTS_DIR / f'{stem}.md'}")
    print(f"\n✅ ML 门禁裁决: {verdict}  ({time.time()-t_start:.0f}s)")
    return 0 if verdict == "PASS" else 1


def run_signals_gate(a) -> int:
    """信号名单验证（V1 机制版）：读 v4 信号 Top20 快照（signal_*.csv），
    验证名单后续 5/20 交易日收益 vs 全市场等权基准（qfq 唯一价格源）。
    ⚠️ 当前历史快照仅数日，fwd20 大多数据不足；本模式为机制打通 + 初步观察，
    正式信号级门禁待 full_score_*.csv 积累后升级（v4 已 8/25 起落盘全市场分数）。"""
    import glob
    d = Path(a.signals_dir) if a.signals_dir else ROOT / "reports" / "daily_signals"
    files = sorted(glob.glob(str(d / "signal_*.csv")))
    if not files:
        print(f"⛔ {d} 下无 signal_*.csv 快照")
        return 2
    con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT code, date, close FROM stock_daily_meta_qfq WHERE close > 0", con)
    con.close()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df["code"] = df["code"].astype(str).str.zfill(6)
    px = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    dates = px.index
    # 与生产 factor_combo 同口径：截断异常收益（±21% 外视为脏数据），防单股除权/错价污染基准
    ret = px.pct_change().clip(-0.21, 0.21)
    ew = ret.mean(axis=1)

    rows = []
    for f in files:
        d0 = datetime.strptime(f.split("signal_")[1][:8], "%Y%m%d")
        sig = pd.read_csv(f)
        codes = sig["code"].astype(str).str.replace(r"^(SH|SZ)", "", regex=True)
        pos = dates.searchsorted(pd.Timestamp(d0), side="right")
        row = {"date": d0.strftime("%Y-%m-%d"), "n": len(codes)}
        for n, tag in ((5, "fwd5"), (20, "fwd20")):
            end = min(pos + n, len(dates))
            row[tag] = row[f"ex{n}"] = None
            if end - pos >= 2:
                seg = px.iloc[pos:end]
                port = seg[codes]
                port = port.loc[:, port.notna().sum() > 0]
                if port.shape[1] > 0:
                    daily = port.pct_change().clip(-0.21, 0.21).mean(axis=1)
                    cum = float((1 + daily.fillna(0)).prod() - 1)
                    ew_cum = float((1 + ew.iloc[pos:end].fillna(0)).prod() - 1)
                    row[tag] = round(cum, 4)
                    row[f"ex{n}"] = round(cum - ew_cum, 4)
        rows.append(row)

    # 汇总
    fwd5s = [r["fwd5"] for r in rows if r["fwd5"] is not None]
    ex5s = [r["ex5"] for r in rows if r["ex5"] is not None]
    mean_ex5 = float(np.mean(ex5s)) if ex5s else None
    pos_ex = sum(1 for x in ex5s if x > 0) if ex5s else 0
    stem = f"wfa_gate_signals{'_smoke' if a.no_log else ''}_{datetime.now().strftime('%Y%m%d')}"
    lines = ["# WFA 门禁报告（信号名单验证，V1 机制版）", "",
             f"> 生成 {datetime.now().isoformat(timespec='seconds')} | 快照数 {len(rows)} | 价格源=qfq（dragon_quant_qfq.db）", "",
             "| 信号日 | 名单数 | 未来5日 | 超额vs等权 | 未来20日 | 超额 |", "|---|---|---|---|---|---|"]
    for r in rows:
        f5 = f"{r['fwd5']:+.2%}" if r["fwd5"] is not None else "—"
        e5 = f"{r['ex5']:+.2%}" if r["ex5"] is not None else "—"
        f20 = f"{r['fwd20']:+.2%}" if r["fwd20"] is not None else "数据不足"
        e20 = f"{r['ex20']:+.2%}" if r["ex20"] is not None else "—"
        lines.append(f"| {r['date']} | {r['n']} | {f5} | {e5} | {f20} | {e20} |")
    if mean_ex5 is not None:
        lines += ["", f"**5 日平均超额：{mean_ex5:+.2%}（正超额 {pos_ex}/{len(ex5s)} 份）**", "",
                  "> 注：V1 机制版，历史快照仅数日、fwd20 大多数据不足，结论仅供参考；",
                  "> 正式信号级门禁待 full_score_*.csv 积累后升级（v4 已 8/25 起落盘）。"]
    else:
        lines += ["", "> 所有快照均无足够后续数据（信号日太近），无法计算超额。"]
    out = REPORTS_DIR / f"{stem}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 {out}")
    return 0


# ═══════════════════════════════════════════════════════════
#  技术网格通道（V1，参数扫描预筛门禁）
# ═══════════════════════════════════════════════════════════
# 定位：VectorBT 等参数扫描/技术网格（SMC/PA 形态参数、均线网格等）研究层预筛的门禁。
# 语义：参数扫描结论禁止采"单点最优格"（邻域尖峰=过拟合特征）；只认"全正 2×2 平台期"
#       （相邻格点一致为正 = 参数稳定区间）。与因子/ML/信号三通道并列，共用一票制/记账/预注册。
# 裁决：PASS=存在稳健平台（累计笔数足+平均胜率足）；WARN=有正平台但样本不足/胜率不足/最优为孤立尖峰；
#       FAIL=无正参数区或有效格点不足。
TECHGRID_ENGINE = "tulong_wfa_gate.techgrid.v1"
GRID_MIN_CELLS = 12       # 有效格点下限（样本不足→FAIL）
PLATEAU_MIN_N = 100       # 平台区累计交易笔数下限
PLATEAU_MIN_WIN = 0.52    # 平台区平均胜率下限
SPIKE_NEIGH_MIN = 4       # 尖峰判定至少需要的邻域格数
GRID_METRIC_KEYS = ("ret", "win", "n")   # 网格 JSON 每格指标键（ret=per-trade 均值%, win=胜率%, n=笔数）


def techgrid_fingerprint(engine, strategy, x_name, x_vals, y_name, y_vals, fixed, start, end, price_source) -> str:
    """技术网格协议指纹：策略 + 网格定义 + 固定参数 + 数据范围 + 引擎版本（不含结果）。
    改网格/数据/策略=新协议=新指纹（一票制语义）。"""
    payload = {
        "engine": engine, "strategy": strategy,
        "x": {"name": x_name, "vals": x_vals},
        "y": {"name": y_name, "vals": y_vals},
        "fixed": fixed, "start": str(start), "end": str(end),
        "price_source": price_source,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _grid_matrix(cells: dict, x_vals: list, y_vals: list):
    """把 {"x|y": {"ret","win","n"}} 解析为 3 个矩阵。win 兼容小数(0.5)与百分数(50)两种输入。"""
    mat = np.full((len(x_vals), len(y_vals)), np.nan)
    winm = np.zeros_like(mat)
    nm = np.zeros_like(mat, dtype=int)
    for i, x in enumerate(x_vals):
        for j, y in enumerate(y_vals):
            c = cells.get(f"{x}|{y}")
            if c is None:
                continue
            mat[i, j] = float(c.get("ret", np.nan))
            w = float(c.get("win", 0.0))
            winm[i, j] = w / 100.0 if w > 1 else w
            nm[i, j] = int(c.get("n", 0))
    return mat, winm, nm


def _find_plateaus(mat, winm, nm):
    """滑动 2×2 全正收益窗口 = 平台期候选。返回 [{x,y,cells,total_n,avg_win,min_ret}]。"""
    h, w = mat.shape
    plats = []
    for i in range(h - 1):
        for j in range(w - 1):
            block = mat[i:i + 2, j:j + 2]
            if np.isnan(block).any() or not (block > 0).all():
                continue
            plats.append({
                "x": int(i), "y": int(j), "cells": 4,
                "total_n": int(nm[i:i + 2, j:j + 2].sum()),
                "avg_win": float(winm[i:i + 2, j:j + 2].mean()),
                "min_ret": float(block.min()),
            })
    return plats


def _techgrid_verdict(mat, winm, nm) -> tuple[str, list[str], dict]:
    reasons: list[str] = []
    h, w = mat.shape
    n_valid = int((~np.isnan(mat)).sum())
    if n_valid < GRID_MIN_CELLS:
        return "FAIL", [f"有效格点 {n_valid}<{GRID_MIN_CELLS} → 样本不足"], {
            "n_valid": n_valid, "best_ret": None, "neigh_mean": None,
            "n_plateaus": 0, "n_good_plateaus": 0}
    best_i, best_j = np.unravel_index(np.nanargmax(mat), mat.shape)
    best = mat[best_i, best_j]
    neigh = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == dj == 0:
                continue
            ii, jj = best_i + di, best_j + dj
            if 0 <= ii < h and 0 <= jj < w and not np.isnan(mat[ii, jj]):
                neigh.append(mat[ii, jj])
    neigh_mean = float(np.mean(neigh)) if len(neigh) >= SPIKE_NEIGH_MIN else None
    plats = _find_plateaus(mat, winm, nm)
    good = [p for p in plats if p["total_n"] >= PLATEAU_MIN_N and p["avg_win"] >= PLATEAU_MIN_WIN]
    diag = {"n_valid": n_valid, "best_ret": round(best, 4), "neigh_mean": neigh_mean,
            "n_plateaus": len(plats), "n_good_plateaus": len(good)}
    if best <= 0:
        return "FAIL", [f"全局最优格 per-trade {best:+.2f}% ≤ 0 → 无正参数区"], diag
    if good:
        p0 = good[0]
        reasons.append(f"存在稳健平台（{p0['cells']}格全正, 累计{p0['total_n']}笔, 平均胜率{p0['avg_win']:.0%}, "
                       f"平台内最低格 {p0['min_ret']:+.2f}%）")
        if neigh_mean is not None and neigh_mean <= 0:
            reasons.append(f"⚠️ 全局最优 {best:+.2f}% 为孤立尖峰（8邻域均值 {neigh_mean:+.2f}%≤0）→ 不采单点，按平台区参数候选")
            return "WARN", reasons, diag
        return "PASS", reasons, diag
    if plats:
        reasons.append("存在全正 2×2 平台但累计笔数/平均胜率低于门槛（样本不足）")
        return "WARN", reasons, diag
    if neigh_mean is not None and neigh_mean <= 0:
        reasons.append(f"全局最优 {best:+.2f}% 为孤立尖峰（8邻域均值 {neigh_mean:+.2f}%≤0）→ 过拟合特征")
        return "WARN", reasons, diag
    reasons.append(f"全局最优 {best:+.2f}% 但无全正 2×2 平台 → 无稳健参数区间")
    return "WARN", reasons, diag


def run_tech_grid_gate(a) -> int:
    """技术网格通道：读参数扫描结果 JSON（{strategy,pool,dims,fixed,cells}），
    按平台期结构裁决 PASS/WARN/FAIL，接入一票制/预注册/试验记账。"""
    try:
        g = json.loads(Path(a.tech_grid).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⛔ 技术网格结果读取失败: {e}")
        return 2
    strategy = g.get("strategy", "tech_grid")
    pool = g.get("pool", "")
    x_name, x_vals = g["dims"]["x"]["name"], g["dims"]["x"]["values"]
    y_name, y_vals = g["dims"]["y"]["name"], g["dims"]["y"]["values"]
    fixed = g.get("fixed", {})
    start, end = g.get("start", a.start), g.get("end", a.end)
    price_source = g.get("price_source", "qfq")
    cells = g["cells"]
    fp = techgrid_fingerprint(TECHGRID_ENGINE, strategy, x_name, x_vals, y_name, y_vals,
                              fixed, start, end, price_source)

    one_vote_status, last = check_one_vote(fp, a.force)
    if one_vote_status == "blocked":
        print(f"\n⛔ 一票制拦截（B4）：技术网格指纹 {fp} 已裁决为 {last['verdict']}（{last['ts']}）。"
              f"\n   禁止无变更重跑；--force 记 violation。")
        return 2
    force_used = one_vote_status == "force"

    # 预注册检查（技术网格专用）
    prereg = None
    if a.prereg:
        try:
            p = load_prereg(a.prereg)
        except Exception as e:
            print(f"⛔ 预注册文件读取失败: {e}")
            return 2
        if p.get("engine") != TECHGRID_ENGINE:
            print(f"⛔ engine 不一致（预注册 {p.get('engine')} vs 本次 {TECHGRID_ENGINE}）")
            return 2
        if p.get("strategy") != strategy:
            print(f"⛔ strategy 不一致（预注册 {p.get('strategy')} vs 本次 {strategy}）")
            return 2
        pg = p.get("grid", {})
        if pg.get("x") != x_vals or pg.get("y") != y_vals or pg.get("fixed") != fixed:
            print(f"⛔ 网格定义与预注册不一致（fail-closed）：预注册 {pg} vs 本次 dims/fixed")
            return 2
        if p.get("status") == "completed":
            print("⛔ 该预注册已 completed，不得复用（一票制语义）")
            return 2
        prereg = p
        print(f"   ✅ 预注册匹配：{p.get('id')}（对象 {p.get('object', '')}）")
    else:
        print("   ℹ️ 未指定 --prereg：本次为探索性验证（B7）")

    mat, winm, nm = _grid_matrix(cells, x_vals, y_vals)
    verdict, reasons, diag = _techgrid_verdict(mat, winm, nm)
    n_eff = int(nm.sum())

    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "fingerprint": fp, "engine": TECHGRID_ENGINE,
        "combo": [strategy], "pool": pool,
        "params": {"x": {"name": x_name, "values": x_vals},
                   "y": {"name": y_name, "values": y_vals},
                   "fixed": fixed, "start": str(start), "end": str(end),
                   "price_source": price_source},
        "n_eff_trials": max(1, n_eff),
        "verdict": verdict, "diag": diag,
        "force": force_used, "one_vote_status": one_vote_status,
        "prereg": ({"id": prereg.get("id"), "status": "completed"} if prereg
                   else {"status": "exploratory"}),
    }
    if not a.no_log:
        append_trial(rec)
        if prereg:
            complete_prereg(a.prereg, prereg, verdict, rec.get("oos") or {"n": n_eff}, None, None)
            print(f"   ✅ 预注册已回填：{a.prereg}")

    tag = "_smoke" if a.no_log else ""
    stem = f"wfa_gate_techgrid_{strategy}{tag}_{datetime.now().strftime('%Y%m%d')}"
    best_disp = f"{diag['best_ret']:+.2f}%" if diag["best_ret"] is not None else "—"
    nm_disp = f"{diag['neigh_mean']:+.2f}%" if diag["neigh_mean"] is not None else "—（邻域格不足）"
    md = f"""# WFA 门禁报告（技术网格通道）：{strategy}

> 生成 {datetime.now().isoformat(timespec='seconds')} | 引擎 {TECHGRID_ENGINE} | 协议指纹 `{fp}`
> 网格 {x_name}∈{x_vals} × {y_name}∈{y_vals} | 固定 {fixed} | 样本池 {pool or '—'}
> 数据 {start}~{end} | 价格源 {price_source} | 累计有效格点 {diag['n_valid']}，全网格交易 {n_eff} 笔
> 预注册：{prereg.get('id', '—') if prereg else '未预注册'} —— {'正式验证' if prereg else '探索性验证（B7）'}

## 门禁裁决：**{verdict}**

{'；'.join(reasons)}

## 平台期诊断

| 指标 | 值 |
|---|---|
| 全局最优格 per-trade | {best_disp} |
| 8 邻域均值 | {nm_disp} |
| 全正 2×2 平台数 | {diag['n_plateaus']} |
| 稳健平台数（笔数≥{PLATEAU_MIN_N} 且 胜率≥{PLATEAU_MIN_WIN:.0%}） | {diag['n_good_plateaus']} |

> 语义：参数扫描结论禁止采单点最优格（邻域尖峰=过拟合特征）；只认全正 2×2 平台期（参数稳定区间）。
> 边界规则 B2/B3/B4/B7 同其他通道适用；正式上线须预注册（P2-11）+ 过 R1-R18 闸门 + 名单制。
"""
    (REPORTS_DIR / f"{stem}.md").write_text(md, encoding="utf-8")
    print(f"      📄 {REPORTS_DIR / f'{stem}.md'}")
    print(f"\n✅ 技术网格门禁裁决: {verdict}  ({datetime.now().strftime('%H:%M:%S')})")
    for r in reasons:
        print(f"    - {r}")
    return 0 if verdict == "PASS" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="屠龙 WFA 验证门禁流水线 V1")
    ap.add_argument("--combo", nargs="+", default=["bp_ep"],
                    help="PIT 因子组合（可用: droe/roa/ep/bp/bp_ep），默认 bp_ep")
    ap.add_argument("--start", default="20180101")
    ap.add_argument("--end", default="20260814")
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--fwd", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.001)
    ap.add_argument("--top-pct", type=float, default=0.10)
    ap.add_argument("--price-source", choices=["raw", "qfq"], default="raw",
                    help="价格源：raw=stock_daily_meta 原始收盘（默认，与生产一致）；qfq 为预留")
    ap.add_argument("--force", action="store_true", help="一票制拦截时强制重跑（记 violation）")
    # OOS 一票制锁（2026-08-26 O8）：锁定日后数据禁调参；只读验证加 --validate
    ap.add_argument("--validate", action="store_true",
                    help="OOS一票制：只读验证模式（允许读锁定日后数据，试验记 validate，禁据此调参）")
    ap.add_argument("--pbo", action="store_true",
                    help="深度扫描：在协议参数邻域生成变体矩阵，输出 PBO/CSCV 过拟合概率")
    ap.add_argument("--cpcv", action="store_true",
                    help="CPCV 多路径 OOS 验证（组合化交叉验证，IC 层面，WFA 的稳健性补充）")
    ap.add_argument("--cpcv-splits", type=int, default=6,
                    help="CPCV 时间块数（偶数，默认 6 → 10 条路径）")
    ap.add_argument("--prereg", metavar="JSON",
                    help="研究预注册文件路径（正式验证前置声明；模板 data/wfa_prereg_template.json）")
    ap.add_argument("--combos", help="批量验证：逗号分隔的多个因子组合，如 'bp_ep,droe roa,ep bp'")
    ap.add_argument("--no-log", action="store_true",
                    help="隔离模式（冒烟测试用）：不写试验日志/不回填预注册，报告加 _smoke 后缀")
    ap.add_argument("--ml", action="store_true",
                    help="ML 信号通道：复用 factor_ml_walkforward_v3 管线（qfq+PIT+purge/embargo），接入门禁裁决")
    ap.add_argument("--featureset", nargs="+", default=["D"], choices=["D", "E"])
    ap.add_argument("--model", nargs="+", default=["ridge"], choices=["ridge", "lgbm"])
    ap.add_argument("--max-train-samples", type=int, default=600_000)
    ap.add_argument("--warmup", type=int, default=90)
    ap.add_argument("--signals-dir", metavar="DIR",
                    help="信号名单验证（V1 机制版）：读 v4 信号 Top20 快照目录，验证名单后续超额（默认 reports/daily_signals）")
    ap.add_argument("--tech-grid", metavar="JSON",
                    help="技术网格通道：读参数扫描结果 JSON（{strategy,dims,fixed,cells}），按平台期结构裁决 PASS/WARN/FAIL")
    a = ap.parse_args()
    # OOS 一票制锁检查（2026-08-26 O8）：窗口止越过锁定日且非 --validate → exit 2
    try:
        import oos_lock
        _ok = oos_lock.check(a.end, validate=getattr(a, "validate", False), force=a.force,
                             strategy=",".join(a.combo))
        if not _ok.get("ok"):
            print(json.dumps(_ok, ensure_ascii=False))
            return 2
        if _ok.get("forced") or _ok.get("validate"):
            print(f"[oos_lock] {_ok.get('reason')}")
    except ImportError:
        pass
    if a.ml:
        return run_ml_gate(a)
    if a.signals_dir:
        return run_signals_gate(a)
    if a.tech_grid:
        return run_tech_grid_gate(a)
    if a.combos:
        combos = [c.strip().split() for c in a.combos.split(",") if c.strip()]
        if not combos:
            ap.error("--combos 为空")
        return run_batch(combos, a)
    return WfaGate(a).run()


if __name__ == "__main__":
    sys.exit(main())
