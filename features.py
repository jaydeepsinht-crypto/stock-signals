"""Turn raw Finviz payloads (snapshot table, statements, daily OHLC) into a flat feature dict.

All percentages from the Finviz snapshot stay in percentage points (e.g. ROE 25.3 = 25.3 %).
Statement values are in millions (Finviz convention). Shares (shs_out) are absolute.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

SNAPSHOT_MAP = {
    "price": "Price", "market_cap": "Market Cap", "ev": "Enterprise Value", "pe": "P/E",
    "fwd_pe": "Forward P/E", "peg": "PEG", "ps": "P/S", "pfcf": "P/FCF", "ev_ebitda": "EV/EBITDA",
    "eps_ttm": "EPS (ttm)", "fwd_eps": "EPS next Y", "eps_this_y": "EPS this Y",
    "eps_next_y_g": "EPS next Y_2", "eps_next_5y": "EPS next 5Y", "eps_past_3y": "EPS past 3/5Y",
    "sales_past_3y": "Sales past 3/5Y", "roe": "ROE", "roic": "ROIC", "roa": "ROA",
    "gross_margin": "Gross Margin", "oper_margin": "Oper. Margin", "profit_margin": "Profit Margin",
    "debt_eq": "Debt/Eq", "current_ratio": "Current Ratio", "sma20": "SMA20", "sma50": "SMA50",
    "sma200": "SMA200", "rsi": "RSI (14)", "atr": "ATR (14)", "beta": "Beta",
    "rel_volume": "Rel Volume", "avg_volume": "Avg Volume", "perf_half": "Perf Half Y",
    "perf_year": "Perf Year", "target_price": "Target Price", "recom": "Recom",
    "short_float": "Short Float", "insider_trans": "Insider Trans", "shs_out": "Shs Outstand",
}

_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_num(s: Any) -> float | None:
    """'5723.03B' -> 5.72303e12, '74.67%' -> 74.67, '-' -> None. Uses the first token only."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    s = s.split()[0]
    if s == "-":
        return None
    mult = 1.0
    if s[-1] in _MULT:
        mult = _MULT[s[-1]]
        s = s[:-1]
    s = s.replace(",", "").replace("%", "")
    try:
        return float(s) * mult
    except ValueError:
        return None


def parse_second(s: Any) -> float | None:
    if s is None:
        return None
    parts = str(s).strip().split()
    return parse_num(parts[1]) if len(parts) > 1 else None


def snapshot_pairs(cells: list[str]) -> dict[str, str]:
    """Pair label/value cells; a repeated label (Finviz has two 'EPS next Y') gets a '_2' suffix."""
    kv: dict[str, str] = {}
    for i in range(0, len(cells) - 1, 2):
        k = cells[i]
        if k in kv:
            k = k + "_2"
        kv[k] = cells[i + 1]
    return kv


def _row(data: dict, key: str) -> list[float | None]:
    out = []
    for x in data.get(key, []) or []:
        if x in ("", None):
            out.append(None)
        else:
            try:
                out.append(float(str(x).replace(",", "")))
            except ValueError:
                out.append(None)
    return out


def _at(arr: list, i: int):
    return arr[i] if 0 <= i < len(arr) else None


def _ema(a: np.ndarray, n: int) -> np.ndarray:
    k = 2.0 / (n + 1)
    out = np.empty_like(a, dtype=float)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1 - k)
    return out


def technical_features(ohlc: dict) -> dict:
    c = np.asarray(ohlc["close"], dtype=float)
    lo = np.asarray(ohlc["low"], dtype=float)
    v = np.asarray(ohlc["volume"], dtype=float)
    n = len(c)
    macd = _ema(c, 12) - _ema(c, 26)
    sig = _ema(macd, 9)
    c1y = c[-252:]
    peak = np.maximum.accumulate(c1y)
    mdd = float(np.max((peak - c1y) / peak))
    rets = np.log(c[n - 251:] / c[n - 252:n - 1])
    return {
        "last_close": float(c[-1]),
        "ma50": float(c[-50:].mean()),
        "ma200": float(c[-200:].mean()),
        "ma200_prev20": float(c[n - 220:n - 20].mean()),
        "macd": float(macd[-1]),
        "macd_signal": float(sig[-1]),
        "macd_hist": float(macd[-1] - sig[-1]),
        "macd_hist_prev": float(macd[-2] - sig[-2]),
        "mdd_1y": mdd,
        "rvol_1y": float(np.std(rets, ddof=1) * math.sqrt(252)),
        "support20": float(lo[-20:].min()),
        "support60": float(lo[-60:].min()),
        "vol_ratio": float(v[-1] / v[n - 21:n - 1].mean()),
        "day_chg": float(c[-1] / c[-2] - 1),
        "n_bars": int(n),
        "last_date": int(ohlc["date"][-1]) if ohlc.get("date") else None,
    }


def build_features(ticker: str, snap: dict, ia: dict, ca: dict, ba: dict, ohlc: dict) -> dict:
    """snap = {'kv': {...}, 'name', 'sector', 'industry', 'country'}; ia/ca/ba = statement 'data' dicts."""
    kv = snap["kv"]
    f: dict[str, Any] = {
        "ticker": ticker, "name": snap.get("name"), "sector": snap.get("sector"),
        "industry": snap.get("industry"), "country": snap.get("country"),
    }
    for key, label in SNAPSHOT_MAP.items():
        f[key] = parse_num(kv.get(label))
    sur = (kv.get("EPS/Sales Surpr.") or "").split()
    f["eps_surprise"] = parse_num(sur[0]) if sur else None
    f["sales_surprise"] = parse_num(sur[1]) if len(sur) > 1 else None
    f["high52_pct"] = parse_second(kv.get("52W High"))
    f["low52_pct"] = parse_second(kv.get("52W Low"))
    f["earnings"] = kv.get("Earnings") or None

    rev, ni, op = _row(ia, "Total Revenue"), _row(ia, "Net Income"), _row(ia, "Operating Income")
    ebitda, sh = _row(ia, "EBITDA"), _row(ia, "Shares Outstanding")
    fcf = _row(ca, "Free Cash Flow")
    cash = _row(ba, "Cash & Short Term Investments")
    std = _row(ba, "Short Term Debt Incl. Current Port. of LT Debt")
    ltd = _row(ba, "Long Term Debt")
    off = 1 if (ia.get("Period") or [None])[0] == "TTM" else 0
    fo = 1 if (ca.get("Period") or [None])[0] == "TTM" else 0

    def ratio(a, b):
        return a / b if (a is not None and b) else None

    f.update({
        "rev_ttm": _at(rev, 0), "ni_ttm": _at(ni, 0), "ebitda_ttm": _at(ebitda, 0), "fcf_ttm": _at(fcf, 0),
        "rev_fy0": _at(rev, off), "rev_fy3": _at(rev, off + 3),
        "opm_fy0": ratio(_at(op, off), _at(rev, off)), "opm_fy3": ratio(_at(op, off + 3), _at(rev, off + 3)),
        "fcf_fy0": _at(fcf, fo), "fcf_fy1": _at(fcf, fo + 1), "fcf_fy3": _at(fcf, fo + 3),
        "shares_fy0": _at(sh, off), "shares_fy3": _at(sh, off + 3),
        "cash": _at(cash, 0), "debt": (_at(std, 0) or 0) + (_at(ltd, 0) or 0),
        "n_fy": len([x for x in rev[off:] if x is not None]), "stmt_units": "millions",
    })
    f.update(technical_features(ohlc))
    return f
