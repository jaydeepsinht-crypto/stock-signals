"""Tests: blueprint worked examples, factor tables, and Python-vs-browser feature parity on real Finviz data."""
import datetime as dt
import json
import math
from pathlib import Path

import pytest

from features import build_features, snapshot_pairs, parse_num
import factors as F
import trade as T
from engine import analyze_universe, days_to_earnings, market_regime
from snapshot_io import load_snapshot

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------- blueprint examples
def test_roic_example():                       # s.10: ROIC 18% -> 90
    assert F.f_roic({"roic": 18}).score == 90


def test_peg_example_table():                  # s.24: P/E 25, growth 20 -> PEG 1.25 -> 60
    assert F.f_peg({"peg": 25 / 20}).score == 60


def test_mos_example():                        # s.27: FV 200, price 150 -> MOS 25% -> 90
    assert F.f_dcf_mos((200 - 150) / 200, "AVAILABLE").score == 90


def test_entry_example():                      # s.45-46: FV 200, score 88 -> 180; blend with 175 -> 178
    assert T.required_mos(88) == 0.10
    assert 0.6 * 200 * (1 - T.required_mos(88)) + 0.4 * 175 == pytest.approx(178)


def test_stop_example():                       # s.51: entry 100, ATR 4, support 92 -> stop 88
    assert T.stop_loss({"atr": 4, "support20": 92}, 100) == 88


def test_rr_example():                         # s.56: (135-100)/(100-88) = 2.92
    assert T.risk_reward(100, 88, 135) == pytest.approx(2.9167, abs=1e-3)


def test_position_example():                   # s.53: $100k, 0.75%, risk/share 12 -> 62 shares
    p = T.position_size({"rvol_1y": 0.2, "beta": 1}, "BUY", 100, 88, "BULL", None, 100_000, 0.0075)
    assert p["shares"] == 62 and p["pct"] == pytest.approx(0.062)


def test_position_caps_and_gates():
    # Strong buy capped at 10%, volatile capped at 5%, neutral x0.8, earnings x0.25, hold = 0
    p = T.position_size({"rvol_1y": 0.6}, "STRONG BUY", 100, 99, "BULL", None, 100_000, 0.0075)
    assert p["shares"] == 50
    p = T.position_size({"rvol_1y": 0.2}, "BUY", 100, 88, "NEUTRAL", 1, 100_000, 0.0075)
    assert p["shares"] == math.floor(62 * 0.8 * 0.25)
    assert T.position_size({}, "HOLD", 100, 88, "BULL", None)["shares"] == 0


def test_macd_points_total_100():
    f = dict(macd=2, macd_signal=1, macd_hist=1, macd_hist_prev=0.5, ma50=10, ma200=9, ma200_prev20=8.5, price=11)
    assert F.f_macd_trend(f).score == 100


def test_rsi_prefers_50_65():
    assert F.f_rsi({"rsi": 60}).score == 100 and F.f_rsi({"rsi": 80}).score == 40


def test_missing_is_not_zero():
    r = F.f_roic({})
    assert r.score is None and r.status == "MISSING"


def test_regime():
    bull = market_regime({"price": 110, "ma200": 100, "ma200_prev20": 99}, {"pct": 60})
    bear = market_regime({"price": 90, "ma200": 100, "ma200_prev20": 101}, {"pct": 30})
    neut = market_regime({"price": 110, "ma200": 100, "ma200_prev20": 99}, {"pct": 45})
    assert (bull["regime"], bear["regime"], neut["regime"]) == ("BULL", "BEAR", "NEUTRAL")


def test_days_to_earnings_year_rollover():
    assert days_to_earnings("Jan 05 BMO", dt.date(2026, 12, 30)) == 6
    assert days_to_earnings("Oct 08 BMO", dt.date(2026, 10, 7)) == 1


def test_parse_num():
    assert parse_num("5723.03B") == pytest.approx(5.72303e12)
    assert parse_num("22.83% 17.38%") == 22.83
    assert parse_num("-") is None


# ---------------------------------------------------- parity with the browser collector
def test_feature_parity_msft():
    d = json.loads((ROOT / "msft_raw.json").read_text())
    n = len(d["close"])
    low = [d["low_last60"][0]] * (n - 60) + d["low_last60"][-60:]
    vol = [d["volume_last21"][0]] * (n - 21) + d["volume_last21"]
    ohlc = {"close": d["close"], "low": low, "volume": vol, "date": [d["ohlc_last_date"]]}
    snap = {**d["snap"], "kv": snapshot_pairs(d["snap"]["cells"])}
    f = build_features("MSFT", snap, d["ia"], d["ca"], d["ba"], ohlc)
    for k, v in d["browser_features"].items():
        tol = 0.02 if k in ("macd", "macd_signal") else 1e-4   # EMA seed differs (300 vs 2522 bars)
        assert f[k] == pytest.approx(v, rel=tol), k


# ------------------------------------------------------------ full run on real data
def test_full_universe_run():
    data = load_snapshot(ROOT / "finviz_snapshot_2026-10-07.json")
    res = analyze_universe(data, today=dt.date(2026, 10, 7))
    assert len(res["stocks"]) == 40
    for s in res["stocks"]:
        assert 0 <= s["score"] <= 100
        w = {"quality": .25, "growth": .2, "valuation": .2, "technical": .2, "risk": .15}
        assert s["score"] == pytest.approx(sum(w[c] * s["categories"][c] for c in w), abs=0.11)
        if s["signal"] in ("STRONG BUY", "BUY", "BUY / ACCUMULATE"):
            t = s["trade"]
            assert t["stop"] < t["exec_entry"] < t["target"] and t["risk_reward"] >= 1.5
            assert t["position"]["pct"] <= 0.10 + 1e-9
