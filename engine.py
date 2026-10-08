"""Scoring engine: 25 factors -> category scores -> total -> signal -> trade plan (Blueprint s.38-73)."""
from __future__ import annotations

import datetime as dt
import math
from statistics import pstdev, mean

from config import (CATEGORY_WEIGHTS, FACTOR_WEIGHTS, FACTOR_LABELS, SCORE_BANDS,
                     FINANCIAL_INDUSTRY_KEYWORDS, MIN_PEER_GROUP, MODEL_VERSION)
import factors as F
import valuation as V
import trade as T


# ------------------------------------------------------------------ helpers
def is_financial(f) -> bool:
    ind = (f.get("industry") or "").lower()
    return any(k in ind for k in FINANCIAL_INDUSTRY_KEYWORDS)


def band_label(score: float) -> str:
    for lo, lab in SCORE_BANDS:
        if score >= lo:
            return lab
    return "STRONG SELL"


def market_regime(bench: dict, breadth: dict | None) -> dict:
    above = bench["price"] > bench["ma200"]
    rising = bench["ma200"] > bench["ma200_prev20"]
    b = (breadth or {}).get("pct")
    pos = [above, rising] + ([b > 50] if b is not None else [])
    neg = [not above, not rising] + ([b < 40] if b is not None else [])
    regime = "BULL" if all(pos) else "BEAR" if all(neg) else "NEUTRAL"
    return {"regime": regime, "spy_above_200dma": above, "spy_200dma_rising": rising, "breadth_pct": b,
            "spy_price": bench["price"], "spy_ma200": bench["ma200"]}


def days_to_earnings(text: str | None, today: dt.date) -> int | None:
    """Finviz shows e.g. 'Oct 28 AMC' (next or most recent report). Returns signed days."""
    if not text:
        return None
    try:
        parts = text.split()
        d = dt.datetime.strptime(f"{parts[0]} {parts[1]} {today.year}", "%b %d %Y").date()
    except (ValueError, IndexError):
        return None
    if (d - today).days < -180:
        d = d.replace(year=today.year + 1)
    elif (d - today).days > 180:
        d = d.replace(year=today.year - 1)
    return (d - today).days


def _peer_vals(universe, f, key, financial_ok=True):
    same = [r.get(key) for r in universe if r.get("sector") == f.get("sector") and r.get(key) is not None]
    return same if len(same) >= MIN_PEER_GROUP else [r.get(key) for r in universe if r.get(key) is not None]


def category_score(factors: dict, cat: str) -> float | None:
    w = FACTOR_WEIGHTS[cat]
    avail = {k: factors[k].score for k in w if factors[k].score is not None}
    if not avail:
        return None
    return sum(w[k] * s for k, s in avail.items()) / sum(w[k] for k in avail)


IMPORTANT = ["price", "fwd_pe", "roe", "roic", "gross_margin", "oper_margin", "fcf_ttm", "rev_ttm",
             "ni_ttm", "ebitda_ttm", "eps_past_3y", "sales_past_3y", "eps_next_y_g", "ma200", "ma50",
             "rsi", "atr", "beta", "perf_half", "perf_year"]
IMPORTANT_FIN_SKIP = {"fcf_ttm", "ebitda_ttm", "gross_margin"}


def red_flags(f, financial: bool) -> list[dict]:
    flags = []
    s0, s3 = f.get("shares_fy0"), f.get("shares_fy3")
    if s0 and s3 and s3 > 0 and (s0 / s3) ** (1 / 3) - 1 > 0.10:
        flags.append({"flag": "Extreme dilution (>10%/yr share growth)", "severe": True})
    if not financial:
        e, debt, cash = f.get("ebitda_ttm"), f.get("debt") or 0, f.get("cash") or 0
        if e is not None and e > 0 and (debt - cash) / e > 6:
            flags.append({"flag": "Debt stress (net debt > 6x EBITDA)", "severe": True})
        if e is not None and e <= 0 and debt > cash:
            flags.append({"flag": "Negative EBITDA with net debt (solvency risk)", "severe": True})
        cr = f.get("current_ratio")
        if cr is not None and cr < 0.7:
            flags.append({"flag": f"Weak liquidity (current ratio {cr:.2f})", "severe": cr < 0.5})
    if (f.get("fcf_ttm") or 0) < 0 and (f.get("ni_ttm") or 0) < 0:
        flags.append({"flag": "Cash burn (negative FCF and net income)", "severe": False})
    if (f.get("short_float") or 0) > 20:
        flags.append({"flag": f"High short interest ({f['short_float']:.0f}% of float)", "severe": False})
    return flags


# --------------------------------------------------------------- per stock
def analyze_stock(f: dict, universe: list[dict], bench: dict, regime: dict, today: dt.date,
                  portfolio_value: float | None = None, risk_pct: float | None = None) -> dict:
    fin = is_financial(f)
    g = V.valuation_growth(f)

    med_pe = V.peer_medians(universe, "fwd_pe", f.get("sector"), MIN_PEER_GROUP, 0, 80)
    med_ev = V.peer_medians(universe, "ev_ebitda", f.get("sector"), MIN_PEER_GROUP, 0, 60)
    med_pfcf = V.peer_medians(universe, "pfcf", f.get("sector"), MIN_PEER_GROUP, 0, 100)
    mult = V.fair_multiples(f, g, med_pe, med_ev, med_pfcf)
    if fin:
        mult["pe"] = V.bank_fair_pe(g)
    fv_res = V.fair_value(f, mult, g, fin)
    fv, comps = fv_res["fair_value"], fv_res["components"]

    price = f.get("price")
    if "dcf" in comps and price:
        mos, mos_status = (comps["dcf"] - price) / comps["dcf"], "AVAILABLE"
    elif fv and price:
        mos, mos_status = (fv - price) / fv, "ESTIMATED"
    else:
        mos, mos_status = None, "MISSING"

    fx = {
        "roic": F.f_roic(f),
        "roe": F.f_roe(f),
        "gross_margin": F.f_gross_margin(f, _peer_vals(universe, f, "gross_margin")),
        "oper_margin": F.f_oper_margin(f, _peer_vals(universe, f, "oper_margin")),
        "fcf_margin": F.f_fcf_margin(f, fin),
        "debt_ebitda": F.f_debt_ebitda(f, fin),
        "fcf_conversion": F.f_fcf_conversion(f, fin),
        "revenue_growth": F.f_revenue_growth(f),
        "eps_growth": F.f_eps_growth(f),
        "fcf_growth": F.f_fcf_growth(f, fin),
        "fwd_eps_growth": F.f_fwd_eps_growth(f),
        "eps_revision": F.f_eps_revision(f),
        "fwd_pe": F.f_fwd_pe(f, mult["pe"]),
        "ev_ebitda": F.f_ev_ebitda(f, mult["ev_ebitda"], fin),
        "peg": F.f_peg(f),
        "p_fcf": F.f_p_fcf(f, _peer_vals(universe, f, "pfcf"), fin),
        "dcf_mos": F.f_dcf_mos(mos, mos_status),
        "vs_200dma": F.f_vs_200dma(f),
        "vs_50dma": F.f_vs_50dma(f),
        "rel_strength": F.f_rel_strength(f, bench),
        "rsi": F.f_rsi(f),
        "macd_trend": F.f_macd_trend(f),
        "volume": F.f_volume(f),
        "volatility": F.f_volatility(f),
        "max_drawdown": F.f_max_drawdown(f),
        "market_regime": F.f_market_regime(regime),
    }

    cats = {c: category_score(fx, c) for c in CATEGORY_WEIGHTS}
    avail_w = {c: w for c, w in CATEGORY_WEIGHTS.items() if cats[c] is not None}
    total = sum(avail_w[c] * cats[c] for c in avail_w) / sum(avail_w.values()) if avail_w else 0.0
    inv_quality = ((25 * cats["quality"] + 20 * cats["growth"]) / 45
                   if cats["quality"] is not None and cats["growth"] is not None else None)
    alt_overall = (0.4 * inv_quality + 0.3 * cats["valuation"] + 0.3 * cats["technical"]
                   if None not in (inv_quality, cats["valuation"], cats["technical"]) else None)

    # ---- data quality (Section 65)
    imp = [k for k in IMPORTANT if not (fin and k in IMPORTANT_FIN_SKIP)]
    missing = [k for k in imp if f.get(k) is None]
    missing_ratio = len(missing) / len(imp)

    # ---- trade levels
    plan = T.entry_plan(f, fv, total)
    model_entry = plan.get("entry")
    # If price already trades below the model entry, the executable entry is the market price.
    entry = min(model_entry, price) if (model_entry and price) else model_entry
    plan["exec_entry"] = entry
    target = V.target_price(f, comps, fv, g)
    stop = T.stop_loss(f, entry) if entry else None
    rr = T.risk_reward(entry, stop, target)
    dte = days_to_earnings(f.get("earnings"), today)
    flags = red_flags(f, fin)
    severe = any(x["severe"] for x in flags)

    # ---- signal (Sections 39-43, 57, 67)
    c = cats
    tech = c["technical"] or 0
    val = c["valuation"] or 0
    in_zone = bool(model_entry and price and price <= plan["zone_high"])
    above_200 = bool(price and f.get("ma200") and price > f["ma200"])
    bear = regime["regime"] == "BEAR"
    reasons = []

    deterioration = sum([
        (f.get("eps_next_y_g") is not None and f["eps_next_y_g"] < -10),
        (f.get("fcf_ttm") is not None and f.get("fcf_fy1") is not None and f["fcf_ttm"] < f["fcf_fy1"]),
        not above_200,
    ])
    breakdown = ((f.get("rev_ttm") and f.get("rev_fy0") and f["rev_ttm"] < 0.9 * f["rev_fy0"])
                 and (f.get("fcf_ttm") or 0) < 0 and not above_200)

    if missing_ratio > 0.20:
        signal = "DATA INSUFFICIENT"
        reasons.append(f"{len(missing)} of {len(imp)} key inputs missing")
    elif total < 30 or breakdown:
        signal = "STRONG SELL"
    elif total < 45 or deterioration == 3:
        signal = "SELL"
        if deterioration == 3 and total >= 45:
            reasons.append("EPS estimates falling, FCF deteriorating, price < 200-DMA")
    elif severe:
        signal = "AVOID (RED FLAG)"
        reasons.append("severe red flag blocks any buy")
    else:
        strong = (total >= 85 and (c["quality"] or 0) >= 70 and (c["growth"] or 0) >= 70 and val >= 70
                  and tech >= 70 and (c["risk"] or 0) >= 50 and rr is not None and rr >= 2.5 and in_zone
                  and fv and price <= 0.9 * fv and above_200 and not bear)
        buy = (total >= 75 and (c["quality"] or 0) >= 65 and (c["growth"] or 0) >= 65 and val >= 60
               and tech >= 60 and (c["risk"] or 0) >= 45 and rr is not None and rr >= 2.0 and in_zone
               and (not bear or total >= 85))
        accum = (total >= 65 and val >= 50 and tech >= 55 and rr is not None and rr >= 1.5 and in_zone
                 and not bear)
        if strong:
            signal = "STRONG BUY"
        elif buy:
            signal = "BUY"
        elif accum:
            signal = "BUY / ACCUMULATE"
        elif total >= 55:
            signal = "HOLD"
        else:
            signal = "WEAK HOLD"
        if signal in ("HOLD", "WEAK HOLD") and total >= 65:
            why = []
            if not in_zone and model_entry:
                why.append(f"price above buy zone (wait for ≤ ${plan['zone_high']:.2f})")
            if rr is None or rr < 2.0:
                why.append(f"risk/reward {rr:.2f} < 2.0" if rr is not None else "risk/reward unavailable")
            if val < 60:
                why.append(f"valuation score {val:.0f} < 60")
            if tech < 60:
                why.append(f"technical score {tech:.0f} < 60")
            if bear:
                why.append("bear market gate")
            reasons += why

    pos = T.position_size(f, signal, entry, stop, regime["regime"], dte, portfolio_value, risk_pct)

    # ---- confidence (Section 64)
    completeness = 100 * (1 - missing_ratio)
    coverage = 100 if (f.get("target_price") and f.get("recom") and f.get("eps_next_y_g") is not None) else 40
    history = min(100, 25 * (f.get("n_fy") or 0)) if (f.get("n_bars") or 0) >= 252 else 40
    vals_ = [v for v in comps.values()]
    if len(vals_) >= 2 and mean(vals_) > 0:
        cv = pstdev(vals_) / mean(vals_)
        val_agree = max(0, 100 - 150 * cv)
    else:
        val_agree = 40
    tech_keys = ["vs_200dma", "vs_50dma", "rel_strength", "macd_trend"]
    tsc = [fx[k].score for k in tech_keys if fx[k].score is not None]
    bull = sum(1 for s in tsc if s >= 60)
    tech_agree = 100 * max(bull, len(tsc) - bull) / len(tsc) if tsc else 40
    est_pen = sum(1 for v in fx.values() if v.status == "ESTIMATED")
    confidence = (0.25 * completeness + 0.20 * coverage + 0.20 * history + 0.20 * val_agree
                  + 0.15 * tech_agree) - 2 * est_pen
    confidence = max(0, min(100, confidence))

    # ---- explainability (Section 73)
    positives, risks = [], []
    ranked = sorted(((k, v) for k, v in fx.items() if v.score is not None and k != "market_regime"),
                    key=lambda kv: -kv[1].score * _w(kv[0]))
    for k, v in ranked:
        if v.score >= 80 and len(positives) < 6:
            positives.append(_explain(k, v, True))
    for k, v in sorted(ranked, key=lambda kv: kv[1].score):
        if v.score <= 35 and len(risks) < 5:
            risks.append(_explain(k, v, False))
    if T.earnings_soon(dte):
        risks.append(f"Earnings in {dte} day(s) — position limited to 25%")
    if fv and price and price > fv * 1.15:
        risks.append(f"Price {((price / fv) - 1) * 100:.0f}% above blended fair value")
    if (f.get("rvol_1y") or 0) > 0.45:
        risks.append(f"High realised volatility ({f['rvol_1y'] * 100:.0f}% annualised)")
    for fl in flags:
        risks.append(("⛔ " if fl["severe"] else "") + fl["flag"])
    if regime["regime"] != "BULL":
        risks.append(f"Market regime {regime['regime']}")

    return {
        "ticker": f["ticker"], "name": f.get("name"), "sector": f.get("sector"), "industry": f.get("industry"),
        "price": price, "model_version": MODEL_VERSION, "signal_date": today.isoformat(),
        "financial_model": fin,
        "score": round(total, 1), "band": band_label(total), "signal": signal, "signal_notes": reasons,
        "categories": {k: (round(v, 1) if v is not None else None) for k, v in cats.items()},
        "investment_quality": round(inv_quality, 1) if inv_quality is not None else None,
        "overall_3score": round(alt_overall, 1) if alt_overall is not None else None,
        "factors": {k: {**v.to_dict(), "label": FACTOR_LABELS[k], "weight": _w(k)} for k, v in fx.items()},
        "valuation": {"growth_used": g, "wacc": V.wacc(f), "fair_multiples": mult,
                      "components": comps, "fair_value": fv, "mos_vs_dcf": mos},
        "trade": {**plan, "target": target, "stop": stop, "risk_reward": rr,
                  "upside_to_target": (target / price - 1) if target and price else None,
                  "trailing": T.trailing_plan(entry, stop, f.get("atr")),
                  "position": pos, "days_to_earnings": dte, "earnings": f.get("earnings"),
                  "in_buy_zone": in_zone},
        "confidence": round(confidence, 0),
        "data_quality": {"missing": missing, "missing_ratio": missing_ratio,
                         "estimated": [k for k, v in fx.items() if v.status == "ESTIMATED"],
                         "na": [k for k, v in fx.items() if v.status == "N/A"]},
        "red_flags": flags, "positives": positives, "risks": risks,
        "metrics": {k: f.get(k) for k in ("market_cap", "pe", "fwd_pe", "peg", "pfcf", "ev_ebitda", "roe",
                                          "roic", "gross_margin", "oper_margin", "eps_next_y_g", "eps_next_5y",
                                          "sales_past_3y", "eps_past_3y", "rsi", "beta", "atr", "sma50",
                                          "sma200", "perf_half", "perf_year", "target_price", "recom",
                                          "rvol_1y", "mdd_1y", "ma50", "ma200", "support20")},
    }


def _w(k):
    for cat, ws in FACTOR_WEIGHTS.items():
        if k in ws:
            return ws[k]
    return 0


def _explain(k, v, good: bool) -> str:
    lab = FACTOR_LABELS[k]
    raw = v.raw
    unit = {"roic": "%", "roe": "%", "gross_margin": "%", "oper_margin": "%", "fcf_margin": "%",
            "fcf_conversion": "%", "revenue_growth": "%/yr", "eps_growth": "%/yr", "fcf_growth": "%/yr",
            "fwd_eps_growth": "%", "eps_revision": "%", "dcf_mos": "%", "vs_200dma": "%", "vs_50dma": "%",
            "rel_strength": " pts", "volatility": "% vol", "max_drawdown": "%"}.get(k, "")
    rv = f" ({raw:.1f}{unit})" if isinstance(raw, (int, float)) and math.isfinite(raw) else ""
    if v.note and k in ("fwd_pe", "ev_ebitda", "p_fcf", "gross_margin", "oper_margin"):
        rv = f" ({v.note})"
    return f"{'Strong' if good else 'Weak'} {lab.lower()}{rv}"


# ---------------------------------------------------------------- universe
def analyze_universe(data: dict, today: dt.date | None = None, portfolio_value=None, risk_pct=None) -> dict:
    """data = output of FinvizClient.universe_features()."""
    res = data["results"]
    bench_t = data.get("benchmark", "SPY")
    bench = res[bench_t]
    regime = market_regime(bench, data.get("breadth"))
    today = today or dt.date.today()
    stocks = [f for t, f in res.items() if t != bench_t]
    out = [analyze_stock(f, stocks, bench, regime, today, portfolio_value, risk_pct) for f in stocks]
    order = {"STRONG BUY": 0, "BUY": 1, "BUY / ACCUMULATE": 2}
    out.sort(key=lambda r: (order.get(r["signal"], 9), -r["score"]))
    return {"asof": data.get("asof"), "model_version": MODEL_VERSION, "regime": regime,
            "benchmark": {k: bench.get(k) for k in ("price", "perf_half", "perf_year", "sma200", "ma200")},
            "breadth": data.get("breadth"), "stocks": out}
