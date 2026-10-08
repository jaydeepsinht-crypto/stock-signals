# Stock Signal Dashboard (Blueprint v1.0)

A 25-factor stock scoring model that pulls data from **finviz.com**. It outputs a 0–100 score and a gated BUY/HOLD/SELL signal. For each stock it also gives fair value, entry zone, 12-month target, ATR stop, risk/reward and a risk-budget position size.

## Quick start

```bash
pip install -r requirements.txt
python run_daily.py                       # fetch the default 40-stock universe from Finviz, score, store
python run_daily.py --tickers AAPL NVDA JPM --portfolio 250000 --risk 0.005
streamlit run app.py                      # interactive dashboard (live Finviz data, falls back to saved snapshot)
pytest -q                                 # 16 tests: blueprint examples, factor tables, Finviz parity
```

Re-score saved data offline with `python run_daily.py --snapshot finviz_snapshot_2026-10-07.json`.

## What comes from Finviz

| Source | Endpoint | Used for |
|---|---|---|
| Quote snapshot | `quote.ashx?t=XXX` | P/E, Fwd P/E, PEG, P/FCF, EV/EBITDA, ROE, ROIC, margins, Debt/Eq, EPS growth (past 3Y, next Y, next 5Y), Sales past 3Y, RSI, ATR, Beta, SMA distances, Perf Half/Year, target price, earnings date, EPS surprise |
| Annual statements | `api/statement.ashx?s=IA/CA/BA` | revenue, net income, EBITDA, FCF (TTM + 3 years), operating-margin trend, share-count dilution, cash and debt |
| Daily OHLCV (~10y) | `api/quote.ashx?timeframe=d` | 50/200-DMA and slope, MACD, 1-year max drawdown, realised volatility, 20-day support, volume ratio |
| Screener counts | `screener.ashx?f=idx_sp500,ta_sma200_pa` | S&P 500 breadth for the market-regime gate |

Requests are spaced (default 0.6 s), retried on HTTP 429 and cached per day. Respect Finviz's terms of use. For heavy use, Finviz Elite offers an official export.

## Layout (all files in one folder)

```
app.py           Streamlit app (deploy this as the main file)
config.py        weights, thresholds, valuation and trading parameters  <- tune here
finviz.py        Finviz HTTP client (snapshot, statements, OHLC, breadth)
features.py      raw Finviz -> flat feature dict
factors.py       the 25 factor functions
valuation.py     DCF, fair multiples, blended fair value, target
trade.py         entry, stop, R:R, position size, trailing plan
engine.py        regime, category scores, gates, confidence, red flags, explanations
db.py            SQLite store (schema mirrors PostgreSQL)
snapshot_io.py   load/save data snapshots
run_daily.py     daily pipeline CLI
test_model.py    tests (pytest)
```

## Model choices beyond the blueprint (all in `config.py`)

- **Missing data** is never zero. The factor is marked MISSING / N/A / ESTIMATED and its weight is redistributed within its category. If more than 20% of the key inputs are missing, the signal is `DATA INSUFFICIENT`.
- **Industry-relative factors** (gross margin, operating margin, P/FCF, median multiples) use sector peers when at least 5 are in the universe, otherwise the whole universe. Score a bigger universe (e.g. the S&P 500) for sharper percentiles.
- **DCF**: 10 years on TTM FCF. Growth is 80% of blended consensus (60% EPS next 5Y + 40% next Y), capped at 15%, fading to 3% terminal. WACC = 4.5% + β×5.5%, clamped to 8.5–12%. Each fair-value component is winsorised to 0.33–3× price.
- **Banks/insurers**: FCF, EBITDA and DCF factors are disabled. Fair value uses justified P/B, a bank fair P/E (8–16×) and PEG.
- **Executable entry**: if price is already below the model entry, stop, R:R and size are computed from the market price.
- **Accumulate** (65–75): valuation ≥ 50, technical ≥ 55, R:R ≥ 1.5, in buy zone, not a bear market.
- **Red flags**: >10%/yr dilution, net debt > 6× EBITDA, negative EBITDA with net debt, or current ratio < 0.5 are severe and block any buy. Cash burn and short interest > 20% are flagged but not blocking.

## Known limits

- **Estimate revisions**: Finviz has no revision history, so the factor uses last quarter's EPS surprise ÷ 2, flagged ESTIMATED. Plug real 30/90-day revisions into `eps_rev_30d` / `eps_rev_90d` to replace it.
- **Backtesting (Phase 6)**: Finviz has no point-in-time fundamentals, so a backtest on this data would carry look-ahead bias. A valid backtest needs a point-in-time vendor (e.g. Compustat, Sharadar). The DB stores every signal with its model version, so a forward (paper-trading) record builds up from day one.
- Research and decision-support tool only. Not investment advice.
