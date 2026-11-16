#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_wfa_gate_smoke.py — WFA 门禁回归冒烟测试
================================================================
目的：门禁是生产验证工具（影响策略上线的裁决），任何改动不得破坏
      裁决/一票制/预注册/批量/CPCV 逻辑。本测试在验收时运行（不常跑）。

分层：
  A. 单元测试（快，不跑数据；monkeypatch 日志到临时路径，零污染）
     - check_one_vote 一票制：已裁决拦截 / force 放行 / 无记录放行
     - protocol_fingerprint：普通模式与 V1 指纹一致（一票制持续）；参数/cpcv 变化=新指纹
     - check_prereg 预注册校验：匹配通过 / mismatch 拒绝 / completed 拒绝复用
     - complete_prereg 回填：文件回填 + 日志追加
  B. 端到端（短区间 2022-2024 + --no-log 隔离，不写正式试验日志）
     - 普通裁决可跑且出报告（_smoke 后缀）
     - 批量 --combos 出汇总（_smoke 后缀）
     - 预注册 fail-closed：mismatch → exit 2

用法: python tests/test_wfa_gate_smoke.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tulong_wfa_gate as g  # noqa: E402

GATE = [sys.executable, str(ROOT / "tulong_wfa_gate.py")]
SHORT = ["--start", "20220101", "--end", "20240830",
         "--train", "200", "--test", "40", "--no-log"]


class TestUnit(unittest.TestCase):
    def setUp(self):
        # 隔离日志：monkeypatch 到临时目录
        self._tmp = Path(tempfile.mkdtemp(prefix="wfa_gate_test_"))
        self._old_trials = g.TRIALS_LOG
        self._old_prereg = g.PREREG_LOG
        g.TRIALS_LOG = self._tmp / "trials.jsonl"
        g.PREREG_LOG = self._tmp / "prereg.jsonl"

    def tearDown(self):
        g.TRIALS_LOG = self._old_trials
        g.PREREG_LOG = self._old_prereg

    # ── 1) 一票制 ──
    def test_one_vote_blocked(self):
        g.append_trial({"fingerprint": "fp1", "verdict": "PASS", "ts": "x"})
        status, last = g.check_one_vote("fp1", force=False)
        self.assertEqual(status, "blocked")
        self.assertEqual(last["verdict"], "PASS")

    def test_one_vote_force(self):
        g.append_trial({"fingerprint": "fp2", "verdict": "WARN", "ts": "x"})
        status, _ = g.check_one_vote("fp2", force=True)
        self.assertEqual(status, "force")

    def test_one_vote_fresh(self):
        status, last = g.check_one_vote("nope", force=False)
        self.assertEqual(status, "ok")
        self.assertIsNone(last)

    # ── 2) 协议指纹 ──
    def test_fingerprint_stable_and_compat(self):
        # 普通模式指纹与 V1（无 cpcv 键的 payload）一致 → 一票制持续
        fp = g.protocol_fingerprint(["bp_ep"], 500, 60, 20, 0.001, "20180101", "20260814", 0.1, "raw")
        payload_v1 = {"engine": g.ENGINE_VERSION, "combo": ["bp_ep"], "train": 500, "test": 60,
                      "fwd": 20, "cost": 0.001, "top_pct": 0.1, "start": "20180101",
                      "end": "20260814", "price_source": "raw"}
        raw = json.dumps(payload_v1, sort_keys=True, ensure_ascii=False)
        self.assertEqual(fp, __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()[:20])

    def test_fingerprint_changes(self):
        fp_a = g.protocol_fingerprint(["bp_ep"], 500, 60, 20, 0.001, "20180101", "20260814", 0.1, "raw")
        fp_b = g.protocol_fingerprint(["bp_ep"], 400, 60, 20, 0.001, "20180101", "20260814", 0.1, "raw")
        fp_c = g.protocol_fingerprint(["bp_ep"], 500, 60, 20, 0.001, "20180101", "20260814", 0.1, "raw", cpcv=True)
        self.assertNotEqual(fp_a, fp_b)
        self.assertNotEqual(fp_a, fp_c)
        self.assertEqual(fp_a, g.protocol_fingerprint(
            ["bp_ep"], 500, 60, 20, 0.001, "20180101", "20260814", 0.1, "raw"))

    # ── 3) 预注册校验 ──
    def test_prereg_check(self):
        params = {"train": 500, "test": 60, "fwd": 20, "cost": 0.001, "top_pct": 0.1,
                  "start": "20180101", "end": "20260814"}
        good = {"combo": ["bp_ep"], "params": params, "engine": g.ENGINE_VERSION, "status": "pending"}
        self.assertEqual(g.check_prereg(good, ["bp_ep"], params, g.ENGINE_VERSION), [])
        bad = dict(good, combo=["droe", "roa"])
        self.assertTrue(g.check_prereg(bad, ["bp_ep"], params, g.ENGINE_VERSION))
        done = dict(good, status="completed")
        self.assertTrue(g.check_prereg(done, ["bp_ep"], params, g.ENGINE_VERSION))

    # ── 4) 预注册回填 ──
    def test_prereg_complete(self):
        p = {"id": "t", "combo": ["bp_ep"], "status": "pending"}
        path = self._tmp / "prereg.json"
        path.write_text(json.dumps(p), encoding="utf-8")
        g.complete_prereg(str(path), p, "PASS", {"cagr": 0.1}, None, None)
        back = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(back["status"], "completed")
        self.assertEqual(back["result"]["verdict"], "PASS")
        self.assertTrue(g.PREREG_LOG.exists())


class TestE2E(unittest.TestCase):
    def _run(self, args, allowed_rc):
        r = subprocess.run(GATE + SHORT + args, capture_output=True, text=True, timeout=420)
        self.assertIn(r.returncode, allowed_rc,
                      f"rc={r.returncode} 期望 {allowed_rc}\nstdout:\n{r.stdout[-1200:]}\nstderr:\n{r.stderr[-800:]}")
        return r

    def test_plain_verdict(self):
        self._run(["--combo", "ep"], (0, 1))
        self.assertTrue(list((ROOT / "reports").glob("wfa_gate_ep_smoke_*.md")),
                        "普通模式应产出 _smoke 报告")

    def test_batch(self):
        self._run(["--combos", "ep,bp"], (0, 1))
        self.assertTrue(list((ROOT / "reports").glob("wfa_gate_batch_smoke_*.md")),
                        "批量模式应产出 _smoke 汇总")

    def test_prereg_mismatch_failclosed(self):
        p = {"id": "e2e-mismatch", "combo": ["bp_ep"], "params": {
            "train": 200, "test": 40, "fwd": 20, "cost": 0.001, "top_pct": 0.1,
            "start": "20220101", "end": "20240830"},
            "engine": g.ENGINE_VERSION, "status": "pending"}
        path = ROOT / "data" / "prereg_e2e_mismatch.json"
        path.write_text(json.dumps(p), encoding="utf-8")
        try:
            r = subprocess.run(GATE + SHORT + ["--combo", "ep", "--prereg", str(path)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, f"fail-closed 应 exit 2\n{r.stdout[-800:]}")
        finally:
            path.unlink(missing_ok=True)

    def test_ml_verdict(self):
        # ML 通道短区间（无 --no-log 参数之外的冲突）：qfq+PIT+purge/embargo 管线能跑出裁决
        r = subprocess.run(GATE + ["--ml", "--featureset", "D", "--model", "ridge",
                                   "--start", "20220101", "--end", "20240830",
                                   "--train", "200", "--test", "40", "--no-log"],
                           capture_output=True, text=True, timeout=420)
        self.assertIn(r.returncode, (0, 1),
                      f"ML 通道应出裁决 rc in (0,1)，got {r.returncode}\n{r.stdout[-1200:]}\n{r.stderr[-800:]}")
        self.assertTrue(list((ROOT / "reports").glob("wfa_gate_ml_D_ridge_smoke_*.md")),
                        "ML 通道应产出 _smoke 报告")

    def test_signals_gate(self):
        # 信号名单验证（V1 机制版）：读现有 signal_*.csv 快照，产出名单验证报告
        r = subprocess.run(GATE + ["--signals-dir", str(ROOT / "reports" / "daily_signals"),
                                   "--no-log"], capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, f"信号验证应 rc=0\n{r.stdout[-1000:]}\n{r.stderr[-600:]}")
        self.assertTrue(list((ROOT / "reports").glob("wfa_gate_signals_smoke_*.md")),
                        "信号验证应产出 _smoke 报告")


if __name__ == "__main__":
    unittest.main(verbosity=2)
