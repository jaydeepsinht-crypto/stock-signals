"""The 25 factor scoring functions (Blueprint sections 10-37).

Every function returns ``Factor(score 0-100 | None, raw value, status, note)``.
status is one of AVAILABLE / ESTIMATED / MISSING / N/A  (Section 65: missing data never becomes 0).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from bisect import bisect_right


@dataclass
class Factor:
    score: float | None
    raw: float | None = None
    status: str = "AVAILABLE"
    note: str = ""

    def to_dict(self):
        return asdict(self)


MISSING = lambda note="": Factor(None, None, "MISSING", note)  # noqa: E731
NA = lambda note="": Factor(None, None, "N/A", note)          # noqa: E731


def clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def band(x: float, edges: list[float], scores: list[float]) -> float:
    """Piecewise table. edges ascending; len(scores) == len(edges)+1. x < edges[0] -> scores[0]."""
    return scores[bisect_right(edges, x)]


def pct_rank(x: float, peers: list[float]) -> float:
    """Percentile (0-1) of x among peers (mid-rank for ties)."""
    vals = [p for p in peers if p is not None]
    if not vals:
        return 0.5
    below = sum(1 for p in vals if p < x)
    equal = sum(1 for p in vals if p == x)
    return (below + 0.5 * equal) / len(vals)


def _ok(*xs):
    return all(x is not None for x in xs)


# ============================================================ QUALITY (25)
def f_roic(f):
    x = f.get("roic")
    if x is None:
        return MISSING()
    return Factor(clip(50 + 5 * (x - 10)), x)


def f_roe(f):
    x, de = f.get("roe"), f.get("debt_eq")
    if x is None:
        return MISSING()
    s = band(x, [5, 10, 15, 20], [20, 40, 60, 80, 100])
    note = ""
    if de is not None and (de > 3 or de < 0):
        s, note = min(s, 60), "capped: Debt/Equity > 3 (leverage-inflated ROE)"
    elif de is None and x > 50:
        s, note = min(s, 60), "capped: equity near zero/negative (ROE not meaningful)"
    return Factor(s, x, note=note)


def f_gross_margin(f, peers):
    x = f.get("gross_margin")
    if x is None:
        return MISSING()
    p = pct_rank(x, peers)
    return Factor(clip(25 + 75 * p), x, note=f"{p * 100:.0f}th pct vs peers")


def f_oper_margin(f, peers):
    x = f.get("oper_margin")
    if x is None:
        return MISSING()
    p = pct_rank(x, peers)
    s = 25 + 75 * p
    bonus = 0
    if _ok(f.get("opm_fy0"), f.get("opm_fy3")):
        bps = (f["opm_fy0"] - f["opm_fy3"]) * 10_000
        bonus = 10 if bps > 300 else 5 if bps > 100 else 0 if bps >= 0 else -10
    return Factor(clip(s + bonus), x, note=f"{p * 100:.0f}th pct, trend bonus {bonus:+d}")


def f_fcf_margin(f, financial):
    if financial:
        return NA("disabled for financials")
    fcf, rev = f.get("fcf_ttm"), f.get("rev_ttm")
    if not _ok(fcf, rev) or rev <= 0:
        return MISSING()
    x = fcf / rev * 100
    return Factor(band(x, [0, 5, 10, 20], [20, 40, 60, 80, 100]), x)


def f_debt_ebitda(f, financial):
    if financial:
        return NA("disabled for financials")
    e, debt, cash = f.get("ebitda_ttm"), f.get("debt"), f.get("cash")
    if not _ok(e, debt, cash):
        return MISSING()
    if e <= 0:
        return Factor(10, None, note="negative EBITDA")
    x = (debt - cash) / e
    return Factor(band(x, [0, 1, 2, 3, 4, 5], [100, 95, 85, 70, 50, 30, 10]), x)


def f_fcf_conversion(f, financial):
    if financial:
        return NA("disabled for financials")
    fcf, ni = f.get("fcf_ttm"), f.get("ni_ttm")
    if not _ok(fcf, ni):
        return MISSING()
    if ni <= 0:
        return NA("net income negative")
    x = fcf / ni * 100
    return Factor(band(x, [50, 75, 100, 125], [25, 50, 70, 85, 100]), x)


# ============================================================= GROWTH (20)
_G_EDGES, _G_SCORES = [0, 5, 10, 20], [20, 40, 60, 80, 100]


def f_revenue_growth(f):
    x = f.get("sales_past_3y")
    if x is None:
        if _ok(f.get("rev_fy0"), f.get("rev_fy3")) and f["rev_fy3"] > 0 and f["rev_fy0"] > 0:
            x = ((f["rev_fy0"] / f["rev_fy3"]) ** (1 / 3) - 1) * 100
        else:
            return MISSING()
    return Factor(band(x, _G_EDGES, _G_SCORES), x)


def f_eps_growth(f):
    x = f.get("eps_past_3y")
    if x is None:
        return MISSING()
    return Factor(band(x, _G_EDGES, _G_SCORES), x)


def f_fcf_growth(f, financial):
    if financial:
        return NA("disabled for financials")
    a, b = f.get("fcf_fy0"), f.get("fcf_fy3")
    if not _ok(a, b):
        return MISSING()
    if a <= 0:
        return Factor(20, None, note="latest FCF negative")
    if b <= 0:
        return Factor(80, None, "ESTIMATED", "FCF turned positive (CAGR undefined)")
    x = ((a / b) ** (1 / 3) - 1) * 100
    return Factor(band(x, _G_EDGES, _G_SCORES), x)


def f_fwd_eps_growth(f):
    x = f.get("eps_next_y_g")
    has_coverage = f.get("target_price") is not None and f.get("recom") is not None
    if x is None:
        hx = f.get("eps_past_3y")
        if hx is None:
            return MISSING()
        return Factor(band(hx, _G_EDGES, _G_SCORES), hx, "ESTIMATED", "no consensus: historical EPS growth used")
    return Factor(band(x, _G_EDGES, _G_SCORES), x, "AVAILABLE" if has_coverage else "ESTIMATED")


def f_eps_revision(f):
    """Finviz does not publish estimate-revision history. Proxy: last-quarter EPS surprise (halved),
    flagged ESTIMATED. Replace with real 30D/90D revision data when available."""
    if f.get("eps_rev_30d") is not None:
        x = 0.6 * f["eps_rev_30d"] + 0.4 * (f.get("eps_rev_90d") or f["eps_rev_30d"])
        status = "AVAILABLE"
    elif f.get("eps_surprise") is not None:
        x = f["eps_surprise"] / 2
        status = "ESTIMATED"
    else:
        return MISSING()
    return Factor(band(x, [-10, -5, 0, 5, 10], [10, 25, 45, 65, 85, 100]), x, status,
                  "proxy = EPS surprise / 2" if status == "ESTIMATED" else "")


# ========================================================== VALUATION (20)
_REL_EDGES, _REL_SCORES = [0.70, 0.85, 1.00, 1.15, 1.35], [100, 85, 70, 50, 30, 10]


def f_fwd_pe(f, fair_pe):
    x = f.get("fwd_pe")
    if x is None or fair_pe is None:
        if f.get("fwd_eps") is not None and f["fwd_eps"] <= 0:
            return Factor(10, None, note="negative forward EPS")
        return MISSING()
    r = x / fair_pe
    return Factor(band(r, _REL_EDGES, _REL_SCORES), x, note=f"{r * 100:.0f}% of fair P/E {fair_pe:.1f}")


def f_ev_ebitda(f, fair_mult, financial):
    if financial:
        return NA("disabled for financials")
    x = f.get("ev_ebitda")
    if x is None or fair_mult is None:
        return MISSING()
    if x <= 0:
        return Factor(10, x, note="negative EBITDA")
    r = x / fair_mult
    return Factor(band(r, _REL_EDGES, _REL_SCORES), x, note=f"discount {(1 - r) * 100:+.0f}% vs fair {fair_mult:.1f}x")


def f_peg(f):
    x = f.get("peg")
    if x is None:
        g = f.get("eps_next_5y")
        if g is not None and g <= 0:
            return Factor(20, None, note="no growth expected")
        return MISSING()
    if x <= 0:
        return Factor(20, x)
    return Factor(band(x, [0.75, 1.0, 1.25, 1.5, 2.0], [100, 90, 75, 60, 40, 20]), x)


def f_p_fcf(f, peers, financial):
    if financial:
        return NA("disabled for financials")
    x = f.get("pfcf")
    if x is None:
        if f.get("fcf_ttm") is not None and f["fcf_ttm"] <= 0:
            return Factor(10, None, note="negative FCF")
        return MISSING()
    p = pct_rank(x, peers)
    return Factor(clip(100 - 80 * p), x, note=f"{p * 100:.0f}th pct (lower is cheaper)")


def f_dcf_mos(mos, status):
    if mos is None:
        return MISSING("DCF unavailable")
    x = mos * 100
    return Factor(band(x, [-20, -10, 0, 10, 20, 30], [10, 25, 40, 60, 75, 90, 100]), x, status)


# ========================================================== TECHNICAL (20)
def _dist(price, ma):
    return (price / ma - 1) * 100 if _ok(price, ma) and ma else None


def f_vs_200dma(f):
    x = f.get("sma200")
    if x is None:
        x = _dist(f.get("price"), f.get("ma200"))
    if x is None:
        return MISSING()
    return Factor(band(x, [-20, -10, 0, 5, 15], [20, 35, 50, 70, 90, 80]), x)


def f_vs_50dma(f):
    x = f.get("sma50")
    if x is None:
        x = _dist(f.get("price"), f.get("ma50"))
    if x is None:
        return MISSING()
    return Factor(band(x, [-10, -5, 0, 5, 10], [20, 35, 50, 75, 90, 75]), x)


def f_rel_strength(f, bench):
    if not _ok(f.get("perf_half"), f.get("perf_year"), bench.get("perf_half"), bench.get("perf_year")):
        return MISSING()
    x = 0.5 * (f["perf_half"] - bench["perf_half"]) + 0.5 * (f["perf_year"] - bench["perf_year"])
    return Factor(band(x, [-20, -10, 0, 10, 20], [10, 30, 50, 70, 85, 100]), x)


def f_rsi(f):
    x = f.get("rsi")
    if x is None:
        return MISSING()
    return Factor(band(x, [25, 35, 50, 65, 75], [35, 65, 85, 100, 75, 40]), x)


def f_macd_trend(f):
    keys = ("macd", "macd_signal", "macd_hist", "macd_hist_prev", "ma50", "ma200", "ma200_prev20", "price")
    if not _ok(*(f.get(k) for k in keys)):
        return MISSING()
    pts = 0
    pts += 25 if f["macd"] > f["macd_signal"] else 0
    pts += 20 if f["macd_hist"] > f["macd_hist_prev"] else 0
    pts += 20 if f["price"] > f["ma50"] else 0
    pts += 25 if f["ma50"] > f["ma200"] else 0
    pts += 10 if f["ma200"] > f["ma200_prev20"] else 0
    return Factor(pts, pts)


def f_volume(f):
    x = f.get("vol_ratio")
    if x is None:
        x = f.get("rel_volume")
    if x is None:
        return MISSING()
    up = (f.get("day_chg") or 0) >= 0
    if up:
        s = band(x, [1.0, 1.2, 1.5, 2.0], [45, 60, 75, 90, 100])
    else:  # breakdown: heavy volume on a down day is negative
        s = band(x, [1.0, 1.2, 1.5, 2.0], [60, 50, 40, 30, 20])
    return Factor(s, x, note="up day" if up else "down day (reversed)")


# =============================================================== RISK (15)
def f_volatility(f):
    rv, beta = f.get("rvol_1y"), f.get("beta")
    if rv is None and beta is None:
        return MISSING()
    parts = []
    if rv is not None:
        parts.append(band(rv * 100, [20, 28, 36, 50, 70], [100, 85, 70, 45, 20, 5]))
    if beta is not None:
        parts.append(band(beta, [0.8, 1.0, 1.3, 1.7, 2.2], [100, 85, 70, 45, 20, 5]))
    return Factor(sum(parts) / len(parts), rv * 100 if rv is not None else None,
                  note=f"beta {beta}" if beta is not None else "")


def f_max_drawdown(f):
    x = f.get("mdd_1y")
    if x is None:
        return MISSING()
    return Factor(band(x * 100, [10, 20, 30, 40, 50], [100, 85, 70, 50, 30, 10]), x * 100)


def f_market_regime(regime: dict):
    return Factor({"BULL": 100, "NEUTRAL": 60, "BEAR": 20}[regime["regime"]], None, note=regime["regime"])
