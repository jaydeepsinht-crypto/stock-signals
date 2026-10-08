#!/usr/bin/env python3
"""Daily pipeline (Blueprint s.80): download -> features -> regime -> score -> levels -> signals -> store.

Examples
--------
  python run_daily.py                                  # default universe, live Finviz fetch
  python run_daily.py --tickers AAPL MSFT NVDA          # custom list
  python run_daily.py --tickers-file sp500.txt --portfolio 250000
  python run_daily.py --snapshot output/finviz_snapshot_2026-10-07.json   # re-score saved data offline
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from db import Store
from engine import analyze_universe
from snapshot_io import load_snapshot, save_snapshot

DEFAULT_UNIVERSE = ("AAPL MSFT NVDA GOOGL AMZN META AVGO TSLA LLY JPM V MA COST NFLX AMD ORCL CRM ADBE UNH XOM "
                    "JNJ PG HD KO PEP MRK ABBV WMT CAT PLTR GE NOW INTU QCOM TXN CVX BAC ISRG BKNG UBER").split()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--tickers-file")
    ap.add_argument("--snapshot", help="score a saved snapshot instead of fetching")
    ap.add_argument("--portfolio", type=float, default=None)
    ap.add_argument("--risk", type=float, default=None, help="risk per trade, e.g. 0.0075")
    ap.add_argument("--out", default=".")
    ap.add_argument("--db", default="stock_signal.db")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between Finviz requests")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(exist_ok=True)
    if a.snapshot:
        data = load_snapshot(a.snapshot)
    else:
        from finviz import FinvizClient
        tickers = a.tickers or (Path(a.tickers_file).read_text().split() if a.tickers_file else DEFAULT_UNIVERSE)
        client = FinvizClient(delay=a.delay, cache_dir=out / "cache")
        data = client.universe_features(
            tickers, progress=lambda i, n, t: print(f"[{i}/{n}] {t}", flush=True))
        save_snapshot(data, out / f"features_{dt.date.today()}.json")
        if data["errors"]:
            print("Errors:", data["errors"])

    res = analyze_universe(data, portfolio_value=a.portfolio, risk_pct=a.risk)
    (out / "results_latest.json").write_text(json.dumps(res, default=str, indent=1))
    n = Store(a.db).save_run(res, data["results"])

    print(f"\nMarket regime: {res['regime']['regime']}  (breadth {res['regime']['breadth_pct']})")
    print(f"{'#':>2} {'TICKER':6} {'SCORE':>5}  {'SIGNAL':18} {'PRICE':>9} {'ENTRY':>9} {'TARGET':>9} {'STOP':>9} {'R:R':>5}")
    for i, s in enumerate(res["stocks"][:15], 1):
        t = s["trade"]
        fmt = lambda x: f"{x:9.2f}" if x else "        -"  # noqa: E731
        rr = f"{t['risk_reward']:5.2f}" if t["risk_reward"] is not None else "    -"
        print(f"{i:2} {s['ticker']:6} {s['score']:5.1f}  {s['signal']:18} {fmt(s['price'])} {fmt(t.get('entry'))} "
              f"{fmt(t.get('target'))} {fmt(t.get('stop'))} {rr}")
    print(f"\nSaved {n} signals -> {a.db}; results -> {out / 'results_latest.json'}")


if __name__ == "__main__":
    main()
