"""Fair value, DCF and target price (Blueprint sections 26-27, 44, 48-49)."""
from __future__ import annotations

from statistics import median

from config import VAL


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def valuation_growth(f) -> float | None:
    """Growth rate (%) used for valuation: 60% long-term (EPS next 5Y) + 40% next-year, capped."""
    g5, g1 = f.get("eps_next_5y"), f.get("eps_next_y_g")
    if g5 is None and g1 is None:
        g = f.get("eps_past_3y")
        if g is None:
            return None
    elif g5 is None:
        g = g1
    elif g1 is None:
        g = g5
    else:
        g = 0.6 * g5 + 0.4 * g1
    return _clip(g, -10.0, VAL["growth_cap"])


def wacc(f) -> float:
    beta = f.get("beta") if f.get("beta") is not None else 1.0
    return _clip(VAL["risk_free"] + beta * VAL["equity_risk_premium"], VAL["wacc_min"], VAL["wacc_max"])


def dcf_per_share(f, g_pct: float | None) -> float | None:
    """10-year two-stage DCF on TTM free cash flow.
    Years 1-5 grow at g (capped), years 6-10 fade linearly to terminal growth."""
    fcf, shares = f.get("fcf_ttm"), f.get("shs_out")
    if fcf is None or fcf <= 0 or not shares or g_pct is None:
        return None
    r, tg = wacc(f), VAL["terminal_growth"]
    g1 = _clip(g_pct * VAL["dcf_growth_haircut"], 0.0, VAL["dcf_growth_cap"]) / 100
    pv, cf = 0.0, fcf
    for t in range(1, 11):
        g = g1 if t <= 5 else g1 + (tg - g1) * (t - 5) / 5
        cf *= 1 + g
        pv += cf / (1 + r) ** t
    tv = cf * (1 + tg) / (r - tg)
    ev = pv + tv / (1 + r) ** 10
    net_debt = (f.get("debt") or 0) - (f.get("cash") or 0)
    equity = ev - net_debt                    # millions
    return equity * 1e6 / shares if equity > 0 else None


def peer_medians(universe: list[dict], key: str, sector: str | None, min_n: int, lo: float, hi: float):
    def vals(rows):
        return [r[key] for r in rows if r.get(key) is not None and lo < r[key] < hi]
    same = vals([r for r in universe if r.get("sector") == sector])
    pool = same if len(same) >= min_n else vals(universe)
    return median(pool) if pool else None


def fair_multiples(f, g, med_pe, med_ev, med_pfcf) -> dict:
    gp = max(g or 0.0, 0.0)
    def blend(growth_fair, med, lo, hi):
        v = growth_fair if med is None else 0.5 * growth_fair + 0.5 * med
        return _clip(v, lo, hi)
    return {
        "pe": blend(12 + 0.9 * gp, med_pe, 10, 45),
        "ev_ebitda": blend(7 + 0.5 * gp, med_ev, 6, 35),
        "p_fcf": blend(12 + 0.8 * gp, med_pfcf, 8, 50),
    }


def bank_fair_pe(g) -> float:
    return _clip(9 + 0.4 * max(g or 0.0, 0.0), 8, 16)


def justified_pb_value(f, g) -> float | None:
    """Banks/insurers (Blueprint s.66): P/B_fair = (ROE - g) / (r - g); book/share = EPS / ROE."""
    roe, eps = f.get("roe"), f.get("eps_ttm")
    if not roe or roe <= 0 or not eps or eps <= 0:
        return None
    r, gl = wacc(f), min(max(g or 0.0, 0.0) / 100, 0.05)
    book = eps / (roe / 100)
    pb = _clip((roe / 100 - gl) / (r - gl), 0.5, 4.0)
    return pb * book


def fair_value(f, mult: dict, g, financial: bool) -> dict:
    """Per-share value from each method, winsorised to [0.25x, 4x] price, then weighted (Sec. 44)."""
    price, shares = f.get("price"), f.get("shs_out")
    comps: dict[str, float] = {}
    if not financial:
        d = dcf_per_share(f, g)
        if d:
            comps["dcf"] = d
    else:
        pbv = justified_pb_value(f, g)
        if pbv:
            comps["pb"] = pbv
    eps = f.get("fwd_eps")
    if eps and eps > 0:
        comps["pe"] = (bank_fair_pe(g) if financial else mult["pe"]) * eps
        if g is not None:
            peg_pe = _clip(g, 5, VAL["growth_cap"])
            comps["peg"] = eps * (min(peg_pe, bank_fair_pe(g)) if financial else peg_pe)  # fair at PEG = 1
    if not financial and shares:
        e = f.get("ebitda_ttm")
        if e and e > 0:
            nd = (f.get("debt") or 0) - (f.get("cash") or 0)
            v = (mult["ev_ebitda"] * e - nd) * 1e6 / shares
            if v > 0:
                comps["ev_ebitda"] = v
        fcf = f.get("fcf_ttm")
        if fcf and fcf > 0:
            comps["p_fcf"] = mult["p_fcf"] * fcf * 1e6 / shares
    if price:
        lo, hi = VAL["component_clip"]
        comps = {k: _clip(v, lo * price, hi * price) for k, v in comps.items()}
    w = VAL["fv_weights"]
    tot_w = sum(w[k] for k in comps)
    fv = sum(w[k] * v for k, v in comps.items()) / tot_w if tot_w else None
    return {"fair_value": fv, "components": comps, "coverage": tot_w}


def target_price(f, comps: dict, fv: float | None, g) -> float | None:
    if fv is None:
        return None
    parts = {}
    if "dcf" in comps:
        parts["dcf"] = comps["dcf"] * (1 + wacc(f))   # value rolled forward 12 months
    if "pb" in comps:
        parts["dcf"] = comps["pb"] * (1 + wacc(f))
    if "pe" in comps:
        parts["pe"] = comps["pe"]
    if "ev_ebitda" in comps:
        parts["ev_ebitda"] = comps["ev_ebitda"]
    if f.get("target_price"):
        parts["analyst"] = f["target_price"]
    if not parts:
        return fv
    w = VAL["target_weights"]
    t = sum(w[k] * v for k, v in parts.items()) / sum(w[k] for k in parts)
    cap = VAL["target_cap_high_growth"] if (g or 0) > 20 else VAL["target_cap"]
    return min(t, cap * fv)
