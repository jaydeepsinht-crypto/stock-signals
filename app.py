"""Stock Signal Dashboard — Streamlit UI (Blueprint s.62-63, 73, 81, 86).

Run:  streamlit run app.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import analyze_universe  # noqa: E402
from snapshot_io import load_snapshot, save_snapshot  # noqa: E402
from run_daily import DEFAULT_UNIVERSE  # noqa: E402

OUT = ROOT
SIG_COLOR = {"STRONG BUY": "#0b8a4a", "BUY": "#1fa35c", "BUY / ACCUMULATE": "#b5891a", "HOLD": "#7a7f87",
             "WEAK HOLD": "#c46a1b", "SELL": "#c0392b", "STRONG SELL": "#8e1b12",
             "AVOID (RED FLAG)": "#8e1b12", "DATA INSUFFICIENT": "#555"}

st.set_page_config(page_title="Stock Signal Dashboard", layout="wide")


# ------------------------------------------------------------------ data
REFRESH_HOURS = 6   # live data is re-pulled from Finviz at most this often (shared by all visitors)


@st.cache_data(ttl=REFRESH_HOURS * 3600, show_spinner=False)
def fetch_live(tickers: tuple[str, ...]) -> dict:
    from finviz import FinvizClient
    client = FinvizClient(cache_dir=None)
    bar = st.progress(0.0, text="Pulling fresh data from Finviz (about 2-3 minutes)…")
    data = client.universe_features(list(tickers), progress=lambda i, n, t: bar.progress(i / n, text=f"Finviz: {t} ({i}/{n})"))
    bar.empty()
    if data.get("benchmark", "SPY") not in data["results"] or len(data["results"]) < 2:
        raise RuntimeError("Finviz returned no usable data: " + "; ".join(f"{k}: {v}" for k, v in list(data["errors"].items())[:3]))
    try:
        save_snapshot(data, OUT / f"features_{dt.date.today()}.json")
    except OSError:
        pass
    return data


def latest_snapshot() -> Path | None:
    files = sorted(OUT.glob("features_*.json")) + sorted(OUT.glob("finviz_snapshot_*.json"))
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


# Optional password: set APP_PASSWORD in the host's secrets to keep the app private.
try:
    _pw = st.secrets.get("APP_PASSWORD")
except Exception:
    _pw = None
if _pw and st.session_state.get("auth") != _pw:
    st.title("Stock Signal Dashboard")
    entered = st.text_input("Password", type="password")
    if entered == _pw:
        st.session_state["auth"] = entered
        st.rerun()
    st.stop()

with st.sidebar:
    st.header("Data")
    live = st.toggle("Live Finviz data", value=True,
                     help=f"Pulls fresh data from Finviz, re-used for {REFRESH_HOURS} hours. Off = last saved snapshot.")
    tickers_txt = st.text_area("Universe (space separated)", " ".join(DEFAULT_UNIVERSE), height=120)
    if st.button("Refresh now", disabled=not live):
        fetch_live.clear()
    st.header("Portfolio")
    pv = st.number_input("Portfolio value ($)", 1_000.0, 1e9, 100_000.0, step=10_000.0)
    risk = st.slider("Risk per trade (%)", 0.25, 2.0, 0.75, 0.05) / 100

data, source_note = None, ""
if live:
    try:
        data = fetch_live(tuple(sorted(set(tickers_txt.upper().split()))))
        source_note = "Live Finviz data"
    except Exception as e:  # e.g. Finviz blocking the host's IP: fall back instead of failing
        st.warning(f"Could not pull live data from Finviz ({e}). Showing the last saved snapshot instead.")
if data is None:
    p = latest_snapshot()
    if not p:
        st.error("No live data and no saved snapshot found.")
        st.stop()
    data = load_snapshot(p)
    source_note = f"Saved snapshot ({p.name})"

res = analyze_universe(data, portfolio_value=pv, risk_pct=risk)
stocks = {s["ticker"]: s for s in res["stocks"]}
reg = res["regime"]

# ------------------------------------------------------------------ header
st.title("Stock Signal Dashboard")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Market regime", reg["regime"])
c2.metric("SPY vs 200-DMA", f"{(reg['spy_price'] / reg['spy_ma200'] - 1) * 100:+.1f}%",
          "200-DMA rising" if reg["spy_200dma_rising"] else "200-DMA falling")
c3.metric("S&P 500 above 200-DMA", f"{reg['breadth_pct']:.0f}%" if reg["breadth_pct"] is not None else "n/a")
c4.metric("Data as of", str(res["asof"])[:16].replace("T", " "), source_note, delta_color="off")

tab_top, tab_stock, tab_cmp, tab_heat = st.tabs(["Top opportunities", "Stock detail", "Compare", "Factor heatmap"])


def table(rows):
    df = pd.DataFrame([{
        "Ticker": s["ticker"], "Name": s["name"], "Sector": s["sector"], "Score": s["score"], "Signal": s["signal"],
        "Quality": s["categories"]["quality"], "Growth": s["categories"]["growth"],
        "Valuation": s["categories"]["valuation"], "Technical": s["categories"]["technical"],
        "Risk": s["categories"]["risk"], "Price": s["price"], "Fair value": s["valuation"]["fair_value"],
        "Buy zone": f"{s['trade'].get('zone_low', 0):.2f}–{s['trade'].get('zone_high', 0):.2f}",
        "Target": s["trade"]["target"], "Stop": s["trade"]["stop"], "R:R": s["trade"]["risk_reward"],
        "Shares": s["trade"]["position"]["shares"], "Confidence": s["confidence"]} for s in rows])
    return df


with tab_top:
    st.subheader("Ranked universe")
    st.dataframe(table(res["stocks"]), hide_index=True, width="stretch",
                 column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
                                **{k: st.column_config.NumberColumn(format="%.2f")
                                   for k in ("Price", "Fair value", "Target", "Stop", "R:R")}})

with tab_stock:
    t = st.selectbox("Ticker", list(stocks))
    s = stocks[t]
    tr, v = s["trade"], s["valuation"]
    a, b = st.columns([1, 2])
    with a:
        st.markdown(f"### {t} · {s['name']}")
        st.markdown(f"<div style='font-size:42px;font-weight:700'>{s['score']:.1f}<span style='font-size:18px'>/100</span></div>"
                    f"<div style='color:white;background:{SIG_COLOR.get(s['signal'], '#555')};display:inline-block;"
                    f"padding:4px 10px;border-radius:6px;font-weight:600'>{s['signal']}</div>", unsafe_allow_html=True)
        for n in s["signal_notes"]:
            st.caption("• " + n)
        st.write(f"Confidence **{s['confidence']:.0f}%** · model {s['model_version']}")
        lv = pd.DataFrame({"": ["Price", "Fair value", "Ideal entry", "Buy zone", "12-month target", "Stop loss",
                                "Risk / reward", "Suggested shares", "Position"],
                           "Value": [f"${s['price']:.2f}", f"${v['fair_value']:.2f}" if v["fair_value"] else "-",
                                     f"${tr['entry']:.2f}" if tr.get("entry") else "-",
                                     f"${tr['zone_low']:.2f} – ${tr['zone_high']:.2f}" if tr.get("zone_low") else "-",
                                     f"${tr['target']:.2f}" if tr["target"] else "-",
                                     f"${tr['stop']:.2f}" if tr["stop"] else "-",
                                     f"{tr['risk_reward']:.2f} : 1" if tr["risk_reward"] else "-",
                                     str(tr["position"]["shares"]),
                                     f"{tr['position']['pct'] * 100:.1f}% (${tr['position']['value']:,.0f})"]})
        st.table(lv.set_index(""))
    with b:
        cats = s["categories"]
        fig = go.Figure(go.Bar(x=list(cats.values()), y=[c.title() for c in cats], orientation="h",
                               text=[f"{x:.0f}" for x in cats.values()], marker_color="#2f6fdf"))
        fig.update_layout(height=230, margin=dict(l=0, r=0, t=10, b=0), xaxis_range=[0, 100])
        st.plotly_chart(fig, width="stretch")
        p1, p2 = st.columns(2)
        p1.markdown("**Why**\n" + "\n".join(f"- ✓ {x}" for x in s["positives"]) if s["positives"] else "**Why**\n- none")
        p2.markdown("**Risks**\n" + "\n".join(f"- ⚠ {x}" for x in s["risks"]) if s["risks"] else "**Risks**\n- none")
    st.markdown("#### 25 factors")
    st.dataframe(pd.DataFrame([{"Factor": f["label"], "Weight": f["weight"], "Score": f["score"], "Raw": f["raw"],
                                "Status": f["status"], "Note": f["note"]} for f in s["factors"].values()]),
                 hide_index=True, width="stretch")
    st.markdown("#### Valuation build-up")
    st.json({"growth used (%)": v["growth_used"], "WACC": v["wacc"], "fair multiples": v["fair_multiples"],
             "per-share components": v["components"]}, expanded=False)
    if tr.get("trailing"):
        st.markdown("#### Trade management")
        tp = tr["trailing"]
        st.write(f"+1R ${tp['plus_1R']['price']:.2f} → {tp['plus_1R']['action']} · "
                 f"+2R ${tp['plus_2R']['price']:.2f} → {tp['plus_2R']['action']} · "
                 f"+3R ${tp['plus_3R']['price']:.2f} → {tp['plus_3R']['action']}")

with tab_cmp:
    pick = st.multiselect("Compare", list(stocks), default=list(stocks)[:3])
    if pick:
        rows = {}
        for t in pick:
            s = stocks[t]
            rows[t] = {"Total score": s["score"], **{k.title(): v for k, v in s["categories"].items()},
                       "Fair value": s["valuation"]["fair_value"], "Entry": s["trade"].get("entry"),
                       "Target": s["trade"]["target"], "R:R": s["trade"]["risk_reward"], "Signal": s["signal"],
                       "Confidence": s["confidence"]}
        st.dataframe(pd.DataFrame(rows).map(lambda x: f"{x:.2f}" if isinstance(x, float) else ("-" if x is None else str(x))), width="stretch")

with tab_heat:
    keys = list(next(iter(stocks.values()))["factors"])
    z = [[stocks[t]["factors"][k]["score"] for k in keys] for t in stocks]
    fig = go.Figure(go.Heatmap(z=z, x=[stocks[next(iter(stocks))]["factors"][k]["label"] for k in keys],
                               y=list(stocks), colorscale="RdYlGn", zmin=0, zmax=100))
    fig.update_layout(height=28 * len(stocks) + 150, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

st.caption("Decision-support research model, not investment advice. Data: finviz.com. "
           "Estimate revisions use an EPS-surprise proxy (Finviz has no revision history).")
