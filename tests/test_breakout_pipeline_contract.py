# -*- coding: utf-8 -*-
"""突破主线管线·契约烟测(20261027) — 锁死 builder/守卫的结构不变量, 防未来改动碰坏每日自动执行链。
依赖真实数据(与生产同源); 断言全部为结构不变量(与当日候选内容无关):
  ①builder --dry: 双臂产物齐备, 每臂≤5席, A∩B=∅(当日幂等约束), 定价=close*1.01/股数规则/meta完整(r31旗/regime)
  ②guard --execute-guard-dry --arm a|b: 走完整守卫逻辑但零提交(Skip路径或GUARD_DRY路径, 永不触executor)
用法: python tests/test_breakout_pipeline_contract.py
注意: 本测试不触真实批次文件(--dry/dry-guard均只读), 可在任何时段安全运行。
"""
import json
import os
import subprocess
import sys

BASE = r"D:\zcodesj"
PY = sys.executable
DRY = os.path.join(BASE, "logs", "_pipeline_dry")


def run_pipeline(args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([PY, os.path.join(BASE, "analysis", "breakout_batch_pipeline.py")] + args,
                          capture_output=True, text=True, cwd=BASE, env=env,
                          encoding="utf-8", errors="replace")


def test_contract():
    r = run_pipeline(["--dry"])
    assert r.returncode == 0, f"builder --dry rc={r.returncode}: {(r.stderr or '')[-300:]}"
    bb = json.load(open(os.path.join(DRY, "batch_exec_breakout.json"), encoding="utf-8"))
    ba = json.load(open(os.path.join(DRY, "batch_a.json"), encoding="utf-8"))
    mb = json.load(open(os.path.join(DRY, "meta.json"), encoding="utf-8"))
    ma = json.load(open(os.path.join(DRY, "meta_a.json"), encoding="utf-8"))
    # 席位不变量
    for entries, source, arm, meta in ((bb, "breakout_mainline", "B", mb), (ba, "breakout_ab_A", "A", ma)):
        assert 0 < len(entries) <= 5, f"[{arm}] 席位数异常: {len(entries)}"
        codes = [e["code"] for e in entries]
        assert len(set(codes)) == len(codes), f"[{arm}] 席位重复"
        for e in entries:
            assert e["source"] == source, f"[{arm}] source错: {e}"
            # 2026-11-03 断根令反转: 计划条目禁止携带主人批准旗(批准=CLI显式旗标/master_confirmed,
            # 计划文件自授权通道已焊死=trade_executor BUY_ARG_FIELDS 移除+_OWNER_ONLY_PLAN_KEYS拒单)
            for _k in ("owner_approve_ddgate", "owner_approve_p2", "owner_approve_r31", "owner_override_session"):
                assert _k not in e, f"[{arm}] 计划条目携带批准旗 {_k} = 断根令违规"
            close = float(e["prev_close"])
            assert e["volume"] == (200 if close < 15 else 100), f"[{arm}] {e['code']} 股数违规则"
            assert e["price"] == round(close * 1.01, 2), f"[{arm}] {e['code']} 定价偏离 close+1%"
            assert 2 <= close <= 30, f"[{arm}] {e['code']} 价格带外"
        assert meta["arm"] == arm and meta["executed"] is False
        assert int(meta["signal_date"]) > 0
        assert meta.get("regime") in ("趋势市up", "下跌市down", "震荡市range", "过渡trans", "N/A")
    b_codes = {e["code"] for e in bb}
    a_codes = {e["code"] for e in ba}
    assert not (a_codes & b_codes), f"A/B臂当日票集相交: {a_codes & b_codes} (executor当日幂等约束)"
    # 守卫dry路径: 双臂走完整守卫逻辑但零提交
    for arm in ("a", "b"):
        g = run_pipeline(["--execute-guard-dry", "--arm", arm])
        assert g.returncode == 0, f"guard[{arm}] rc={g.returncode}"
        out = g.stdout
        assert ("SKIP[" in out) or ("GUARD_PASS[" in out and "GUARD_DRY[" in out), \
            f"guard[{arm}] 输出既非SKIP也非GUARD_DRY: {out[-200:]}"
        assert "EXEC[" not in out, f"guard[{arm}] dry模式出现了EXEC提交!"
    print(f"契约烟测 PASS | B臂={sorted(b_codes)} A臂={sorted(a_codes)} | regime={mb.get('regime')}")


if __name__ == "__main__":
    test_contract()
    print("ALL PASS")
